"""15.9.1 items 4-6: the setup-2 re-test (configs/minipilot_s2_retest.yaml), phase minipilot_s2_retest, caps OpenAI 1.24 /
Anthropic 0.39 / Google 2.25 ($3.88). Driver: scripts/run_s2_retest.py (mock dry run unless --live).
retest_guard re-derives everything the config claims (design, caps, cap basis, sameness with the core config) and refuses
otherwise; cost_estimate prints the per-vendor worst / expected cost against the caps before any call."""
import copy, datetime as dt, json, os, shutil, uuid, yaml
from .build_data import P
from .s2_replace import AMENDMENT_SHA256, CORE_RUN, RETEST_PHASE

CFG = P("configs", "minipilot_s2_retest.yaml")
CORE_CFG = P("configs", "minipilot_haiku_batch.yaml")
SAME_AS_CORE = ("run_seed", "trim_seed", "pilot", "repeats", "variants", "models", "judge", "max_output_tokens", "expected_prompt_version",
                "letter_lexicon_sha256", "m1a_require_codes_on_deny_setup3", "google_tools_schema_mode", "batch_chunk", "est_tokens")
N_PROFILES = 25
DRYRUN_PRIVATE = "/home/box/private/blind_keys_dryrun"   # dry-run blind keys: a separate private root, never the real store

def cost_estimate(cfg, prices):
    """Per vendor: calls, API requests, expected and worst cost on the cap basis vs the caps (judge billed to OpenAI)."""
    from .budget import PHASE_CAPS, phase_projection, retest_cap_basis, RETEST_DESIGN
    cb = retest_cap_basis(cfg["models"], cfg.get("judge"), prices)
    n = N_PROFILES * len(cfg["variants"]) * cfg["repeats"]
    exp = phase_projection(cfg["models"], cfg.get("judge"), {2: n}, prices, case="expected", judge_letters=n * len(cfg["models"]))
    caps = PHASE_CAPS[RETEST_PHASE]; jv = (cfg.get("judge") or {}).get("vendor"); rows = []
    for m in cfg["models"]:
        v = m["vendor"]; b = cb["by_vendor"][v]
        rows.append({"vendor": v, "model": m["model"], "calls": n, "api_requests": 2 * n, "api_requests_max_with_retries": 4 * n,
                     "judge_letters": n * len(cfg["models"]) if v == jv else 0,
                     "expected_usd": round(exp[v], 4), "worst_usd": b["max_usd"], "worst_basis": b["basis_used"],
                     "pilot_basis_worst_usd": b["pilot_basis_usd"], "core_actuals_worst_usd": b["core_actuals_usd"],
                     "cap_usd": caps[v], "config_cap_usd": cfg["per_vendor_budget_usd"].get(v),
                     "worst_under_cap": b["max_usd"] <= caps[v] + 1e-9, "headroom_usd": round(caps[v] - b["max_usd"], 4)})
    return {"phase": RETEST_PHASE, "rows": rows, "calls_total": n * len(cfg["models"]), "judge_letters": RETEST_DESIGN["judged_letters"],
            "expected_total_usd": round(sum(r["expected_usd"] for r in rows), 4), "worst_total_usd": round(sum(r["worst_usd"] for r in rows), 4),
            "cap_total_usd": caps["total"], "under_cap": all(r["worst_under_cap"] for r in rows) and sum(r["worst_usd"] for r in rows) <= caps["total"] + 1e-9,
            "cap_basis_matches_phase_caps": cb["matches_phase_caps"], "basis": cb["rule"]}

def retest_guard(cfg, items, prices, core_cfg=None):
    """Refuse (SystemExit) unless cfg is the 15.9.1 re-test: phase, hash, setups [2], full grid, caps = PHASE_CAPS = cap
    basis, everything else identical to the core mini-pilot config."""
    from .budget import PHASE_CAPS
    from .batch import plan_trim
    c = PHASE_CAPS[RETEST_PHASE]
    if cfg.get("phase") != RETEST_PHASE or cfg.get("probe"): raise SystemExit(f"REFUSING: re-test config needs phase {RETEST_PHASE} (and is not a probe)")
    if cfg.get("amendment_sha256") != AMENDMENT_SHA256: raise SystemExit("REFUSING: amendment_sha256 is not the logged 15.9.1 hash")
    if cfg.get("replaces_setup2_of") != CORE_RUN: raise SystemExit(f"REFUSING: replaces_setup2_of must be the core run {CORE_RUN}")
    if cfg.get("setups") != [2] or cfg.get("mode") != "batch" or cfg.get("profile_ids") or not cfg.get("require_all_profiles"):
        raise SystemExit("REFUSING: re-test design is setup 2 only, batch, all pilot profiles (no profile_ids), require_all_profiles: true")
    if cfg.get("budget_usd") != c["total"] or any(cfg.get("per_vendor_budget_usd", {}).get(v) != c[v] for v in ("openai", "anthropic", "google")):
        raise SystemExit(f"REFUSING: re-test caps must equal PHASE_CAPS[{RETEST_PHASE}] {c}")
    core = core_cfg or yaml.safe_load(open(CORE_CFG))
    diff = [k for k in SAME_AS_CORE if cfg.get(k) != core.get(k)]
    if diff: raise SystemExit(f"REFUSING: re-test settings differ from the core mini-pilot config: {diff}")
    if items.profile_id.nunique() != N_PROFILES or len(items) != N_PROFILES * len(cfg["variants"]):
        raise SystemExit(f"REFUSING: expected {N_PROFILES} pilot profiles x {len(cfg['variants'])} variants, got {items.profile_id.nunique()} / {len(items)}")
    est = cost_estimate(cfg, prices)
    if not est["cap_basis_matches_phase_caps"] or not est["under_cap"]:
        raise SystemExit(f"REFUSING: cap basis does not reproduce the caps or worst case exceeds them: {est}")
    t = plan_trim(cfg, prices, items)
    if t["setup2_profiles_dropped"]: raise SystemExit(f"REFUSING: plan would trim setup-2 profiles {t['setup2_profiles_dropped']}")
    est["plan_projection_by_vendor"] = t["projected_by_vendor"]
    return est

def mock_config(cfg):
    """Free dry run: each vendor (and the judge) replaced by a mock; real vendor batch bodies are still built offline."""
    from .probe_s2 import mock_config as pm
    c = pm(cfg)
    for m in c["models"]: m["model"] = f"mock-batch-{m['emulate']}"     # priced like the emulated model at batch price (prices.yaml)
    if c.get("judge"): c["judge"] = {"vendor": "mock", "model": "mock-batch-judge", "effort": c["judge"].get("effort")}
    return c

def summarize(rd):
    """Operational summary of a re-test run (counts only; no effect estimates)."""
    from .s2_replace import operational_counts
    recs = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl")) if l.strip()]
    meta = json.load(open(os.path.join(rd, "run_meta.json")))
    ops = operational_counts(recs, allow_mock=True)
    for v, d in ops.items():
        d["first_attempt_tool_rate"] = round(d["first_attempt"].get("tool", 0) / d["n"], 4)
        d["final_tool_rate"] = round(d["final"].get("tool", 0) / d["n"], 4)
    return {"run_id": meta["run_id"], "calls": len(recs), "by_vendor": ops, "budget_stop": meta.get("budget_stop"),
            "compliance_stop": meta.get("compliance_stop"), "error_counts": meta.get("error_counts"), "spent_usd": meta.get("spent_usd"),
            "unfetched_batches": meta.get("unfetched_batches"), "cap_basis_in_run_meta": bool(meta.get("cap_basis"))}

def run_retest(cfg_path=CFG, live=False, out_root=None, private_root=None, core_rd=None, demo_replace=True, log=print):
    """Dry run (default): mock models + mock judge, $0, nothing in the cumulative ledger, blind keys under a separate
    private root. live=True: real batch calls + batch judge (needs Gowtham's direct 'run it'; the probe passed)."""
    from .budget import load_prices
    from .runner import config_guard, load_items
    from . import batch as B, judge as JD, labels as LB
    cfg = yaml.safe_load(open(cfg_path)); prices = load_prices(P("configs", "prices.yaml"))
    config_guard(cfg, allow_paid=True); items = load_items(cfg)
    est = retest_guard(cfg, items, prices)
    log("COST ESTIMATE (before any call):\n" + format_estimate(est))
    use = cfg if live else mock_config(cfg)
    root = out_root or (P("runs") if live else P("runs", "s2_retest_dryrun")); os.makedirs(root, exist_ok=True)
    tag = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    cp = os.path.join(root, f"retest_config_{tag}{'' if live else '_mock'}.yaml"); yaml.safe_dump(use, open(cp, "w"), sort_keys=False)
    old_priv = LB.PRIVATE
    if not live: LB.PRIVATE = private_root or DRYRUN_PRIVATE
    try:
        rd = B.start(cp, allow_paid=live, out_root=root, poll_s=None if live else 0)
        rep = {"live": live, "run_dir": rd, "cost_estimate": est, "retest": summarize(rd)}
        jp, jstop = JD.run_judge_batch(rd, allow_paid=live, setups=(2,), poll_s=None if live else 0)
        jm = json.load(open(os.path.join(rd, "run_meta.json")))
        rep["judge"] = {"judgments": sum(1 for _ in open(jp)), "budget_stop": jstop,
                        "blind_key": os.path.join(LB.PRIVATE, jm["run_id"], "blind_key.jsonl"),
                        "blind_key_in_run_folder": any(f.startswith("blind_key") for _, _, fs in os.walk(rd) for f in fs)}
        rep["spent_usd_after_judge"] = sum(json.loads(l).get("cost", 0) for l in open(os.path.join(rd, "spend_ledger.jsonl")))
        if not live and demo_replace and core_rd and os.path.exists(os.path.join(core_rd, "calls.jsonl")):
            rep["replace_demo"] = demo_replacement(core_rd, rd, os.path.join(root, f"replace_demo_{tag}"))
    finally:
        LB.PRIVATE = old_priv
    json.dump(rep, open(os.path.join(rd, "retest_report.json"), "w"), indent=1, default=str)
    return rep

def demo_replacement(core_rd, retest_rd, work):
    """Dry run of item 5 on a COPY of the core (calls, judgments, run_meta): the real core folder is only read."""
    from .s2_replace import replace
    cc = os.path.join(work, os.path.basename(os.path.normpath(core_rd))); os.makedirs(cc)
    for f in ("calls.jsonl", "judgments.jsonl", "run_meta.json"): shutil.copy2(os.path.join(core_rd, f), cc)
    out = replace(cc, retest_rd, os.path.join(work, "merged"), allow_mock=True)
    out["work_dir"] = work
    return out

def format_estimate(est):
    h = f"{'vendor':10} {'model':17} {'calls':>5} {'api_req':>7} {'expected$':>9} {'worst$':>7} {'basis':12} {'cap$':>5} {'headroom$':>9} ok"
    lines = [h] + [f"{r['vendor']:10} {r['model']:17} {r['calls']:>5} {r['api_requests']:>7} {r['expected_usd']:>9.4f} {r['worst_usd']:>7.4f} "
                   f"{r['worst_basis']:12} {r['cap_usd']:>5.2f} {r['headroom_usd']:>9.4f} {r['worst_under_cap']}"
                   + (f"   (incl. judge on {r['judge_letters']} letters)" if r["judge_letters"] else "") for r in est["rows"]]
    lines.append(f"{'TOTAL':10} {'':17} {est['calls_total']:>5} {2 * est['calls_total']:>7} {est['expected_total_usd']:>9.4f} {est['worst_total_usd']:>7.4f} "
                 f"{'':12} {est['cap_total_usd']:>5.2f} {round(est['cap_total_usd'] - est['worst_total_usd'], 4):>9.4f} {est['under_cap']}")
    lines.append(f"basis: {est['basis']}; cap basis reproduces PHASE_CAPS: {est['cap_basis_matches_phase_caps']}")
    return "\n".join(lines)
