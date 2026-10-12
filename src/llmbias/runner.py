"""Run harness. Usage:
  python -m llmbias.runner --config configs/pilot_mock.yaml            # mock dry run (free)
  python -m llmbias.runner --config configs/pilot.yaml --estimate-only # cost estimate, no calls
  python -m llmbias.runner --config configs/pilot.yaml --allow-paid    # REAL calls (requires explicit flag)
"""
import argparse, datetime as dt, hashlib, json, os, subprocess, time, uuid
import pandas as pd, yaml
from .build_data import P
from .budget import BudgetGuard, BudgetExceeded, load_prices, estimate
from .clients import make_client
from .parsing import parse_output
from .prompts import PROMPT_VERSION, setup1, setup2, setup3
from .render import order_seed, render
from .scoring import score_application, TOOL_SPEC, THRESHOLD

def _clean(o):
    if isinstance(o, float) and o != o: return None
    if isinstance(o, dict): return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_clean(v) for v in o]
    return o

def git_commit():
    try:
        return subprocess.check_output(["git", "-C", P(), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None

def load_items(cfg, variants_csv=None):
    v = pd.read_csv(variants_csv or P("data", "processed", "variants.csv"), dtype={"property_zip": str})
    if cfg.get("pilot"):
        v = v[v.pilot]
    v = v[v.variant.isin(cfg["variants"])]
    return v

def tool_fn_factory():
    def fn(args):
        r = score_application(args); return {"score": r.score, "decision": r.decision, "reason_codes": r.reason_codes}
    return fn

def build_record(cfg, run_id, excluded, m, setup, app, rep):
    """Shared by sync and batch runners: renders the prompt and builds the logged record (without the response)."""
    oseed = order_seed(cfg["run_seed"], app["profile_id"], rep)
    text = render(app, oseed)
    tool = score_application(app); toold = {"score": tool.score, "decision": tool.decision, "reason_codes": tool.reason_codes}
    sysm, user = {1: lambda: setup1(text), 2: lambda: setup2(text), 3: lambda: setup3(text, toold)}[setup]()
    rec = {"run_id": run_id, "call_id": uuid.uuid4().hex, "ts": dt.datetime.now().astimezone().isoformat(),
           "excluded_from_confirmatory": excluded, "prompt_version": PROMPT_VERSION,
           "vendor": m["vendor"], "model": m["model"], "effort": m.get("effort"), "setup": setup,
           "profile_id": app["profile_id"], "stratum": app["stratum"], "variant": app["variant"], "variant_id": app["variant_id"],
           "repeat": rep, "order_seed": oseed, "applicant_name": app.get("applicant_name"),
           "property_zip": app.get("property_zip") if isinstance(app.get("property_zip"), str) else None,
           "tool_decision": toold["decision"], "tool_score": toold["score"], "tool_reason_codes": toold["reason_codes"],
           "tool_threshold": THRESHOLD,
           "hmda_denial_reasons": [None if pd.isna(app.get(f"hmda_denial_reason_{i}")) else str(app.get(f"hmda_denial_reason_{i}")).split(".")[0] for i in range(1, 5)],
           "proxy_state": None if pd.isna(app.get("proxy_state")) else str(app.get("proxy_state")).split(".")[0],
           "name_source": None if pd.isna(app.get("name_source")) else str(app.get("name_source")),
           "name_pair_sensitivity_only": None if pd.isna(app.get("name_pair_sensitivity_only")) else bool(app.get("name_pair_sensitivity_only")),
           "name_pair_no_class": None if pd.isna(app.get("name_pair_no_class")) else bool(app.get("name_pair_no_class")),
           "name_pair_id": app.get("name_pair_id"), "name_pair_strict_155": None if pd.isna(app.get("name_pair_strict_155")) else bool(app.get("name_pair_strict_155")),
           "system": sysm, "prompt": user, "prompt_sha256": hashlib.sha256((sysm + user).encode()).hexdigest()}
    return rec, sysm, user

def finish_record(rec, res, cost):
    setup = rec["setup"]
    rec.update(response=res.text, reasoning=res.reasoning, reasoning_kind=res.reasoning_kind, model_version=res.model_version,
               input_tokens=res.input_tokens, output_tokens=res.output_tokens, reasoning_tokens=res.reasoning_tokens,
               visible_output_tokens=None if res.reasoning_tokens is None else res.output_tokens - res.reasoning_tokens,
               tool_calls=res.tool_calls, tool_called=bool(res.tool_calls) if setup == 2 else None, cost_usd=cost)
    p = parse_output(res.text); rec["parsed"] = p
    rec["override"] = (p.get("decision") != rec["tool_decision"]) if setup in (2, 3) and p.get("decision") else None
    return rec

def run(cfg_path, allow_paid=False, out_root=None, limit=None, variants_csv=None):
    cfg = yaml.safe_load(open(cfg_path))
    paid = [m for m in cfg["models"] + [cfg["judge"]] if m["vendor"] != "mock"]
    if paid and not allow_paid:
        raise SystemExit("config contains paid vendors; pass --allow-paid to make real API calls")
    prices = load_prices(P("configs", "prices.yaml"))
    items = load_items(cfg, variants_csv)
    if limit: items = items[items.profile_id.isin(items.profile_id.unique()[:limit])]
    chash = hashlib.sha256(open(cfg_path, "rb").read()).hexdigest()[:8]
    run_id = f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{chash}-{uuid.uuid4().hex[:6]}"
    rd = os.path.join(out_root or P("runs"), run_id); os.makedirs(rd)
    meta = {"run_id": run_id, "config_path": cfg_path, "config": cfg, "git_commit": git_commit(), "prompt_version": PROMPT_VERSION,
            "started": dt.datetime.now().astimezone().isoformat(), "pilot": bool(cfg.get("pilot")),
            "excluded_from_confirmatory": bool(cfg.get("pilot")), "n_items": len(items)}
    json.dump(meta, open(os.path.join(rd, "run_meta.json"), "w"), indent=2, default=str)
    guard = BudgetGuard(cfg["budget_usd"], cfg.get("per_vendor_budget_usd", {}), prices, os.path.join(rd, "spend_ledger.jsonl"))
    tool_fn = tool_fn_factory(); log = open(os.path.join(rd, "calls.jsonl"), "a"); stopped = None
    try:
        for setup in cfg["setups"]:                       # setup order from config (3 first = top priority)
            for m in cfg["models"]:
                ckw = {"bias": m["bias"]} if m["vendor"] == "mock" and m.get("bias") else {}
                client = make_client(m["vendor"], m["model"], m.get("effort"), max_output_tokens=cfg["max_output_tokens"], **ckw)
                for rep in range(cfg["repeats"]):
                    for _, it in items.iterrows():
                        app = _clean(it.to_dict())
                        rec, sysm, user = build_record(cfg, run_id, meta["excluded_from_confirmatory"], m, setup, app, rep)
                        call_id = rec["call_id"]; toold = {"decision": rec["tool_decision"]}
                        try:
                            guard.check(m["vendor"], m["model"], len(sysm + user) // 3, cfg["max_output_tokens"], 2 if setup == 2 else 1)
                        except BudgetExceeded as e:
                            stopped = str(e); rec["error"] = f"budget_stop: {e}"; log.write(json.dumps(_clean(rec)) + "\n"); raise
                        t0 = time.time()
                        try:
                            res = client.complete(sysm, user, TOOL_SPEC if setup == 2 else None, tool_fn if setup == 2 else None)
                            finish_record(rec, res, guard.record(m["vendor"], m["model"], res.input_tokens, res.output_tokens, call_id))
                        except Exception as e:
                            rec["error"] = f"{type(e).__name__}: {str(e)[:500]}"
                        rec["latency_s"] = round(time.time() - t0, 3)
                        log.write(json.dumps(_clean(rec), default=str, allow_nan=False) + "\n")
    except BudgetExceeded:
        pass
    finally:
        log.close()
        meta.update(finished=dt.datetime.now().astimezone().isoformat(), budget_stop=stopped, spent_usd=round(guard.spent, 6), spent_by_vendor=guard.by_vendor)
        json.dump(meta, open(os.path.join(rd, "run_meta.json"), "w"), indent=2, default=str)
    return rd

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True); ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--estimate-only", action="store_true"); ap.add_argument("--batch", action="store_true"); ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.estimate_only:
        cfg = yaml.safe_load(open(a.config)); items = load_items(cfg)
        rows = estimate(cfg, load_prices(P("configs", "prices.yaml")), items.profile_id.nunique(), len(cfg["variants"]), batch=a.batch)
        df = pd.DataFrame(rows); print(df.to_string(index=False)); print("TOTAL USD", round(df.usd.sum(), 2)); return
    print(run(a.config, a.allow_paid, limit=a.limit))

if __name__ == "__main__":
    main()
