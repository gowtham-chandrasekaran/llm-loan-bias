import json, os, yaml, pytest
from llmbias.runner import run
from llmbias.judge import run_judge
from llmbias.analysis import analyze
from llmbias import labels as LB
from llmbias.build_data import P

def _cfg(tmp_path, **over):
    cfg = yaml.safe_load(open(P("configs", "pilot_mock.yaml"))); cfg.update(over)
    p = tmp_path / "c.yaml"; yaml.safe_dump(cfg, open(p, "w")); return str(p)

def test_full_mock_dry_run(tmp_path):
    rd = run(_cfg(tmp_path), out_root=str(tmp_path), limit=6)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert len(calls) == 6 * 8 * 3 * 3       # profiles x variants x repeats x setups
    assert all(c["excluded_from_confirmatory"] for c in calls)
    assert all(c["parsed"]["parse_ok"] for c in calls)
    s2 = [c for c in calls if c["setup"] == 2]; assert all(c["tool_called"] in (True, False) for c in s2)
    assert all(c["tool_called"] and len(c["tool_calls"]) == 1 for c in s2)   # 15.9.1: round-1 tool call forced (was optional)
    assert all(c["s2_compliance"] is None and c["round1_tool_choice"] == "forced:score_application" for c in s2)
    s3 = [c for c in calls if c["setup"] == 3]; assert all(c["parsed"]["decision"] == c["tool_decision"] for c in s3)
    assert all(c["parsed"]["letter"] for c in s3)                       # explanation text logged
    assert all(c["reasoning"] for c in calls)                           # mock exposes a trace at effort=low
    assert all(isinstance(c["order_seed"], int) for c in calls)
    json.loads(open(os.path.join(rd, "calls.jsonl")).readline())         # strict JSON (no NaN)
    assert "NaN" not in open(os.path.join(rd, "calls.jsonl")).read()
    meta = json.load(open(os.path.join(rd, "run_meta.json"))); assert meta["spent_usd"] > 0 and meta["budget_stop"] is None
    run_judge(rd)
    R = analyze(rd, n_perm=200)
    assert R["excluded_from_confirmatory"] and R["parse_rate"] == 1.0 and len(R["holm"]) == 8 and "glmer_confirmatory_name" in R
    assert LB.PRIVATE.startswith(str(tmp_path))   # conftest redirects the private store into a throw-away dir
    paths, kp = LB.export(rd, str(tmp_path / "labeling"))
    import pandas as pd
    key = json.load(open(kp))
    for lab in ("A", "B"):
        d = pd.read_csv(paths[lab][1], dtype=str, keep_default_na=False)
        assert list(d.columns) == ["item_id", "task", "text", "label", "comment"]
        d["label"] = [str(key["labelers"][lab]["map"][i]["judge"]) for i in d.item_id]; d.to_csv(paths[lab][1], index=False)
    res = LB.agreement(rd, paths["A"][1], paths["B"][1], B=200)
    hj = res["per_task"]["hedging"]["A_vs_judge"]   # 15.8.3: mock letters may be constant -> kappa undefined, gate not evaluable
    assert (hj["kappa"] == 1.0 or (hj["kappa"] is None and hj["gate_status"] == LB.NOT_EVALUABLE)) and hj["raw_agreement"] == 1.0 and res["per_task"]["hedging"]["human_human"]["raw_agreement"] == 1.0

def test_budget_stop_in_run(tmp_path):
    rd = run(_cfg(tmp_path, budget_usd=0.02, per_vendor_budget_usd={"mock": 0.02}), out_root=str(tmp_path), limit=3)
    meta = json.load(open(os.path.join(rd, "run_meta.json")))
    assert meta["budget_stop"] and meta["spent_usd"] <= 0.02

def test_injected_bias_detected(tmp_path):
    cfg = yaml.safe_load(open(P("configs", "pilot_mock.yaml")))
    cfg["models"] = [{"vendor": "mock", "model": "mock-1", "effort": "low", "bias": {"name": 0.9}}]; cfg["setups"] = [1, 3]
    p = tmp_path / "b.yaml"; yaml.safe_dump(cfg, open(p, "w"))
    rd = run(str(p), out_root=str(tmp_path)); run_judge(rd); R = analyze(rd, n_perm=500)
    assert R["M3_name"]["net_flip_rate"] > 0.2 and R["M3_name"]["p_mcnemar"] < 0.05
    assert R["M3_zip"]["p_mcnemar"] > 0.05   # no ZIP bias injected
    assert R["M1b_name"]["diff"] > 0 and R["M1b_name"]["p_perm"] < 0.05     # hedging injected on Black-name letters

def test_paid_requires_flag(tmp_path):
    with pytest.raises(SystemExit): run(P("configs", "pilot.yaml"), out_root=str(tmp_path))

def test_fallback_bounds_track_each_proxy(tmp_path):
    """Regression for the identical 0.434 bounds: with different injected name/ZIP biases the M3 bounds must differ,
    and each proxy must be computed from its own pairs."""
    import pandas as pd
    from llmbias.analysis import load_calls, majority, pair_table
    tp = pd.read_csv(P("data", "processed", "tract_pairs.csv"), dtype={"zip_b": str})
    cfg = yaml.safe_load(open(P("configs", "pilot_mock.yaml")))
    cfg["models"] = [{"vendor": "mock", "model": "mock-1", "effort": "low", "bias": {"name": 0.9, "zip": 0.25, "zip_treated": sorted(set(tp.zip_b))}}]; cfg["setups"] = [1]
    p = tmp_path / "b.yaml"; yaml.safe_dump(cfg, open(p, "w"))
    rd = run(str(p), out_root=str(tmp_path)); R = analyze(rd, n_perm=200)
    fn, fz = R["fallback"]
    assert R["M3_name"]["flips"] > R["M3_zip"]["flips"] and fn["upper"] != fz["upper"]
    pt = pair_table(majority(load_calls(rd)))
    pr = pt[(pt.pair_type == "proxy") & (pt.setup == 1)]
    assert set(pr[pr.proxy == "name"].vb) == {"name_b"} and set(pr[pr.proxy == "zip"].vb) == {"zip_b"}
