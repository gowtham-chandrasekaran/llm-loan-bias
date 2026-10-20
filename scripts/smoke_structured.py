"""Smoke test for 15.9 structured output (plan: 36 calls = 12 per vendor, setup 3, sync, 1 profile-variant set).
  python scripts/smoke_structured.py                 # offline: mock emulating each vendor (free; default)
  python scripts/smoke_structured.py --live --dry-run       # NO network: planned 36 calls + worst-case cost per vendor
  python scripts/smoke_structured.py --live --allow-paid   # REAL calls (needs explicit authorization; NOT run on output-robustness)
Caps: whole smoke run SMOKE_TOTAL_CAP_USD = $1.00 (BudgetGuard total cap) and per vendor min(SMOKE_VENDOR_CAP_USD, budget.PHASE_CAPS['smoke'], vendor cumulative balance); cumulative $200 ledger;
every call (and its retry) is checked against them BEFORE it is sent.
Per-vendor assertions: (1) native schema mode used on every final answer, (2) attempt <= 2 for every call,
(3) every attempt-1 response that is valid parses as the schema's exact field set, (4) >= 11/12 valid after the single retry."""
import argparse, json, os, sys, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from llmbias.output_schema import json_schema
from llmbias.runner import run
PER_VENDOR = 12
SMOKE_TOTAL_CAP_USD = 1.00
SMOKE_VENDOR_CAP_USD = {"openai": 0.30, "anthropic": 0.30, "google": 0.30, "mock": 1.00}
EXPECT = {"openai": "text.format json_schema strict", "anthropic": "output_config.format json_schema", "google": "response_mime_type + response_json_schema"}
LIVE = [{"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}, {"vendor": "anthropic", "model": "claude-haiku-5-5", "effort": "none"},
        {"vendor": "google", "model": "gemini-3.8-flash", "effort": "none"}]

def check(calls, live):
    rep, ok = {}, True
    for v in EXPECT:
        cs = [c for c in calls if c.get("schema_vendor") == v]
        modes = {c.get("schema_mode") for c in cs}
        valid = sum(bool((c.get("parsed") or {}).get("valid")) for c in cs)
        fields_ok = all(set(json.loads(c["response"])) == set(json_schema(3)["properties"]) for c in cs if (c.get("parsed") or {}).get("valid") and c.get("attempt") == 1)
        a = {"n": len(cs), "n_ok": len(cs) == PER_VENDOR, "schema_mode_ok": modes == {"native"},   # setup 3: native schema, no tools
             "attempts_le_2": all(c.get("attempt", 1) <= 2 for c in cs), "exact_fields": fields_ok, "valid_after_retry": valid, "valid_ok": valid >= PER_VENDOR - 1,
             "retried": sum(c.get("attempt") == 2 for c in cs)}
        a["PASS"] = all(a[k] for k in ("n_ok", "schema_mode_ok", "attempts_le_2", "exact_fields", "valid_ok")); ok &= a["PASS"]; rep[v] = a
    return ok, rep

def dry_run(cfg):
    """Builds the exact 36 records the run would send (same build_record, same items) without creating any client."""
    from llmbias.runner import build_record, load_items, config_guard, _clean
    from llmbias.budget import load_prices, call_cost
    from llmbias.build_data import P
    config_guard(cfg, allow_paid=False)
    items = load_items(cfg); items = items[items.profile_id.isin(items.profile_id.unique()[:3])]
    prices = load_prices(P("configs", "prices.yaml")); rows = []
    for setup in cfg["setups"]:
        for m in cfg["models"]:
            for rep in range(cfg["repeats"]):
                for _, it in items.iterrows():
                    rec, sysm, user = build_record(cfg, "dry", True, m, setup, _clean(it.to_dict()), rep)
                    w = call_cost(prices, m["vendor"], m["model"], len(sysm + user) // 3, cfg["max_output_tokens"], batch=False)
                    rows.append({"vendor": m["vendor"], "model": m["model"], "setup": setup, "profile_id": rec["profile_id"], "variant": rec["variant"], "worst_usd": w})
    import pandas as pd
    df = pd.DataFrame(rows)
    by = df.groupby(["vendor", "model"]).agg(calls=("worst_usd", "size"), worst_usd=("worst_usd", "sum")).reset_index()
    by["worst_with_one_retry_usd"] = by.worst_usd * 2
    from llmbias.budget import PHASE_CAPS, cumulative_spend, VENDOR_BALANCE_USD, CUMULATIVE_CAP_USD
    pc = PHASE_CAPS["smoke"]; cum = cumulative_spend()
    by["vendor_cap_usd"] = [min(cfg["per_vendor_budget_usd"].get(v, cfg["budget_usd"]), pc.get(v, float("inf")), VENDOR_BALANCE_USD.get(v, float("inf")) - cum.get(v, 0)) for v in by.vendor]
    rep = {"planned_calls": len(df), "total_cap_usd": min(cfg["budget_usd"], pc["total"], CUMULATIVE_CAP_USD - sum(cum.values())),
           "cumulative_logged_usd": round(sum(cum.values()), 4), "per_vendor": by.round(5).to_dict("records"),
           "worst_total_usd": round(float(df.worst_usd.sum()), 5), "worst_total_with_retries_usd": round(float(df.worst_usd.sum() * 2), 5)}
    rep["fits_caps"] = bool(rep["worst_total_with_retries_usd"] <= rep["total_cap_usd"] and (by.worst_with_one_retry_usd <= by.vendor_cap_usd).all())
    print(df.to_string(index=False)); print(json.dumps(rep, indent=1)); return rep["fits_caps"], rep, None

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--live", action="store_true"); ap.add_argument("--allow-paid", action="store_true"); ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true", help="no network, no clients: print planned calls and worst-case cost")
    ap.add_argument("--fail", help="mock failure injection JSON, e.g. '{\"p\":0.3,\"kind\":\"empty_letter\"}'")
    a = ap.parse_args(argv)
    base = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "configs", "minipilot_haiku_batch.yaml")))
    fail = json.loads(a.fail) if a.fail else None
    models = LIVE if a.live else [{"vendor": "mock", "model": f"mock-{v}", "effort": "none", "emulate": v, **({"fail": fail} if fail else {})} for v in EXPECT]
    cfg = dict(base, mode="sync", setups=[3], repeats=1, variants=["base", "name_b", "zip_b", "anchor"], models=models, **({} if a.live else {"judge": {"vendor": "mock", "model": "mock-judge"}}),
               phase="smoke", budget_usd=SMOKE_TOTAL_CAP_USD, per_vendor_budget_usd=SMOKE_VENDOR_CAP_USD, pilot=True)
    assert cfg["budget_usd"] <= 1.00, "smoke run must be capped at $1"
    if a.dry_run: return dry_run(cfg)
    out = a.out or tempfile.mkdtemp(); cp = os.path.join(out, "smoke.yaml"); yaml.safe_dump(cfg, open(cp, "w"))
    rd = run(cp, allow_paid=a.allow_paid, out_root=out, limit=3)   # 3 profiles x 4 variants = 12 per vendor
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    ok, rep = check(calls, a.live); print(json.dumps(rep, indent=1)); print("SMOKE", "PASS" if ok else "FAIL")
    return ok, rep, rd

if __name__ == "__main__":
    sys.exit(0 if main()[0] else 1)
