"""No-network dry run of the 15.9(5) mini-pilot (configs/minipilot_haiku_batch.yaml): runs the paid-run guards as if
--allow-paid (no client is built), builds every planned request record, counts calls per vendor x setup, and projects
expected and worst cost per vendor on the cap basis (configs/token_basis.yaml, = budget.PHASE_CAPS['minipilot']).
The (5b) mini planted-bias check is NOT included (separate config, not yet implemented).
  python scripts/minipilot_dryrun.py"""
import json, os, sys, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from llmbias.build_data import P
from llmbias.runner import config_guard, build_record, load_items, _clean
from llmbias.batch import plan_trim
from llmbias.budget import load_prices, PHASE_CAPS
from llmbias.prompts import PROMPT_VERSION
from llmbias.output_schema import lexicon

CFG = P("configs", "minipilot_haiku_batch.yaml")

def main(cfg_path=CFG):
    cfg = yaml.safe_load(open(cfg_path))
    config_guard(cfg, allow_paid=True)            # the exact guards a live run applies (no network, no client)
    items = load_items(cfg); prices = load_prices(P("configs", "prices.yaml"))
    t = plan_trim(cfg, prices, items)
    counts, pv, lex = {}, set(), set()
    for setup in cfg["setups"]:
        for m in cfg["models"]:
            for rep in range(cfg["repeats"]):
                for _, it in items.iterrows():
                    app = _clean(it.to_dict())
                    if setup == 2 and app["profile_id"] not in t["setup2_profiles_kept"]: continue
                    rec, _, _ = build_record(cfg, "dry", True, m, setup, app, rep)
                    k = (m["vendor"], setup); counts[k] = counts.get(k, 0) + 1; pv.add(rec["prompt_version"])
    caps = PHASE_CAPS["minipilot"]; vendors = [m["vendor"] for m in cfg["models"]]
    rows = []
    for v in vendors:
        rows.append({"vendor": v, "calls_s1": counts.get((v, 1), 0), "calls_s2": counts.get((v, 2), 0), "calls_s3": counts.get((v, 3), 0),
                     "calls": sum(n for (vv, _), n in counts.items() if vv == v),
                     "judge_letters": sum(counts.values()) if cfg["judge"]["vendor"] == v else 0,
                     "expected_usd": t["expected_by_vendor"][v], "worst_usd": t["projected_by_vendor"][v],
                     "cap_usd": t["per_vendor_cap"][v], "phase_cap_usd": caps[v], "under_cap": t["projected_by_vendor"][v] <= t["per_vendor_cap"][v]})
    rep = {"config": os.path.relpath(cfg_path, P()), "phase": cfg["phase"], "prompt_version": sorted(pv), "code_prompt_version": PROMPT_VERSION,
           "m1a_flag": cfg["m1a_require_codes_on_deny_setup3"], "lexicon_sha256": lexicon()["sha256"], "profiles": int(items.profile_id.nunique()),
           "variants": len(cfg["variants"]), "repeats": cfg["repeats"], "per_vendor": rows,
           "total_calls": sum(counts.values()), "judge_letters": sum(counts.values()),
           "expected_total_usd": round(sum(t["expected_by_vendor"].values()), 3), "worst_total_usd": t["projected_usd"], "total_cap_usd": t["cap"],
           "setup2_profiles_dropped": t["setup2_profiles_dropped"], "vendor_balance_remaining": t["vendor_balance_remaining"],
           "balance_limited": t["balance_limited"], "under_cap": t["under_cap"]}
    print(json.dumps(rep, indent=1)); return rep

if __name__ == "__main__":
    r = main(); sys.exit(0 if r["under_cap"] and not r["setup2_profiles_dropped"] else 1)
