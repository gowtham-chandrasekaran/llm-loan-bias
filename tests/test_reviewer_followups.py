"""Reviewer follow-ups (Oct 20 2026):
(a) cross_name_vs_zip_M3 uses placebo_pairs-style filtering (drop non-cross rows first, then relabel);
(b) load_calls(legacy_pilot_v1=True) reproduces runs/20261012T143812-ff54324d-4745d6-batch/analysis_placebo_fix.json."""
import json, math, os, shutil, inspect
import pandas as pd, pytest
from llmbias import analysis as A
from llmbias.analysis import pair_table, cross_pairs, m3
from test_placebo_pairs import _maj

ROOT = os.path.join(os.path.dirname(__file__), "..")
PILOT = os.path.join(ROOT, "runs", "20261012T143812-ff54324d-4745d6-batch")
EXPECTED = os.path.join(PILOT, "analysis_placebo_fix.json")


# ---- (a) cross pairs ------------------------------------------------------------------------------------------------
def test_cross_pairs_keeps_only_cross_and_control():
    pairs = pair_table(_maj(lambda v, i, var: "deny"))
    t = cross_pairs(pairs)
    assert set(t.pair_type) == {"proxy", "control"}
    pr = t[t.pair_type == "proxy"]
    assert set(pr.proxy) == {"x"} and set(zip(pr.va, pr.vb)) == {("name_b", "zip_b")}
    assert len(t[t.pair_type == "control"]) == len(pairs[pairs.pair_type == "control"])
    assert not t.vb.isin(["name_w_placebo", "name_b_placebo", "zip_placebo"]).any()


def test_cross_m3_ignores_proxy_and_placebo_flips():
    # every proxy and placebo pair flips, cross and control never do: the cross M3 must see 0 flips on 75 pairs
    flip = lambda v, i, var: "approve" if var in ("name_w_placebo", "zip_placebo") else "deny"
    pairs = pair_table(_maj(flip))
    r = m3(cross_pairs(pairs), "x")
    assert r["n_pairs"] == 75 and r["flips"] == 0 and r["control_flip_rate"] == 0.0
    # cross flips are counted: name_b denied, zip_b approved
    pairs2 = pair_table(_maj(lambda v, i, var: "approve" if var == "zip_b" else "deny"))
    assert m3(cross_pairs(pairs2), "x")["flips"] == 75


def test_cross_pairs_robust_to_label_collision():
    # if a non-cross row ever carried proxy 'x' it must not be pooled (rename-then-filter would have pooled it)
    pairs = pair_table(_maj(lambda v, i, var: "deny"))
    bad = pairs.assign(proxy=pairs.proxy.where(pairs.pair_type != "proxy", "x"))
    assert m3(cross_pairs(bad), "x")["n_pairs"] == 75


def test_analyze_uses_cross_pairs():
    assert 'R["cross_name_vs_zip_M3"] = m3(cross_pairs(pairs), "x")' in inspect.getsource(A.analyze)


# ---- (b) legacy pilot-v1 path ---------------------------------------------------------------------------------------
def test_legacy_load_calls_823eafc_validity(tmp_path):
    recs = [{"call_id": "a", "effort": None, "parsed": {"decision": "deny", "reason_codes": ["R1"], "parse_ok": True}},
            {"call_id": "b", "effort": "low", "parsed": {"decision": "approve"}},
            {"call_id": "c", "effort": None, "parsed": None}]
    (tmp_path / "calls.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    df = A.load_calls(str(tmp_path), legacy_pilot_v1=True)
    assert list(df.decision[:2]) == ["deny", "approve"] and pd.isna(df.decision[2])
    assert list(df.codes) == [["R1"], [], []]
    assert list(df.effort) == ["none", "low", "none"] and df.exclusion_reason.isna().all()


# fit_vb() in statsmodels draws its random-effect start values from an unseeded RNG (BayesMixedGLM._get_start), so the
# VB sensitivity blocks differ in the ~6th significant digit between any two runs, including the two committed pilot files.
# They are compared with a tolerance; every other key must match exactly.
VB_KEYS = {"mixed_logit_vb_sensitivity_name", "mixed_logit_vb_sensitivity_zip"}


def _close(a, b, rtol=1e-4, atol=1e-5):
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=rtol, abs_tol=atol)
    return a == b


@pytest.mark.skipif(not os.path.exists(EXPECTED), reason="pilot-v1 analysis_placebo_fix.json not present")
@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript (lme4) needed for the glmer blocks")
def test_legacy_reproduces_pilot_v1_placebo_fix(tmp_path):
    for f in ("calls.jsonl", "judgments.jsonl", "run_meta.json"):   # tracked read-only copies under raw/
        shutil.copy(os.path.join(PILOT, "raw", f), tmp_path / f)
    out = tmp_path / "out.json"
    A.analyze(str(tmp_path), legacy_pilot_v1=True, out=str(out))
    got, exp = json.loads(out.read_text()), json.load(open(EXPECTED))
    assert set(got) == set(exp)
    for k in exp:
        if k in VB_KEYS:
            assert _close(exp[k], got[k]), k
        else:
            assert got[k] == exp[k], k
    # headline placebo numbers (pilot_observations D3)
    assert (got["placebo"]["name"]["flips"], got["placebo"]["name"]["n_pairs"]) == (8, 74)
    assert (got["placebo"]["zip"]["flips"], got["placebo"]["zip"]["n_pairs"]) == (11, 75)
    assert (got["placebo"]["name_black"]["flips"], got["placebo"]["name_black"]["n_pairs"]) == (9, 75)
