"""Smoke test 2 for 15.9: setups 1 and 2 (sync), 4 profiles x 1 variant (base) x 2 setups = 8 calls per vendor, 24 total.
Exercises: OpenAI strict function tool + parallel_tool_calls:false; the setup-2 tool-first line (SYSTEM_SETUP2) on Haiku
with thinking off (effort none -> thinking disabled); Gemini's two-round flow (final_turn: tools in round 1, schema on round 2).
  python scripts/smoke2_setups12.py                       # offline: mock emulating each vendor (free; default)
  python scripts/smoke2_setups12.py --live --dry-run      # NO network, no clients: planned calls, request checks, worst case
  python scripts/smoke2_setups12.py --live --allow-paid   # REAL calls (needs explicit authorization)
Caps: phase "smoke" (budget.PHASE_CAPS['smoke'] = $1.00 total, $0.30/vendor), run total $1.00, cumulative $200 ledger and
vendor balances; every call and retry is checked BEFORE it is sent. Summary: per vendor x setup tool-call rate, validity,
retries, schema_mode / fallbacks, whether Gemini's round 2 accepted the schema, and (descriptive) model codes the
factor points shown in the prompt do not support (llmbias.code_support). No effect estimates."""
import argparse, json, os, sys, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from llmbias.output_schema import setup_summary, json_schema
from llmbias.code_support import code_support_report
from llmbias.runner import run
N_PROFILES = 4
VARIANTS = ["base"]
SETUPS = [2, 1]                       # setup 2 first: it is the point of this smoke
PER_VENDOR = N_PROFILES * len(VARIANTS) * len(SETUPS)
SMOKE2_TOTAL_CAP_USD = 1.00
SMOKE2_VENDOR_CAP_USD = {"openai": 0.30, "anthropic": 0.30, "google": 0.30, "mock": 1.00}
VENDORS = ("openai", "anthropic", "google")
LIVE = [{"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}, {"vendor": "anthropic", "model": "claude-haiku-5-5", "effort": "none"},
        {"vendor": "google", "model": "gemini-3.8-flash", "effort": "none"}]
EXPECT_MODES = {(v, 1): {"native"} for v in VENDORS} | {("openai", 2): {"native_combined"}, ("anthropic", 2): {"native_combined"},
                                                         ("google", 2): {"two_round_schema", "unconstrained_no_tool"}}

def build_cfg(live, fail=None):
    base = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "configs", "minipilot_haiku_batch.yaml")))
    models = LIVE if live else [{"vendor": "mock", "model": f"mock-{v}", "effort": "none", "emulate": v, **({"fail": fail} if fail else {})} for v in VENDORS]
    cfg = dict(base, mode="sync", setups=SETUPS, repeats=1, variants=VARIANTS, models=models, judge={"vendor": "mock", "model": "mock-judge"},
               phase="smoke", budget_usd=SMOKE2_TOTAL_CAP_USD, per_vendor_budget_usd=SMOKE2_VENDOR_CAP_USD, pilot=True,
               google_tools_schema_mode="final_turn", smoke_label="smoke2_setups12")
    assert cfg["budget_usd"] <= 1.00 and cfg["phase"] == "smoke"
    return cfg

def request_checks(cfg):
    """Offline: build each vendor's real request body for one setup-2 item and confirm the features this smoke must exercise."""
    from llmbias import batch as B
    from llmbias.runner import build_record, load_items, _clean
    from llmbias.prompts import TOOL_FIRST_LINE
    it0 = load_items(cfg).iloc[0]; out = {}
    for m in cfg["models"]:
        v = m.get("emulate") or m["vendor"]
        rec, sysm, user = build_record(cfg, "chk", True, m, 2, _clean(it0.to_dict()), 0)
        it = {"model": m["model"], "system": sysm, "user": user, "max_out": cfg["max_output_tokens"], "effort": m.get("effort"), "tools": True,
              "history": None, "round": 1, "setup": 2, "google_mode": cfg["google_tools_schema_mode"]}
        c = {"tool_first_line_in_system": TOOL_FIRST_LINE in sysm}
        if v == "openai":
            b = B.OpenAIBackend.body(None, it); t = b["tools"][0]
            c.update(strict_tool=t.get("strict") is True and t["parameters"].get("additionalProperties") is False, parallel_tool_calls_false=b.get("parallel_tool_calls") is False,
                     strict_text_format=b["text"]["format"]["strict"] is True)
        elif v == "anthropic":
            b = B.AnthropicBackend.body(None, it)
            c.update(thinking_disabled=b.get("thinking") == {"type": "disabled"}, effort_none=m.get("effort") == "none", output_config=bool(b.get("output_config")))
        else:
            b1 = B.GoogleBackend.body(None, it)["config"]; b2 = B.GoogleBackend.body(None, {**it, "round": 2})["config"]
            c.update(round1_tools_no_schema=bool(b1.get("tools")) and "response_json_schema" not in b1,
                     round2_schema=b2.get("response_json_schema") == json_schema(2) and b2.get("response_mime_type") == "application/json",
                     round2_function_calling_none=b2["tool_config"]["function_calling_config"]["mode"] == "NONE")
        c["ok"] = all(c.values()); out[v] = c
    return out

def dry_run(cfg):
    """No network, no clients: the exact planned records, request checks, and worst case per vendor.
    worst_basis_usd = the guard's own per-call reservation (p95 x1.25, Google x2, setup 2 both rounds; configs/token_basis.yaml);
    worst_maxout_usd = a harder bound: prompt chars/3 (+ tool overhead) in and max_output_tokens out, x2 rounds on setup 2.
    Both are shown with one retry on every call (x2)."""
    import pandas as pd
    from llmbias.runner import build_record, load_items, config_guard, _clean
    from llmbias.budget import load_prices, call_cost, call_worst, PHASE_CAPS, cumulative_spend, VENDOR_BALANCE_USD, CUMULATIVE_CAP_USD
    from llmbias.build_data import P
    config_guard(cfg, allow_paid=False)
    items = load_items(cfg); items = items[items.profile_id.isin(items.profile_id.unique()[:N_PROFILES])]
    prices = load_prices(P("configs", "prices.yaml")); rows = []
    ov = cfg.get("est_tokens", {}).get("tool_overhead", {})
    for setup in cfg["setups"]:
        for m in cfg["models"]:
            for _, it in items.iterrows():
                rec, sysm, user = build_record(cfg, "dry", True, m, setup, _clean(it.to_dict()), 0)
                est_in = len(sysm + user) // 3; r = 2 if setup == 2 else 1
                rows.append({"vendor": m["vendor"], "model": m["model"], "setup": setup, "profile_id": rec["profile_id"],
                             "worst_basis_usd": call_worst(prices, m["vendor"], m["model"], setup, est_in, cfg["max_output_tokens"], r),
                             "worst_maxout_usd": call_cost(prices, m["vendor"], m["model"], (est_in + (ov.get(m["vendor"], 0) if setup == 2 else 0)) * r, cfg["max_output_tokens"] * r)})
    df = pd.DataFrame(rows)
    by = df.groupby(["vendor", "model"]).agg(calls=("setup", "size"), worst_basis_usd=("worst_basis_usd", "sum"), worst_maxout_usd=("worst_maxout_usd", "sum")).reset_index()
    by["worst_basis_with_retry_usd"] = by.worst_basis_usd * 2; by["worst_maxout_with_retry_usd"] = by.worst_maxout_usd * 2
    pc = PHASE_CAPS["smoke"]; cum = cumulative_spend()
    by["vendor_cap_usd"] = [min(cfg["per_vendor_budget_usd"].get(v, cfg["budget_usd"]), pc.get(v, float("inf")), VENDOR_BALANCE_USD.get(v, float("inf")) - cum.get(v, 0)) for v in by.vendor]
    rep = {"planned_calls": len(df), "per_vendor_x_setup": df.groupby(["vendor", "setup"]).size().rename("n").reset_index().to_dict("records"),
           "total_cap_usd": min(cfg["budget_usd"], pc["total"], CUMULATIVE_CAP_USD - sum(cum.values())), "cumulative_logged_usd": round(sum(cum.values()), 4),
           "per_vendor": by.round(5).to_dict("records"),
           "worst_basis_total_with_retry_usd": round(float(df.worst_basis_usd.sum() * 2), 5),
           "worst_maxout_total_with_retry_usd": round(float(df.worst_maxout_usd.sum() * 2), 5),
           "request_checks": request_checks(cfg)}
    rep["fits_caps"] = bool(rep["worst_maxout_total_with_retry_usd"] <= rep["total_cap_usd"] and (by.worst_maxout_with_retry_usd <= by.vendor_cap_usd).all())
    rep["request_checks_ok"] = all(c["ok"] for c in rep["request_checks"].values())
    print(json.dumps(rep, indent=1, default=str)); return rep["fits_caps"] and rep["request_checks_ok"], rep, None

def check(calls):
    """Per vendor x setup: n, tool-call rate (setup 2), validity, retries, schema modes / fallbacks, Gemini round-2 acceptance,
    max tool calls in one call (parallel_tool_calls:false -> <= 1 for OpenAI). PASS needs n, attempts <= 2, valid >= n-1, expected modes."""
    S = {(d["vendor"], d["setup"]): d for d in setup_summary(calls)}; ok = True; rep = []
    CS = {(d["vendor"], d["setup"]): d for d in code_support_report(calls)}
    for v in VENDORS:
        for s in sorted(SETUPS):
            d = S.get((v, s), {"vendor": v, "setup": s, "n": 0, "schema_mode": {}, "valid": 0})
            cs = [c for c in calls if (c.get("schema_vendor") or c.get("vendor")) == v and c.get("setup") == s]
            n_exp = N_PROFILES * len(VARIANTS)
            a = {"vendor": v, "setup": s, "n": d["n"], "tool_call_rate": d.get("tool_call_rate"), "valid": d["valid"], "valid_rate": d.get("valid_rate"),
                 "retried": d.get("retried", 0), "retry_reasons": d.get("retry_reasons", {}), "schema_mode": d["schema_mode"], "fallback": d.get("fallback"),
                 "refusal": d.get("refusal", 0), "max_tokens": d.get("max_tokens", 0), "budget_stop": d.get("budget_stop", 0), "api_error": d.get("api_error", 0),
                 "errors": d.get("errors", {}), "max_tool_calls_per_call": max([len(c.get("tool_calls") or []) for c in cs] or [0])}
            if (v, s) == ("google", 2):
                a.update(round2_schema_n=d.get("round2_schema_n"), round2_schema_accepted=d.get("round2_schema_accepted"),
                         round2_schema_accept_rate=d.get("round2_schema_accept_rate"))
            a["PASS"] = bool(d["n"] == n_exp and all(c.get("attempt", 1) <= 2 for c in cs) and d["valid"] >= n_exp - 1
                             and set(d["schema_mode"]) <= EXPECT_MODES[(v, s)] and (v != "openai" or a["max_tool_calls_per_call"] <= 1))
            cs_row = CS.get((v, s))   # descriptive, packet-only; not a PASS criterion
            a["code_support"] = {k: cs_row[k] for k in ("deny_calls", "codes", "supported", "unsupported", "not_factor_mapped", "calls_with_unsupported", "unsupported_by_code")} if cs_row else None
            ok &= a["PASS"]; rep.append(a)
    return ok, rep

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--live", action="store_true"); ap.add_argument("--allow-paid", action="store_true"); ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true", help="no network, no clients: planned calls, request checks, worst case")
    ap.add_argument("--fail", help="mock failure injection JSON")
    a = ap.parse_args(argv)
    cfg = build_cfg(a.live, json.loads(a.fail) if a.fail else None)
    if a.dry_run: return dry_run(cfg)
    if a.live and not a.allow_paid: raise SystemExit("live smoke2 makes paid calls; pass --allow-paid (explicit authorization required)")
    out = a.out or tempfile.mkdtemp(prefix="smoke2-"); os.makedirs(out, exist_ok=True); cp = os.path.join(out, "smoke2.yaml"); yaml.safe_dump(cfg, open(cp, "w"))
    rd = run(cp, allow_paid=a.allow_paid, out_root=out, limit=N_PROFILES)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    ok, rep = check(calls); json.dump({"run_dir": rd, "pass": ok, "by_vendor_setup": rep}, open(os.path.join(rd, "smoke2_summary.json"), "w"), indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str)); print("run_dir:", rd); print("SMOKE2", "PASS" if ok else "FAIL")
    return ok, rep, rd

if __name__ == "__main__":
    sys.exit(0 if main()[0] else 1)
