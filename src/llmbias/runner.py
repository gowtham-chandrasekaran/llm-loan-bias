"""Run harness. Usage:
  python -m llmbias.runner --config configs/pilot_mock.yaml            # mock dry run (free)
  python -m llmbias.runner --config configs/pilot.yaml --estimate-only # cost estimate, no calls
  python -m llmbias.runner --config configs/pilot.yaml --allow-paid    # REAL calls (requires explicit flag)
"""
import argparse, datetime as dt, hashlib, json, os, subprocess, time, uuid
import pandas as pd, yaml
from .build_data import P
from .budget import BudgetGuard, BudgetExceeded, load_prices, estimate
from .clients import make_client, CallResult, ToolArgumentError
from .parsing import parse_output
from .output_schema import validate_output, retry_reason, MAX_ATTEMPTS, schema_mode_for, GOOGLE_TOOLS_SCHEMA_MODE, s2_compliance, S2ComplianceStop, tool_rule, error_counts
from .prompts import PROMPT_VERSION, PRE_M1A_PROMPT_VERSIONS, M1A_PROMPT_VERSION, setup1, setup2, setup3
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
    if cfg.get("profile_ids"):            # explicit profile subset (15.9.1 probe: 2 pinned pilot profiles)
        v = v[v.profile_id.isin(cfg["profile_ids"])]
    return v

def tool_fn_factory():
    def fn(args):
        r = score_application(args); return {"score": r.score, "decision": r.decision, "reason_codes": r.reason_codes}
    return fn

ANTHROPIC_ALLOWED_EFFORTS = {"none", None}

def config_guard(cfg, allow_paid, prompt_version=None):
    """15.9 run guards (called by runner.run and batch.start/resume before anything else):
    (a) non-pilot configs (not tagged pilot/minipilot) must have the M1a flag ON; (j) Anthropic effort must be none;
    plus m1a_guard (paid + M1a flag + pre-M1a prompt -> refuse)."""
    dup = duplicate_models(cfg)
    if dup:   # s2-retest fix 1: duplicate entries would share call_ids (under-billing, resume skips) and be indistinguishable in analysis
        raise SystemExit(f"REFUSING run: duplicate model entries in models (vendor, model, effort, emulate): {dup}")
    if not (cfg.get("pilot") or cfg.get("minipilot")) and not cfg.get("m1a_require_codes_on_deny_setup3"):
        raise SystemExit("REFUSING run: non-pilot config (not tagged pilot/minipilot) with m1a_require_codes_on_deny_setup3 OFF. "
                         "Main-run configs must enable the M1a hook (draft 15.9 (2a)/(5a)).")
    pv_cfg = cfg.get("expected_prompt_version")
    if pv_cfg is not None and pv_cfg != (prompt_version or PROMPT_VERSION):
        raise SystemExit(f"REFUSING run: config expects prompt {pv_cfg!r} but code has {prompt_version or PROMPT_VERSION!r}.")
    if cfg.get("letter_lexicon_sha256"):
        from .output_schema import lexicon
        if lexicon()["sha256"] != cfg["letter_lexicon_sha256"]:
            raise SystemExit(f"REFUSING run: letter lexicon sha256 {lexicon()['sha256'][:8]} != frozen {cfg['letter_lexicon_sha256'][:8]}.")
    _paid = allow_paid and any(m["vendor"] != "mock" for m in cfg["models"] + ([cfg["judge"]] if cfg.get("judge") else []))
    if _paid and cfg.get("phase") in ("minipilot", "main") and (not pv_cfg or not cfg.get("letter_lexicon_sha256")):
        raise SystemExit(f"REFUSING run: phase {cfg['phase']!r} config must pin expected_prompt_version and letter_lexicon_sha256 for a paid run.")
    if cfg.get("phase") in ("minipilot", "main") and cfg.get("m1a_require_codes_on_deny_setup3") is not True:
        raise SystemExit(f"REFUSING run: phase {cfg['phase']!r} requires m1a_require_codes_on_deny_setup3: true set explicitly "
                         "(the pilot:true tag does not exempt mini-pilot or main runs).")
    for m in cfg["models"] + ([cfg["judge"]] if cfg.get("judge") else []):
        if m["vendor"] == "anthropic" and m.get("effort") not in ANTHROPIC_ALLOWED_EFFORTS:
            raise SystemExit(f"REFUSING run: Anthropic effort {m.get('effort')!r} for {m['model']} is not allowed; only effort 'none' "
                             "(thinking disabled). The batch/sync adapters send extended-thinking budgets that Haiku 5.5 rejects.")
    m1a_guard(cfg, allow_paid, prompt_version)
    from .budget import PHASE_CAPS
    if allow_paid and any(m["vendor"] != "mock" for m in cfg["models"]) and cfg.get("phase") not in PHASE_CAPS:
        raise SystemExit(f"REFUSING paid run: config must set phase to one of {sorted(PHASE_CAPS)} (K1 phase caps).")
    pv = prompt_version or PROMPT_VERSION
    if pv not in PRE_M1A_PROMPT_VERSIONS and not cfg.get("m1a_require_codes_on_deny_setup3"):   # (i) the reverse direction
        raise SystemExit(f"REFUSING run: PROMPT_VERSION={pv!r} is the M1a prompt, which requires m1a_require_codes_on_deny_setup3: true.")

def m1a_guard(cfg, allow_paid, prompt_version=None):
    """15.9: refuse a PAID run with the M1a code-count hook on while the setup-3 prompt is pre-M1a (still hands over the codes).
    Offline/mock runs are not blocked; tests may also pass cfg["m1a_guard_override_offline_test"] (ignored when paid)."""
    pv = prompt_version or PROMPT_VERSION
    paid = any(m["vendor"] != "mock" for m in cfg["models"] + ([cfg["judge"]] if cfg.get("judge") else []))
    if cfg.get("m1a_require_codes_on_deny_setup3") and pv in PRE_M1A_PROMPT_VERSIONS and paid and allow_paid:
        raise SystemExit(f"REFUSING paid run: m1a_require_codes_on_deny_setup3 is ON but PROMPT_VERSION={pv!r} is pre-M1a "
                         "(the setup-3 prompt still gives the tool's reason codes). Ship the approved M1a setup-3 prompt and bump "
                         "PROMPT_VERSION, or turn the flag off.")

def build_record(cfg, run_id, excluded, m, setup, app, rep):
    """Shared by sync and batch runners: renders the prompt and builds the logged record (without the response)."""
    oseed = order_seed(cfg["run_seed"], app["profile_id"], rep)
    text = render(app, oseed)
    tool = score_application(app); toold = {"score": tool.score, "decision": tool.decision, "reason_codes": tool.reason_codes, "components": tool.components}
    sysm, user = {1: lambda: setup1(text), 2: lambda: setup2(text), 3: lambda: setup3(text, toold)}[setup]()
    rec = {"run_id": run_id, "call_id": uuid.uuid4().hex, "ts": dt.datetime.now().astimezone().isoformat(),
           "excluded_from_confirmatory": excluded, "prompt_version": PROMPT_VERSION,
           "vendor": m["vendor"], "model": m["model"], "effort": m.get("effort"), "setup": setup,
           "profile_id": app["profile_id"], "stratum": app["stratum"], "variant": app["variant"], "variant_id": app["variant_id"],
           "repeat": rep, "order_seed": oseed, "applicant_name": app.get("applicant_name"),
           "property_zip": app.get("property_zip") if isinstance(app.get("property_zip"), str) else None,
           "tool_decision": toold["decision"], "tool_score": toold["score"], "tool_reason_codes": toold["reason_codes"],
           "tool_threshold": THRESHOLD, "tool_components": toold["components"],
           "hmda_denial_reasons": [None if pd.isna(app.get(f"hmda_denial_reason_{i}")) else str(app.get(f"hmda_denial_reason_{i}")).split(".")[0] for i in range(1, 5)],
           "proxy_state": None if pd.isna(app.get("proxy_state")) else str(app.get("proxy_state")).split(".")[0],
           "name_source": None if pd.isna(app.get("name_source")) else str(app.get("name_source")),
           "name_pair_sensitivity_only": None if pd.isna(app.get("name_pair_sensitivity_only")) else bool(app.get("name_pair_sensitivity_only")),
           "name_pair_no_class": None if pd.isna(app.get("name_pair_no_class")) else bool(app.get("name_pair_no_class")),
           "name_pair_id": app.get("name_pair_id"), "name_pair_strict_155": None if pd.isna(app.get("name_pair_strict_155")) else bool(app.get("name_pair_strict_155")),
           "validation_rules": {"require_codes_on_deny_setup3": bool(cfg.get("m1a_require_codes_on_deny_setup3"))},
           "schema_vendor": m.get("emulate") or m["vendor"], "google_tools_schema_mode": cfg.get("google_tools_schema_mode", GOOGLE_TOOLS_SCHEMA_MODE),
           "system": sysm, "prompt": user, "prompt_sha256": hashlib.sha256((sysm + user).encode()).hexdigest()}
    if setup == 2: rec["round1_tool_choice"] = "forced:score_application"     # 15.9.1 item 1
    if cfg.get("probe"):                                                     # 15.9.1 item 7: probe rows never enter any analysis
        rec.update(probe=cfg["probe"], excluded_from_analysis=True)
    return rec, sysm, user

def finish_record(rec, res, cost):
    setup = rec["setup"]
    rec.update(response=res.text, reasoning=res.reasoning, reasoning_kind=res.reasoning_kind, model_version=res.model_version,
               input_tokens=res.input_tokens, output_tokens=res.output_tokens, reasoning_tokens=res.reasoning_tokens,
               visible_output_tokens=None if res.reasoning_tokens is None else res.output_tokens - res.reasoning_tokens,
               tool_calls=res.tool_calls, tool_called=bool(res.tool_calls) if setup == 2 else None, cost_usd=cost)
    for k in ("reasoning_tokens_by_round", "usage_metadata", "thought_signatures"):   # response-side logging; absent when not reported
        if getattr(res, k, None) is not None: rec[k] = getattr(res, k)
    p = validate_output(res.text, setup, rules=rec.get("validation_rules"), tool_decision=rec.get("tool_decision") if setup == 3 else None)
    rec["parsed"] = p      # 15.9: strict pydantic validation; parse_ok == valid; setup-3 contradiction flags inside parsed + top level
    rf = getattr(res, "refusal", None); rec["refusal"] = rf
    if rf:   # 15.9 (d): refusal / safety stop = validation failure (gets the single retry)
        p["valid"] = p["parse_ok"] = False; p["validation_errors"] = p["validation_errors"] + [f"refusal: {rf}"]; p["errors"] = list(p["validation_errors"])
    for k in ("contradiction", "codes_contradiction", "letter_contradiction", "letter_signal", "code_format_anomalies", "letter_lexicon_sha256", "letter_lexicon_version"):
        if k in p: rec[k] = p[k]
    if setup == 3: rec.setdefault("letter_signal", None)   # null only when there is no letter text at all
    rec["normalizations"] = p["normalizations"]; rec["truncated"] = bool(getattr(res, "truncated", False))
    rec["schema_mode"] = schema_mode_for(rec.get("schema_vendor", rec["vendor"]), setup, bool(res.tool_calls), rec.get("google_tools_schema_mode", GOOGLE_TOOLS_SCHEMA_MODE))
    rec.setdefault("attempt", 1)
    rec["override"] = (p.get("decision") != rec["tool_decision"]) if setup in (2, 3) and p.get("decision") else None
    if setup == 2: rec["s2_compliance"] = s2_compliance(rec)   # 15.9.1 item 3: None = exactly one forced round-1 call
    return rec

SYNC_CALL_ID_SCHEME = 2   # 2 = includes the model's index in cfg["models"] (s2-retest fix 1); runs without the meta key used 1

def sync_call_id(run_id, m, setup, app, rep, model_index=None, scheme=SYNC_CALL_ID_SCHEME):
    """Deterministic sync call_id, so a resumed run maps journaled attempts and ledger keys back to the same call.
    Scheme 2 hashes the model's position in cfg["models"] too, so two entries for one model can never share call_ids
    (config_guard also rejects such duplicates). Scheme 1 (runs from 21a6672 to 0d9f54b) is kept only so those runs resume."""
    parts = (run_id, m["vendor"], m["model"], m.get("effort"), setup, app["profile_id"], app["variant"], app.get("variant_id"), rep)
    if scheme >= 2:
        if model_index is None: raise ValueError("sync_call_id scheme 2 needs model_index")
        parts = parts + (f"model_index={model_index}",)
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]

def duplicate_models(cfg):
    """(vendor, model, effort, emulate) entries that occur more than once in cfg["models"] (emulate: mock stand-ins for
    different vendors are distinct; it is None for real vendors)."""
    seen, dup = set(), []
    for m in cfg.get("models") or []:
        k = (m.get("vendor"), m.get("model"), m.get("effort"), m.get("emulate"))
        if k in seen and k not in dup: dup.append(k)
        seen.add(k)
    return dup

def load_attempt_journal(rd):
    """{(call_id, attempt): CallResult fields} from raw/attempts_journal.jsonl (a torn last line is ignored: that attempt is redone)."""
    out, p = {}, os.path.join(rd, "raw", "attempts_journal.jsonl")
    for line in (open(p) if os.path.exists(p) else []):
        try: e = json.loads(line)
        except ValueError: break
        out[(e["call_id"], e["attempt"])] = e["result"]
    return out

def call_with_retry(rec, client, guard, m, setup, sysm, user, tool_fn, max_out, journal=None, replay=None):
    """15.9: at most MAX_ATTEMPTS (=2) attempts, identical prompt/settings. Retry triggers: truncation (max_tokens) or failed validation.
    The retry is budget-checked before it is sent. A call that is still invalid after attempt 2 is excluded (parse_ok False).
    Ledger idempotency: each attempt's result is journaled (journal = open file) BEFORE its spend is recorded under the key
    (call_id, attempt, round=None, batch_id=None); on resume an attempt found in `replay` is reused instead of re-sent, and
    re-recording its key is a no-op, so a crash between the two never bills or redoes it twice."""
    first = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1: guard.check(m["vendor"], m["model"], len(sysm + user) // 3, max_out, 2 if setup == 2 else 1, setup=setup)
        if hasattr(client, "attempt"): client.attempt = attempt
        if replay and (rec["call_id"], attempt) in replay:
            res = CallResult(**replay[(rec["call_id"], attempt)])
        else:
            try:
                res = client.complete(sysm, user, TOOL_SPEC if setup == 2 else None, tool_fn if setup == 2 else None, setup=setup)
            except ToolArgumentError as e:   # Reviewer cond. (b): an answered attempt with bad tool args -> tool_args_invalid stop
                u = e.usage
                res = CallResult("", None, None, u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("reasoning_tokens"), e.calls, u.get("model_version"))
            if journal is not None:
                journal.write(json.dumps({"call_id": rec["call_id"], "attempt": attempt, "result": _clean({**res.__dict__, "raw": None})}, default=str) + "\n"); journal.flush()
        cost = guard.record(m["vendor"], m["model"], res.input_tokens, res.output_tokens, rec["call_id"],
                            key={"attempt": attempt, "round": None, "batch_id": None})
        r = finish_record(dict(rec), res, cost); r["attempt"] = attempt
        if first: r.update(first)
        if setup == 2:   # Reviewer cond. 1: the tool rule holds on EVERY attempt; a violation stops the run, no retry
            tr = tool_rule(r["tool_calls"])
            if attempt == 1: fa_ok = tr is None
            r["first_attempt_forced_call_ok"] = fa_ok
            if tr: r["s2_compliance"] = tr; r["tool_rule_attempt"] = attempt; return r
        reason = retry_reason(r["truncated"], r["parsed"], r.get("refusal"))
        if reason is None or attempt == MAX_ATTEMPTS: return r
        first = {"retry_reason": reason, "original_error": {"max_tokens": "max_tokens", "refusal": f"refusal: {r.get('refusal')}"}.get(reason) or "; ".join(r["parsed"]["validation_errors"]),
                 "attempt1_response": r["response"], "attempt1_cost_usd": cost, "attempt1_tool_calls": r["tool_calls"]}
    return r

def retest_cap_meta(cfg):
    """Reviewer cond. (2): the minipilot_s2_retest cap basis, per vendor max(pilot basis, S2_CORE_P95), for run_meta."""
    from .budget import retest_cap_basis
    return retest_cap_basis([m for m in cfg["models"] if m["vendor"] != "mock"] or None, cfg.get("judge") if (cfg.get("judge") or {}).get("vendor") not in (None, "mock") else None) \
        if any(m["vendor"] != "mock" for m in cfg["models"]) else retest_cap_basis()

def _sync_items(cfg, limit, variants_csv):
    items = load_items(cfg, variants_csv)
    if limit: items = items[items.profile_id.isin(items.profile_id.unique()[:limit])]
    return items

def _paid_guard(cfg, allow_paid):
    config_guard(cfg, allow_paid)
    paid = [m for m in cfg["models"] + ([cfg["judge"]] if cfg.get("judge") else []) if m["vendor"] != "mock"]
    if paid and not allow_paid:
        raise SystemExit("config contains paid vendors; pass --allow-paid to make real API calls")

def resume(rd, allow_paid=False):
    """Continue a sync run that crashed: calls already in calls.jsonl are skipped, journaled attempts are reused (not re-sent),
    and the ledger skips keys it already holds, so totals equal an uninterrupted run. Refuses after a compliance stop."""
    meta = json.load(open(os.path.join(rd, "run_meta.json"))); cfg = meta["config"]
    if meta.get("compliance_stop"): raise SystemExit(f"REFUSING resume: run stopped on setup-2 compliance ({meta['compliance_stop']})")
    if not os.path.exists(os.path.join(rd, "raw", "attempts_journal.jsonl")):
        # s2-retest fix 2: runs from before 21a6672 have random call_ids and no attempts journal; nothing would match, so a
        # resume would re-send and re-bill every call
        raise SystemExit("REFUSING resume: no raw/attempts_journal.jsonl (sync run from before 21a6672); its calls cannot be matched, "
                         "so a resume would re-send and re-bill them")
    _paid_guard(cfg, allow_paid)
    items = _sync_items(cfg, meta.get("limit"), meta.get("variants_csv"))
    meta.setdefault("resumes", []).append(dt.datetime.now().astimezone().isoformat())
    return _execute(cfg, rd, meta, items)

def run(cfg_path, allow_paid=False, out_root=None, limit=None, variants_csv=None, phase=None):
    cfg = yaml.safe_load(open(cfg_path))
    if phase: cfg["phase"] = phase                         # CLI --phase overrides the config
    _paid_guard(cfg, allow_paid)
    items = _sync_items(cfg, limit, variants_csv)
    chash = hashlib.sha256(open(cfg_path, "rb").read()).hexdigest()[:8]
    run_id = f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{chash}-{uuid.uuid4().hex[:6]}"
    rd = os.path.join(out_root or P("runs"), run_id); os.makedirs(rd)
    meta = {"run_id": run_id, "config_path": cfg_path, "config": cfg, "git_commit": git_commit(), "prompt_version": PROMPT_VERSION, "phase": cfg.get("phase"),
            "started": dt.datetime.now().astimezone().isoformat(), "pilot": bool(cfg.get("pilot")),
            "excluded_from_confirmatory": bool(cfg.get("pilot")), "n_items": len(items), "limit": limit, "variants_csv": variants_csv,
            "sync_call_id_scheme": SYNC_CALL_ID_SCHEME}
    if cfg.get("probe"): meta.update(probe=cfg["probe"], excluded_from_analysis=True)
    if cfg.get("phase") == "minipilot_s2_retest": meta["cap_basis"] = retest_cap_meta(cfg)   # Reviewer cond. (2)
    json.dump(meta, open(os.path.join(rd, "run_meta.json"), "w"), indent=2, default=str)
    return _execute(cfg, rd, meta, items)

def _execute(cfg, rd, meta, items):
    run_id = meta["run_id"]; prices = load_prices(P("configs", "prices.yaml"))
    cp = os.path.join(rd, "calls.jsonl")
    logged = {json.loads(l)["call_id"] for l in open(cp) if l.strip()} if os.path.exists(cp) else set()   # resume: already done
    replay = load_attempt_journal(rd)
    guard = BudgetGuard(cfg["budget_usd"], cfg.get("per_vendor_budget_usd", {}), prices, os.path.join(rd, "spend_ledger.jsonl"), phase=cfg.get("phase"))
    tool_fn = tool_fn_factory(); log = open(cp, "a", buffering=1); stopped = None; vendor_stops = {}; compliance_stop = None
    os.makedirs(os.path.join(rd, "raw"), exist_ok=True); raw = open(os.path.join(rd, "raw", "responses.jsonl"), "a", buffering=1)
    journal = open(os.path.join(rd, "raw", "attempts_journal.jsonl"), "a", buffering=1)
    try:
        for setup in cfg["setups"]:                       # setup order from config (3 first = top priority)
            for mi, m in enumerate(cfg["models"]):
                ckw = {"bias": m["bias"]} if m["vendor"] == "mock" and m.get("bias") else {}
                if m["vendor"] == "mock": ckw.update({k: m[k] for k in ("fail", "emulate") if m.get(k)})
                client = make_client(m["vendor"], m["model"], m.get("effort"), max_output_tokens=cfg["max_output_tokens"], **ckw)
                for rep in range(cfg["repeats"]):
                    for _, it in items.iterrows():
                        app = _clean(it.to_dict())
                        rec, sysm, user = build_record(cfg, run_id, meta["excluded_from_confirmatory"], m, setup, app, rep)
                        rec["call_id"] = sync_call_id(run_id, m, setup, app, rep, mi, meta.get("sync_call_id_scheme", 1))
                        if rec["call_id"] in logged: continue           # resume: finished before the crash
                        call_id = rec["call_id"]; toold = {"decision": rec["tool_decision"]}
                        if m["vendor"] in vendor_stops:        # K2: a vendor cap/balance hit stops only that vendor
                            rec["error"] = f"budget_stop: {vendor_stops[m['vendor']]}"; log.write(json.dumps(_clean(rec)) + "\n"); continue
                        try:
                            guard.check(m["vendor"], m["model"], len(sysm + user) // 3, cfg["max_output_tokens"], 2 if setup == 2 else 1, setup=setup)
                        except BudgetExceeded as e:
                            rec["error"] = f"budget_stop: {e}"; log.write(json.dumps(_clean(rec)) + "\n")
                            if e.scope == "vendor": vendor_stops[m["vendor"]] = str(e); continue
                            stopped = str(e); raise
                        t0 = time.time()
                        try:
                            rec = call_with_retry(rec, client, guard, m, setup, sysm, user, tool_fn, cfg["max_output_tokens"], journal, replay)
                        except BudgetExceeded as e:
                            rec["error"] = f"budget_stop (retry): {e}"; log.write(json.dumps(_clean(rec), default=str) + "\n")
                            if e.scope == "vendor": vendor_stops[m["vendor"]] = str(e); continue
                            stopped = str(e); raise
                        except Exception as e:
                            rec["error"] = f"{type(e).__name__}: {str(e)[:500]}"
                        rec["latency_s"] = round(time.time() - t0, 3)
                        nc = s2_compliance(rec) if setup == 2 else None    # 15.9.1 item 3: final record, i.e. after the single retry
                        for a, txt in ([(1, rec["attempt1_response"])] if rec.get("attempt1_response") is not None else []) + ([(rec.get("attempt", 1), rec.get("response"))] if "response" in rec else []):
                            raw.write(json.dumps({"call_id": call_id, "attempt": a, "response": txt}) + "\n")
                        log.write(json.dumps(_clean(rec), default=str, allow_nan=False) + "\n")
                        if nc:
                            compliance_stop = {"reason": nc, "call_id": call_id, "vendor": m["vendor"], "model": m["model"], "attempt": rec.get("attempt", 1)}
                            raise S2ComplianceStop(f"setup-2 compliance stop: {nc} ({m['vendor']}/{m['model']}, call {call_id})")
    except BudgetExceeded:
        pass
    except S2ComplianceStop:
        pass
    finally:
        log.close(); raw.close(); journal.close()
        meta["error_counts"] = error_counts([json.loads(l) for l in open(cp) if l.strip()]) if os.path.exists(cp) else None   # cond. (b)
        meta.update(finished=dt.datetime.now().astimezone().isoformat(), budget_stop=stopped, vendor_budget_stops=vendor_stops, compliance_stop=compliance_stop, spent_usd=round(guard.spent, 6), spent_by_vendor=guard.by_vendor)
        json.dump(meta, open(os.path.join(rd, "run_meta.json"), "w"), indent=2, default=str)
    return rd

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True); ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--estimate-only", action="store_true"); ap.add_argument("--batch", action="store_true"); ap.add_argument("--limit", type=int)
    ap.add_argument("--phase", help="budget phase (smoke|minipilot|main); overrides the config's phase")
    a = ap.parse_args()
    if a.estimate_only:
        cfg = yaml.safe_load(open(a.config)); items = load_items(cfg)
        rows = estimate(cfg, load_prices(P("configs", "prices.yaml")), items.profile_id.nunique(), len(cfg["variants"]), batch=a.batch)
        df = pd.DataFrame(rows); print(df.to_string(index=False)); print("TOTAL USD", round(df.usd.sum(), 2)); return
    print(run(a.config, a.allow_paid, limit=a.limit, phase=a.phase))

if __name__ == "__main__":
    main()
