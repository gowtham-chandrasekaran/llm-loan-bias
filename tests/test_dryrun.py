import json, os, yaml, pytest
from llmbias.runner import run
from llmbias.judge import run_judge
from llmbias.analysis import analyze
from llmbias.labels import export, kappa
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
    assert any(c["tool_called"] for c in s2) and any(not c["tool_called"] for c in s2)
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
    p, n = export(rd, n=120); assert n >= 100
    import pandas as pd
    L = pd.read_csv(p); assert set(L.columns) == {"item_id", "task", "text", "human_label"}
    J = {j["item_id"]: j for j in (json.loads(l) for l in open(os.path.join(rd, "judgments.jsonl")))}
    L["human_label"] = [str(bool(J[i]["hedging"])).lower() if t == "hedging" else str(bool(J[i.split("#s")[0]]["spans"][int(i.split("#s")[1])]["mentions_proxy"])).lower() for i, t in zip(L.item_id, L.task)]
    L.to_csv(p, index=False); k = kappa(rd, p); assert k["hedging"]["kappa"] == 1.0

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
