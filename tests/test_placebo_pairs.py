"""Placebo block of analyze() must use placebo pairs only (bug: rename-then-filter pooled proxy + placebo pairs for 'name'/'zip')."""
import numpy as np, pandas as pd
from llmbias.analysis import pair_table, placebo_pairs, m3, KEYS
from llmbias.build_data import PAIRS

VARIANTS = ["base", "control_dup", "name_b", "name_w_placebo", "name_b_placebo", "zip_b", "zip_placebo", "anchor"]
MODELS = [("openai", "gpt-5.4-mini"), ("anthropic", "claude-haiku-5-5"), ("google", "gemini-3.8-flash")]

def _maj(dec, n_prof=25, drop=()):
    """Pilot-shaped majority table: 3 vendors x setup 1 x n_prof profiles x 8 variants. dec(vendor, profile_idx, variant) -> decision."""
    rows = []
    for v, m in MODELS:
        for i in range(n_prof):
            for var in VARIANTS:
                d = None if (v, i, var) in drop else dec(v, i, var)
                rows.append({"vendor": v, "model": m, "effort": "none", "setup": 1, "profile_id": f"P{i:03d}", "variant": var, "maj": d})
    return pd.DataFrame(rows)

def test_placebo_pairs_drops_real_proxy_rows():
    pairs = pair_table(_maj(lambda v, i, var: "deny"))
    t = placebo_pairs(pairs)
    assert set(t.pair_type) == {"proxy", "control"}
    assert set(t[t.pair_type == "proxy"].vb) == {"name_w_placebo", "name_b_placebo", "zip_placebo"}   # no name_b / zip_b
    assert not ((t.va == "base") & t.vb.isin(["name_b", "zip_b"])).any()
    assert len(t[t.pair_type == "control"]) == len(pairs[pairs.pair_type == "control"])

def test_placebo_n_pairs_pilot_shaped():
    # 25 profiles x 3 vendors = 75 pairs per placebo cell; one missing majority each for name and zip -> 74 / 74, like the pilot (74 / 75)
    pairs = pair_table(_maj(lambda v, i, var: "deny", drop={("openai", 3, "name_w_placebo"), ("google", 7, "zip_placebo")}))
    t = placebo_pairs(pairs)
    n = {p: m3(t, p)["n_pairs"] for p in ("name", "name_black", "zip")}
    assert n == {"name": 74, "name_black": 75, "zip": 74}
    assert all(70 <= x <= 75 for x in n.values())

def test_synthetic_pooling_would_be_caught():
    # every proxy pair flips, no placebo or control pair flips: the placebo flip rate must be exactly 0
    proxy_flip = lambda v, i, var: "approve" if var in ("name_b", "zip_b") else "deny"
    pairs = pair_table(_maj(proxy_flip))
    # name_black placebo is name_b vs name_b_placebo -> flips by construction here; check only name and zip
    for p in ("name", "zip"):
        r = m3(placebo_pairs(pairs), p)
        assert r["n_pairs"] == 75 and r["flips"] == 0 and r["flip_rate"] == 0.0
        # the old rename-then-filter expression pooled the 75 proxy pairs in: 150 pairs, 75 flips
        old = pairs.assign(pair_type=pairs.pair_type.replace({"placebo": "proxy"})).query("pair_type=='proxy' or pair_type=='control'")
        ro = m3(old, p); assert ro["n_pairs"] == 150 and ro["flips"] == 75
    # the real proxy M3 is unchanged by the helper
    assert m3(pairs, "name")["flips"] == 75 and m3(pairs, "name")["n_pairs"] == 75

def test_analyze_uses_placebo_pairs(monkeypatch):
    import inspect, llmbias.analysis as A
    src = inspect.getsource(A.analyze)
    assert 'R["placebo"] = {f"{p}": m3(placebo_pairs(pairs), p)' in src
