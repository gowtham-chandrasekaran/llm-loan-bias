"""15.9.1 item 7 (amendment logged sha256 5041167f...): live probe of the forced round-1 tool call before the setup-2 re-test.

Design: 2 pilot profiles (1 tool-approve, 1 tool-deny) x 3 vendors x setup 2, base variant, 1 repeat, on BOTH the sync
path (runner.run) and the batch path (batch.start), phase minipilot_s2_probe (cap $0.15 for both paths together), no judge.
Profile rule (deterministic, re-derived by the guard): among the 25 pilot profiles, base variant, sorted by profile_id, the
lowest profile_id whose score_application decision is 'approve' and the lowest whose decision is 'deny' (the same rule as the
golden request-payload fixtures): approve P001, deny P000.

Pass (every probe call, both paths), automated in check_calls():
  forced_tool_call     the round-1 tool call is present (score_application) on the final answer AND on attempt 1 if retried
  schema_valid_final   the final answer passed strict validation (exclusion_reason None)
  max_one_tool_call    at most 1 tool call per attempt (MAX_TOOL_CALLS_PER_CALL)
  haiku_thinking       Anthropic only: thinking disabled (effort none, no thinking text) and numeric thinking tokens == 0
plus completeness (6 answered calls per path, no API error / budget stop / compliance stop). Any failure stops the work:
the batch path is not started after a failed sync path, and the re-test must not start (return to amendment).
Probe outputs live under runs/probe_s2/ with a PROBE_EXCLUDED_FROM_ANALYSIS marker; analysis.load_calls/analyze refuse them.
Only pass/fail and operational rows are reported."""
import copy, datetime as dt, json, os, uuid
import yaml
from .analysis import PROBE_MARKER
from .build_data import P
from .output_schema import exclusion_reason, MAX_TOOL_CALLS_PER_CALL, FORCED_TOOL_NAME

AMENDMENT_SHA256 = "5041167fe1ed230086a89b940d7c54baa639aaccae77c4f1487885919de8e85b"
PHASE = "minipilot_s2_probe"
CFG = P("configs", "minipilot_s2_probe.yaml")
PATHS = ("sync", "batch")
CRITERIA = ("forced_tool_call", "schema_valid_final", "max_one_tool_call", "haiku_thinking")

def select_profiles(items):
    """{'approve': pid, 'deny': pid}: lowest pilot profile_id per tool decision on the base variant."""
    from .runner import _clean
    from .scoring import score_application
    base = items[items.variant == "base"].sort_values("profile_id"); out = {}
    for a in base.to_dict("records"):
        out.setdefault(score_application(_clean(a)).decision, a["profile_id"])
    return {"approve": out["approve"], "deny": out["deny"]}

def projection(cfg, prices, case="worst"):
    """{path: {vendor: usd}} on the K2 cap basis (p95 x1.25, Google x2, +5% reruns; 'expected' = mean tokens).
    Sync is billed at list price, batch at the batch discount. Mock models have no basis -> use the vendor they emulate."""
    from .budget import phase_projection
    n = {2: len(cfg["profile_ids"]) * len(cfg["variants"]) * cfg["repeats"]}
    ms = [{**m, "vendor": m.get("emulate") or m["vendor"], "model": REAL_MODEL.get(m.get("emulate"), m["model"])} for m in cfg["models"]]
    return {p: phase_projection(ms, None, n, prices, case=case, batch=(p == "batch")) for p in PATHS}

REAL_MODEL = {"openai": "gpt-5.4-mini", "anthropic": "claude-haiku-5-5", "google": "gemini-3.8-flash"}

def cost_estimate(cfg, prices):
    from .budget import PHASE_CAPS
    w, e = projection(cfg, prices), projection(cfg, prices, "expected")
    cap = min(cfg["budget_usd"], PHASE_CAPS[PHASE]["total"])
    n_calls = len(cfg["profile_ids"]) * len(cfg["variants"]) * cfg["repeats"] * len(cfg["models"])
    rows = [{"path": p, "vendor": v, "calls": n_calls // len(cfg["models"]), "api_requests": 2 * n_calls // len(cfg["models"]),
             "expected_usd": round(e[p][v], 5), "worst_usd": round(w[p][v], 5)} for p in PATHS for v in w[p]]
    worst = sum(r["worst_usd"] for r in rows)
    return {"rows": rows, "calls_per_path": n_calls, "calls_total": n_calls * len(PATHS),
            "api_requests_total": 2 * n_calls * len(PATHS), "api_requests_max_with_retries": 4 * n_calls * len(PATHS),
            "expected_total_usd": round(sum(r["expected_usd"] for r in rows), 5), "worst_total_usd": round(worst, 5),
            "worst_if_every_call_retried_usd": round(2 * worst, 5), "cap_usd": cap,
            "under_cap": 2 * worst <= cap + 1e-12, "basis": "configs/token_basis.yaml p95 x1.25, Google price x2, +5% reruns; sync list price, batch 50%"}

def probe_guard(cfg, items, prices):
    """Refuse (SystemExit) unless the config is the 15.9.1 probe: phase, design, pinned profiles, no judge, worst case
    (every call retried) under the $0.15 cap."""
    from .budget import PHASE_CAPS
    if cfg.get("phase") != PHASE or not cfg.get("probe"): raise SystemExit(f"REFUSING: probe config needs probe set and phase {PHASE}")
    if cfg.get("amendment_sha256") != AMENDMENT_SHA256: raise SystemExit("REFUSING: amendment_sha256 is not the logged 15.9.1 hash")
    if cfg.get("setups") != [2] or cfg.get("variants") != ["base"] or cfg.get("repeats") != 1 or cfg.get("judge"):
        raise SystemExit("REFUSING: probe design is setup 2 only, base variant, 1 repeat, no judge")
    sel = select_profiles(items)
    if cfg.get("probe_profiles") != sel or sorted(cfg.get("profile_ids") or []) != sorted(sel.values()):
        raise SystemExit(f"REFUSING: probe profiles must be the deterministic selection {sel}")
    if sorted(m.get("emulate") or m["vendor"] for m in cfg["models"]) != sorted(REAL_MODEL):
        raise SystemExit("REFUSING: probe covers exactly openai, anthropic and google")
    est = cost_estimate(cfg, prices)
    if not est["under_cap"] or cfg["budget_usd"] > PHASE_CAPS[PHASE]["total"]:
        raise SystemExit(f"REFUSING: probe worst case {est['worst_if_every_call_retried_usd']} (every call retried) exceeds cap {est['cap_usd']}")
    return est

def mock_config(cfg):
    """Free dry run: same plan, each vendor replaced by the mock emulating it (batch bodies are built with the real builders)."""
    c = copy.deepcopy(cfg)
    c["models"] = [{"vendor": "mock", "model": f"mock-{m['vendor']}", "effort": m.get("effort"), "emulate": m["vendor"]} for m in cfg["models"]]
    c["per_vendor_budget_usd"] = {"mock": c["budget_usd"]}; c["poll_seconds"] = 0
    return c

def _num(x): return isinstance(x, (int, float)) and not isinstance(x, bool)

def check_call(r):
    v = r.get("schema_vendor") or r["vendor"]; tcs = r.get("tool_calls") or []; a1 = r.get("attempt1_tool_calls")
    ok_err = not r.get("error")
    c = {"forced_tool_call": ok_err and len(tcs) >= 1 and all(t.get("name") == FORCED_TOOL_NAME for t in tcs) and (a1 is None or len(a1) >= 1),
         "schema_valid_final": ok_err and exclusion_reason(r) is None and bool((r.get("parsed") or {}).get("valid")),
         "max_one_tool_call": ok_err and len(tcs) <= MAX_TOOL_CALLS_PER_CALL and len(a1 or []) <= MAX_TOOL_CALLS_PER_CALL,
         "haiku_thinking": None if v != "anthropic" else (ok_err and r.get("effort") == "none" and _num(r.get("reasoning_tokens"))
                                                           and r["reasoning_tokens"] == 0 and not r.get("reasoning") and not r.get("reasoning_kind"))}
    return {"vendor": v, "model": r["model"], "profile_id": r["profile_id"], "tool_decision": r.get("tool_decision"), "attempt": r.get("attempt", 1),
            "retry_reason": r.get("retry_reason"), "n_tool_calls": len(tcs), "s2_compliance": r.get("s2_compliance"),
            "first_attempt_forced_call_ok": r.get("first_attempt_forced_call_ok"),
            "reasoning_tokens": r.get("reasoning_tokens"), "schema_mode": r.get("schema_mode"), "error": (r.get("error") or None) and str(r["error"])[:200],
            **c, "pass": all(x is not False for x in c.values())}      # haiku_thinking None = not applicable (non-Anthropic)

def check_calls(rd, n_expected):
    """Per-call pass rows + path verdict for one run dir. Operational only (no decision / letter content is reported)."""
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))] if os.path.exists(os.path.join(rd, "calls.jsonl")) else []
    meta = json.load(open(os.path.join(rd, "run_meta.json")))
    rows = [check_call(r) for r in calls]
    stops = {k: meta.get(k) for k in ("budget_stop", "compliance_stop") if meta.get(k)}
    if meta.get("vendor_budget_stops"): stops["vendor_budget_stops"] = meta["vendor_budget_stops"]
    complete = len(calls) == n_expected
    fa = {}
    for r in rows:
        if r["first_attempt_forced_call_ok"] is not None: fa.setdefault(r["vendor"], []).append(bool(r["first_attempt_forced_call_ok"]))
    return {"run_dir": rd, "n_calls": len(calls), "n_expected": n_expected, "complete": complete, "stops": stops, "rows": rows,
            "error_counts": meta.get("error_counts"), "inflight_worst_at_stop_usd": meta.get("inflight_worst_at_stop_usd"),
            "first_attempt_forced_call_rate": {v: {"ok": sum(x), "n": len(x), "rate": round(sum(x) / len(x), 4)} for v, x in sorted(fa.items())},
            "failed_criteria": sorted({k for r in rows for k in CRITERIA if r[k] is False}), "pass": complete and not stops and all(r["pass"] for r in rows)}

def batch_payload_check(bodies):
    """Dry run only (mock backend builds the real vendor bodies): round 1 forced, round 2 not."""
    from .output_schema import openai_tool_choice, anthropic_tool_choice, google_forced_tool_config
    bad = []
    for cid, rnd, att, b in bodies:
        tc = b.get("tool_choice"); g = (b.get("config") or {}).get("tool_config")
        forced = tc in (openai_tool_choice(), anthropic_tool_choice()) or g == google_forced_tool_config()
        if (rnd == 1) != forced: bad.append((cid, rnd, att))
    return {"n_bodies": len(bodies), "round1": sum(1 for x in bodies if x[1] == 1), "round2": sum(1 for x in bodies if x[1] == 2), "bad": bad, "pass": bool(bodies) and not bad}

def run_probe(cfg_path=CFG, live=False, out_root=None, log=print):
    """Dry run (default): mock models, no network, $0. live=True: real API calls (needs Gowtham's direct 'run it')."""
    from .budget import load_prices
    from .runner import config_guard, load_items, run as run_sync
    from . import batch as B
    cfg = yaml.safe_load(open(cfg_path)); prices = load_prices(P("configs", "prices.yaml"))
    config_guard(cfg, allow_paid=True); items = load_items(cfg)
    est = probe_guard(cfg, items, prices)
    log("COST ESTIMATE (before any call):\n" + json.dumps(est, indent=1))
    use = cfg if live else mock_config(cfg)
    pid = f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}-{'live' if live else 'dryrun'}"
    root = out_root or P("runs", "probe_s2"); os.makedirs(root, exist_ok=True)
    marker = f"15.9.1 probe outputs (sha256 {AMENDMENT_SHA256[:8]}): excluded from all analyses and from the re-test replacement data.\n"
    open(os.path.join(root, PROBE_MARKER), "w").write(marker)
    pdir = os.path.join(root, pid); os.makedirs(pdir); open(os.path.join(pdir, PROBE_MARKER), "w").write(marker)
    n_exp = len(cfg["profile_ids"]) * len(cfg["models"])
    rep = {"probe_id": pid, "live": live, "amendment_sha256": AMENDMENT_SHA256, "phase": PHASE, "profiles": cfg["probe_profiles"],
           "cost_estimate": est, "paths": {}, "pass": False}
    sync_cfg = os.path.join(pdir, "sync_config.yaml"); yaml.safe_dump(use, open(sync_cfg, "w"), sort_keys=False)
    rd = run_sync(sync_cfg, allow_paid=live, out_root=os.path.join(pdir, "sync"))
    rep["paths"]["sync"] = check_calls(rd, n_exp)
    spent = json.load(open(os.path.join(rd, "run_meta.json"))).get("spent_usd") or 0.0
    if rep["paths"]["sync"]["pass"]:
        bcfg = dict(use, budget_usd=round(max(use["budget_usd"] - spent, 0.0), 6), mode="batch")
        if not live: bcfg["per_vendor_budget_usd"] = {"mock": bcfg["budget_usd"]}
        bcfg_p = os.path.join(pdir, "batch_config.yaml"); yaml.safe_dump(bcfg, open(bcfg_p, "w"), sort_keys=False)
        seen = []; orig_be = B.BatchRun.be
        def _be(self, vendor):                              # dry run: keep the mock backend to read the real vendor bodies it built
            b = orig_be(self, vendor); seen.append(b); return b
        if not live: B.BatchRun.be = _be; B.MockBackend.bodies = []
        try:
            brd = B.start(bcfg_p, allow_paid=live, out_root=os.path.join(pdir, "batch"), poll_s=bcfg.get("poll_seconds", 60))
        finally:
            B.BatchRun.be = orig_be
        rep["paths"]["batch"] = check_calls(brd, n_exp)
        if not live:
            bodies = next((list(getattr(b, "bodies", [])) for b in seen), [])
            rep["paths"]["batch"]["payload_check"] = batch_payload_check(bodies)
            rep["paths"]["batch"]["pass"] = rep["paths"]["batch"]["pass"] and rep["paths"]["batch"]["payload_check"]["pass"]
        spent += json.load(open(os.path.join(brd, "run_meta.json"))).get("spent_usd") or 0.0
    else:
        rep["paths"]["batch"] = {"pass": False, "not_run": "sync path failed (15.9.1 item 7: any failure stops the work)"}
    rep["spent_usd"] = round(spent, 6)
    if not live: rep["spend_note"] = "mock models priced like gpt-5.4-mini for realism; $0 real spend, nothing written to the cumulative ledger"
    rep["pass"] = all(rep["paths"][p]["pass"] for p in PATHS)
    rep["verdict"] = ("PASS: forced round-1 tool call verified on sync and batch" if rep["pass"] else
                      "FAIL: stop; return to amendment; the setup-2 re-test must NOT start (15.9.1 item 7)")
    json.dump(rep, open(os.path.join(pdir, "probe_report.json"), "w"), indent=1, default=str)
    return rep
