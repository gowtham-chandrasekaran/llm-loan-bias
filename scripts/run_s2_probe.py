"""15.9.1 item 7 probe driver (configs/minipilot_s2_probe.yaml; amendment sha256 5041167f...).
  python scripts/run_s2_probe.py                 # FREE dry run: mock models emulating each vendor, sync + batch paths, $0
  python scripts/run_s2_probe.py --live          # REAL calls, phase minipilot_s2_probe ($0.15 cap); needs Gowtham's direct "run it"
Prints the cost estimate before any call. Without --live nothing is sent to any vendor. Exit code 0 = PASS, 1 = FAIL
(stop; return to amendment; the setup-2 re-test must not start)."""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from llmbias import probe_s2 as PR

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=PR.CFG); ap.add_argument("--out", help="output root (default runs/probe_s2)")
    ap.add_argument("--live", action="store_true", help="make REAL paid API calls (otherwise a free mock dry run)")
    ap.add_argument("--allow-paid", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.allow_paid and not a.live:
        raise SystemExit("REFUSING: live probe calls need the explicit --live flag (--allow-paid alone is not enough)")
    print("MODE:", "LIVE (paid API calls)" if a.live else "DRY RUN (mock, no network, $0)")
    rep = PR.run_probe(a.config, live=a.live, out_root=a.out)
    summary = {"probe_id": rep["probe_id"], "live": rep["live"], "profiles": rep["profiles"], "spent_usd": rep["spent_usd"], "spend_note": rep.get("spend_note"),
               "paths": {p: {k: v for k, v in d.items() if k != "rows"} for p, d in rep["paths"].items()},
               "rows": [dict(path=p, **{k: r[k] for k in ("vendor", "profile_id", "tool_decision", "attempt", "first_attempt_forced_call_ok", "n_tool_calls", "reasoning_tokens",
                                                            "schema_mode", *PR.CRITERIA, "pass")}) for p, d in rep["paths"].items() for r in d.get("rows", [])],
               }
    rows = summary.pop("rows")
    print(json.dumps(summary, indent=1, default=str))
    print("path  vendor     profile tool     att fa_forced n_tool reasoning_tok schema_mode        " + " ".join(PR.CRITERIA) + " pass")
    for r in rows:
        print(f"{r['path']:5} {r['vendor']:10} {r['profile_id']:7} {str(r['tool_decision']):8} {r['attempt']:>3} {str(r['first_attempt_forced_call_ok']):>9} {r['n_tool_calls']:>6} {str(r['reasoning_tokens']):>13} "
              f"{str(r['schema_mode']):18} " + " ".join(f"{'n/a' if r[k] is None else str(r[k]):>{len(k)}}" for k in PR.CRITERIA) + f" {r['pass']}")
    for p, d in rep["paths"].items():
        ec = d.get("error_counts") or {}
        print(f"unanswered [{p}]:", ", ".join(f"{k} {x['total']}" for k, x in ec.items()) or "n/a")
        print(f"first-attempt forced-call rate [{p}]:", ", ".join(f"{v} {x['ok']}/{x['n']} = {x['rate']:.0%}" for v, x in d.get("first_attempt_forced_call_rate", {}).items()) or "n/a")
    print("VERDICT:", rep["verdict"])
    return 0 if rep["pass"] else 1

if __name__ == "__main__":
    sys.exit(main())
