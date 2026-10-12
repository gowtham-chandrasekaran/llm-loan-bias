"""After a per-vendor budget stop: keep the capped vendor stopped (its pending items marked 'budget_stopped'),
clear the global stop flag so the other vendors' already-planned items (within their own caps) can finish."""
import json, sys, collections
sys.path.insert(0, "src")
from llmbias.batch import BatchRun
rd, capped = sys.argv[1], sys.argv[2]
cfg = json.load(open(f"{rd}/batch_state.json"))["meta"]["config"]
br = BatchRun(rd, cfg, True)
stop_msg = br.state.get("budget_stop")
for r in br.state["requests"].values():
    if r["vendor"] == capped and r["status"] == "pending":
        r["status"] = "budget_stopped"; r["error"] = f"budget_stop: {stop_msg}"
br.state["budget_stop_history"] = br.state.get("budget_stop_history", []) + [stop_msg]; br.state["budget_stop"] = None; br.save()
br.loop(cfg.get("poll_seconds", 60))
s = json.load(open(f"{rd}/batch_state.json"))
print(collections.Counter((r["vendor"], r["status"]) for r in s["requests"].values()))
print("DONE_RESUME", flush=True)
