"""15.9 (5b) mini planted-bias check driver (configs/minipilot_pc_batch.yaml; rulings sha256 843fcabd...).
  python scripts/run_minipilot_pc.py --dry-run                                  # NO network: guards, planned calls, worst case vs caps
  python scripts/run_minipilot_pc.py --core-run <core run dir> --allow-paid     # REAL calls (needs Gowtham's "run it")
The live run refuses unless the core mini-pilot run is fully collected (R8) and every pc guard passes (codes map, regex sha,
rulings sha, phase minipilot_pc caps with no trimming)."""
import argparse, json, os, sys, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from llmbias.build_data import P
from llmbias import positive_control as PC
CFG = P("configs", "minipilot_pc_batch.yaml")

def dry_run(cfg_path=CFG):
    from llmbias.runner import config_guard, load_items
    from llmbias.budget import load_prices, PHASE_CAPS
    cfg = yaml.safe_load(open(cfg_path)); config_guard(cfg, allow_paid=True)          # the paid-run guards; no client is built
    items = load_items(cfg); prices = load_prices(P("configs", "prices.yaml"))
    worst = PC.pc_guard(cfg, items, prices); exp = PC.projection(cfg, prices, items, "expected")
    reqs = PC.build_requests(cfg, items, "dry")                                         # every planned record, offline
    caps = PHASE_CAPS["minipilot_pc"]; rows = []
    for m in cfg["models"]:
        v = m["vendor"]; rs = [r for r in reqs.values() if r["vendor"] == v]
        by = {a: sum(r["rec"]["pc_arm"] == a for r in rs) for a in PC.ARM_ORDER}
        rows.append({"vendor": v, "m2_setup2": by["m2"], "m1a_setup3": by["m1a"], "m1b_setup3": by["m1b"], "calls": len(rs),
                     "judge_letters": by["m1b"] * len(cfg["models"]) if cfg["judge"]["vendor"] == v else 0,
                     "expected_usd": round(exp[v], 4), "worst_usd": round(worst[v], 4), "cap_usd": min(cfg["per_vendor_budget_usd"][v], caps[v])})
    rep = {"config": os.path.relpath(cfg_path, P()), "phase": cfg["phase"], "rulings_sha256": PC.RULINGS_SHA256, "regex_sha256": PC.PC_M1B_REGEX_SHA256,
           "instructed_codes_sha256": PC.codes_sha256(cfg["pc_instructed_codes"]), "per_vendor": rows, "total_calls": len(reqs),
           "judge_letters": sum(r["judge_letters"] for r in rows), "expected_total_usd": round(sum(exp.values()), 4),
           "worst_total_usd": round(sum(worst.values()), 4), "total_cap_usd": min(cfg["budget_usd"], caps["total"]),
           "under_caps": all(r["worst_usd"] <= r["cap_usd"] for r in rows) and sum(worst.values()) <= min(cfg["budget_usd"], caps["total"])}
    print(json.dumps(rep, indent=1)); return rep

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--config", default=CFG); ap.add_argument("--core-run"); ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.dry_run: return dry_run(a.config)
    rd = PC.start(a.config, core_run=a.core_run, allow_paid=a.allow_paid, out_root=a.out); print("run_dir:", rd); return rd

if __name__ == "__main__":
    main()
