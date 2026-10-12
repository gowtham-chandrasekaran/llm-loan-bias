import json, os, yaml, pytest
import numpy as np, pandas as pd
from llmbias.analysis import newcombe_paired, analyze
from llmbias.build_data import P, load_cfg
from llmbias import batch as B
from llmbias.judge import run_judge

def test_newcombe_properties():
    rng = np.random.default_rng(0); x1 = rng.random(200) < 0.3; x2 = rng.random(200) < 0.1
    r = newcombe_paired(x1, x2); assert not r["degenerate"] and r["lo"] < r["diff"] < r["hi"] and -1 <= r["lo"] and r["hi"] <= 1
    same = newcombe_paired([1, 0, 1, 0] * 10, [1, 0, 1, 0] * 10); assert abs(same["diff"]) < 1e-12 and same["lo"] <= 1e-12 and same["hi"] >= -1e-12
    assert newcombe_paired([], [])["degenerate"]
    z = newcombe_paired([0] * 25, [0] * 25); assert z["diff"] == 0 and z["hi"] > 0   # all-zero still gives a positive upper bound

def test_name_pool_rules():
    pools = pd.read_csv(P("data", "processed", "name_pools.csv")); cfg = load_cfg()
    from llmbias.gate import names_rule_ok
    assert names_rule_ok(pools, cfg)   # amendment 15.6
    assert pools.groupby(["race", "gender"])["first"].apply(lambda s: s.is_unique).all()
    bm = sorted(pools[(pools.race == "B") & (pools.gender == "M")]["first"])
    assert bm == ["Darius", "Jermaine", "Lamont", "Terrell", "Tyrone"]
    assert sorted(pools[(pools.race == "B") & (pools.gender == "M") & pools.strict_155]["first"]) == ["Jermaine", "Tyrone"]
    v = pd.read_csv(P("data", "processed", "variants.csv"), dtype=str)
    base = v[v.variant == "base"].set_index("profile_id").applicant_name
    for pv in ["name_w_placebo", "name_b_placebo"]:
        assert (v[v.variant == pv].set_index("profile_id").applicant_name != (base if pv == "name_w_placebo" else v[v.variant == "name_b"].set_index("profile_id").applicant_name)).all()

def test_tract_pairs_rules():
    tp = pd.read_csv(P("data", "processed", "tract_pairs.csv"), dtype={"msa": str, "tract_a": str, "tract_b": str})
    pp = pd.read_csv(P("data", "processed", "placebo_tract_pairs.csv"), dtype={"msa": str, "tract_a": str, "tract_b": str})
    assert len(tp) >= 15 and len(pp) >= 5 and (tp.msa != "99999").all() and (pp.msa != "99999").all()
    assert ((pp.minority_a - pp.minority_b).abs() <= 10).all()
    v = pd.read_csv(P("data", "processed", "variants.csv"), dtype=str).dropna(subset=["proxy_state"])
    assert (v.hmda_state == v.proxy_state).all()

def _cfg(tmp_path, **over):
    cfg = yaml.safe_load(open(P("configs", "pilot_batch_mock.yaml"))); cfg.update(over)
    p = tmp_path / "c.yaml"; yaml.safe_dump(cfg, open(p, "w")); return str(p)

def test_batch_mock_end_to_end(tmp_path):
    rd = B.start(_cfg(tmp_path), out_root=str(tmp_path), limit=4)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert len(calls) == 4 * 8 * 3 * 3 and all(c["batch"] for c in calls) and all(c.get("parsed", {}).get("parse_ok") for c in calls)
    s2 = [c for c in calls if c["setup"] == 2]
    assert any(c["tool_called"] for c in s2) and any(not c["tool_called"] for c in s2)
    assert all(c["tool_calls"][0]["result"]["decision"] in ("approve", "deny") for c in s2 if c["tool_called"])
    assert all(c["reasoning_tokens"] is not None and "visible_output_tokens" in c for c in calls)
    st = json.load(open(os.path.join(rd, "batch_state.json"))); assert any(b["round"] == 2 for b in st["batches"].values())
    meta = json.load(open(os.path.join(rd, "run_meta.json"))); assert meta["status_counts"] == {"done": len(calls)} and meta["excluded_from_confirmatory"]
    run_judge(rd); R = analyze(rd, n_perm=200); assert R["fallback"][0]["method"] in ("newcombe", "bootstrap")

def test_batch_resume(tmp_path):
    cfgp = _cfg(tmp_path); cfg = yaml.safe_load(open(cfgp))
    from llmbias.runner import load_items
    items = load_items(cfg); items = items[items.profile_id.isin(items.profile_id.unique()[:2])]
    rd = str(tmp_path / "run"); os.makedirs(rd)
    meta = {"run_id": "t", "config": cfg, "excluded_from_confirmatory": True}
    br = B.BatchRun(rd, cfg, False); br.init(items, meta); br.submit_pending()          # "crash" after submit
    st = json.load(open(os.path.join(rd, "batch_state.json"))); assert all(r["status"] == "submitted" for r in st["requests"].values())
    B.resume(rd, poll_s=0)
    st = json.load(open(os.path.join(rd, "batch_state.json"))); assert all(r["status"] == "done" for r in st["requests"].values())

def test_batch_budget_stop(tmp_path):
    rd = B.start(_cfg(tmp_path, budget_usd=0.05, per_vendor_budget_usd={"mock": 0.05}), out_root=str(tmp_path), limit=3)
    meta = json.load(open(os.path.join(rd, "run_meta.json")))
    assert meta["budget_stop"] and meta["spent_usd"] <= 0.05 and meta["status_counts"].get("pending", 0) > 0

def test_trim_drops_setup2_profiles_never_repeats(tmp_path):
    cfg = yaml.safe_load(open(_cfg(tmp_path, budget_usd=4.0, per_vendor_budget_usd={"mock": 4.0})))
    from llmbias.runner import load_items
    t = B.plan_trim(cfg, B.load_prices(P("configs", "prices.yaml")), load_items(cfg))
    assert t["setup2_profiles_dropped"] and t["under_cap"]
    t2 = B.plan_trim(cfg, B.load_prices(P("configs", "prices.yaml")), load_items(cfg)); assert t == t2   # pre-seeded, deterministic

def test_judge_batch_mode(tmp_path):
    from llmbias.judge import run_judge_batch
    rd = B.start(_cfg(tmp_path), out_root=str(tmp_path), limit=2)
    p, stop = run_judge_batch(rd, poll_s=0)
    J = [json.loads(l) for l in open(p)]
    assert J and stop is None and all(j["judge_mode"] == "batch" and j["hedging_status"] == "done" for j in J)
    st = json.load(open(os.path.join(rd, "judge_batch_state.json"))); assert st["batches"] and all(r["status"] == "done" for r in st["requests"].values())
    led = [json.loads(l) for l in open(os.path.join(rd, "spend_ledger.jsonl"))]; assert any(x["call_id"].startswith("j") and x["batch"] for x in led)
    R = analyze(rd, n_perm=100); assert R["parse_rate"] == 1.0
