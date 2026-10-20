"""15.9.1 items 4-6 setup-2 re-test driver (configs/minipilot_s2_retest.yaml; amendment sha256 5041167f...).
  python scripts/run_s2_retest.py                  # FREE dry run: mocks emulating each vendor + mock judge, 1,800 calls, $0
  python scripts/run_s2_retest.py --estimate-only  # print the per-vendor cost estimate vs the caps and stop
  python scripts/run_s2_retest.py --live --probe-report runs/probe_s2/<live>/probe_report.json
                                                   # REAL calls ($3.88 caps); needs Gowtham's direct "run it"
Prints the per-vendor cost estimate against the caps before any call. Without --live nothing is sent to any vendor; the dry
run's blind keys go to a separate private root (default /home/box/private/blind_keys_dryrun), never into the run folder.
The dry run also demonstrates the item-5 archive/replacement on a COPY of the core run (--core; the real core is only read).
The live replacement on the real core is a separate step: python -m llmbias.s2_replace --core ... --retest ..."""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from llmbias import s2_retest as RT
from llmbias.build_data import P

def check_probe_report(path):
    """15.9.1 item 7 gate for --live: a PASSING LIVE probe report, with the logged amendment hash and probe phase, located
    under runs/probe_s2/<probe_id>/ next to the PROBE_EXCLUDED_FROM_ANALYSIS marker the probe driver writes."""
    from llmbias.analysis import PROBE_MARKER
    from llmbias.probe_s2 import AMENDMENT_SHA256, PHASE
    bad = lambda why: SystemExit(f"REFUSING: --live needs --probe-report pointing at a PASSING live probe report (15.9.1 item 7): {why}")
    if not path or not os.path.isfile(path): raise bad("no report file")
    p = os.path.realpath(path); d = os.path.dirname(p); root = os.path.realpath(P("runs", "probe_s2"))
    if os.path.dirname(d) != root or os.path.basename(p) != "probe_report.json": raise bad(f"not runs/probe_s2/<probe_id>/probe_report.json ({p})")
    if not os.path.isfile(os.path.join(d, PROBE_MARKER)): raise bad(f"no {PROBE_MARKER} marker next to the report")
    rep = json.load(open(p))
    if rep.get("live") is not True or rep.get("pass") is not True: raise bad("report is not live + pass")
    if rep.get("amendment_sha256") != AMENDMENT_SHA256: raise bad("amendment_sha256 is not the logged 15.9.1 hash")
    if rep.get("phase") != PHASE: raise bad(f"phase {rep.get('phase')!r} != {PHASE}")
    return rep

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=RT.CFG); ap.add_argument("--out", help="output root (dry run default runs/s2_retest_dryrun)")
    ap.add_argument("--live", action="store_true", help="make REAL paid API calls (otherwise a free mock dry run)")
    ap.add_argument("--probe-report", help="--live only: the passing LIVE probe report (15.9.1 item 7 gate)")
    ap.add_argument("--private-root", help=f"dry run blind-key root (default {RT.DRYRUN_PRIVATE})")
    ap.add_argument("--core", default=P("runs", RT.CORE_RUN), help="core run read for the dry-run replacement demo (copied, never modified)")
    ap.add_argument("--no-replace-demo", action="store_true"); ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--allow-paid", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.allow_paid and not a.live: raise SystemExit("REFUSING: live re-test calls need the explicit --live flag (--allow-paid alone is not enough)")
    if a.live: check_probe_report(a.probe_report)
    if a.estimate_only:
        import yaml
        from llmbias.budget import load_prices
        from llmbias.runner import load_items
        cfg = yaml.safe_load(open(a.config)); est = RT.retest_guard(cfg, load_items(cfg), load_prices(P("configs", "prices.yaml")))
        print(RT.format_estimate(est)); return 0
    print("MODE:", "LIVE (paid API calls)" if a.live else "DRY RUN (mock, no network, $0)")
    rep = RT.run_retest(a.config, live=a.live, out_root=a.out, private_root=a.private_root, core_rd=a.core, demo_replace=not a.no_replace_demo)
    print(json.dumps({k: v for k, v in rep.items() if k != "cost_estimate"}, indent=1, default=str))
    return 0

if __name__ == "__main__":
    sys.exit(main())
