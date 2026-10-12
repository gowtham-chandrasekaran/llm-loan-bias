"""Pilot driver (no harness change): projection hard-stop -> batch run -> resubmit failed items once -> judge batch."""
import json, os, sys, yaml
sys.path.insert(0, "src")
from llmbias.batch import start, BatchRun, plan_trim, load_items
from llmbias.budget import load_prices, estimate
CFG = "configs/pilot_batch.yaml"
cfg = yaml.safe_load(open(CFG)); pr = load_prices("configs/prices.yaml"); items = load_items(cfg)
t = plan_trim(cfg, pr, items)
proj = estimate(cfg, pr, items.profile_id.nunique(), len(cfg["variants"]), batch=True, n_profiles_by_setup={2: len(t["setup2_profiles_kept"])})
byv = {}
for r in proj: byv[r["vendor"]] = byv.get(r["vendor"], 0) + r["usd"]
print("projection", byv, sum(byv.values()), "trim", t.get("setup2_profiles_dropped"), flush=True)
if sum(byv.values()) > cfg["budget_usd"] or any(v > cfg["per_vendor_budget_usd"][k] for k, v in byv.items()) or t.get("setup2_profiles_dropped"):
    raise SystemExit("HARD STOP: projection exceeds a cap or requires trimming")
rd = start(CFG, allow_paid=True); print("RUN_DIR", rd, flush=True)
br = BatchRun(rd, cfg, True)
failed = [r for r in br.state["requests"].values() if r["status"] == "failed"]
print("failed after first pass:", len(failed), flush=True)
if failed:
    for r in failed:
        r["first_error"] = r.get("error"); r["status"] = "pending"; r["resubmitted"] = True
    br.save(); br.loop(cfg.get("poll_seconds", 60))
    print("failed after resubmit:", sum(r["status"] == "failed" for r in br.state["requests"].values()), flush=True)
print("DONE_MAIN", rd, flush=True)
