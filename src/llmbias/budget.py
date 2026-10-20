import json, os, threading, yaml

class BudgetExceeded(Exception):
    """scope: 'vendor' (a vendor's run/phase cap or cumulative balance -> stop that vendor only) or 'total'
    (run total or cumulative $200 cap -> stop everything)."""
    def __init__(self, msg, scope="total", vendor=None):
        super().__init__(msg); self.scope, self.vendor = scope, vendor

def load_prices(path):
    with open(path) as f:
        return yaml.safe_load(f)

def price(prices, vendor, model):
    try:
        return prices[vendor][model]
    except KeyError:
        raise KeyError(f"no price for {vendor}/{model}; add it to configs/prices.yaml before running")

def call_cost(prices, vendor, model, in_tok, out_tok, batch=False):
    p = price(prices, vendor, model); m = p.get("batch_discount", 1.0) if batch else 1.0
    return (in_tok * p["input"] + out_tok * p["output"]) / 1e6 * m

# ---- K1 spend caps (Oct 17 2026) -------------------------------------------------------------------------------------
# Phase caps = computed worst cases (docs/cost_haiku_swap.md @ cbb99c8: p95 tokens x1.25, Google promo price doubled,
# judge x3, +5% reruns). The judge (gpt-5-nano) is billed to OpenAI. Effective per-run vendor cap = min(config, phase cap).
PHASE_CAPS = {
    "smoke":     {"total": 1.00,  "openai": 0.30,  "anthropic": 0.30, "google": 0.30},
    "minipilot": {"total": 9.53,  "openai": 2.55,  "anthropic": 0.59, "google": 6.40},   # openai 2.2499 + judge 0.2932 = 2.5431 -> cent ceiling (K2; was 2.54 from rounded parts)
    "main":      {"total": 88.04, "openai": 23.50, "anthropic": 5.42, "google": 59.13},  # openai 20.80 + judge 2.70; smoke $0.08 excluded   # K2: google 59.1206 -> cent ceiling (was 59.12)
    "minipilot_pc": {"total": 3.41, "openai": 0.96, "anthropic": 0.26, "google": 2.21},   # 15.9(5b) planted check; rulings R6 (843fcabd): cent ceilings of the exact worst case 3.4099
}
# Cumulative caps across ALL runs, in a persistent ledger. Logged spend before this ledger existed (pilot run
# 20261012T143812-ff54324d-4745d6-batch, spend_ledger.jsonl) is seeded per vendor; total $10.58.
CUMULATIVE_CAP_USD = 200.0
CUMULATIVE_SEED = {"anthropic": 6.9397, "google": 2.0085, "openai": 1.6328}
VENDOR_BALANCE_USD = {"openai": 20.0, "anthropic": 20.0}     # cumulative per-vendor ceiling incl. seeded spend
HARD_VENDOR_CAPS = VENDOR_BALANCE_USD                         # backward-compatible name

# ---- K2: one token basis for caps, guard and plan (configs/token_basis.yaml = pilot actual per-call tokens) ------
_BASIS = {}
def token_basis():
    if not _BASIS:
        from .build_data import P
        _BASIS.update(yaml.safe_load(open(P("configs", "token_basis.yaml"))))
    return _BASIS

def call_worst(prices, vendor, model, setup, est_in, max_out, rounds=1, batch=False):
    """Per-call worst case on the SAME basis as the phase caps: p95 tokens x 1.25, Google price x2 (setup 2 = both
    rounds). Falls back to est_in + max_out (x rounds) when no basis exists (mock, judge, unknown setup)."""
    b = token_basis(); t = (b["tokens"].get(vendor) or {}).get(setup) if setup else None
    if t is None: return call_cost(prices, vendor, model, est_in * rounds, max_out * rounds, batch)
    m = b["worst_multiplier"]; c = call_cost(prices, vendor, model, t["inp95"] * m, t["out95"] * m, batch)
    return c * (b["worst_google_price_multiplier"] if vendor == "google" else 1.0)

def phase_projection(models, judge, n_items_by_setup, prices, case="worst", batch=True, judge_letters=None):
    """Projected cost per vendor on the cap basis (reproduces docs/cost_haiku_swap.md): case 'worst' = p95 x1.25,
    Google x2, +5% reruns, judge x3; 'expected' = mean tokens. n_items_by_setup: {setup: calls per vendor}."""
    b = token_basis(); out = {}
    for m in models:
        tot = 0.0
        for s, n in n_items_by_setup.items():
            t = b["tokens"][m["vendor"]][s]
            if case == "worst":
                c = call_worst(prices, m["vendor"], m["model"], s, 0, 0, 1, batch) * (1 + b["worst_rerun_margin"])
            else:
                c = call_cost(prices, m["vendor"], m["model"], t["inp"], t["out"] * (1.12 if (m["vendor"] == "google" and s == 1) else 1), batch)
            tot += n * c
        out[m["vendor"]] = out.get(m["vendor"], 0.0) + tot
    if judge:
        jb = b["judge_per_item"]; jm = b["worst_judge_multiplier"] if case == "worst" else 1.0
        n_letters = judge_letters if judge_letters is not None else sum(n_items_by_setup.values()) * len(models)
        # judge_per_item tokens are per judged letter (hedging + mention spans, pilot actual); one per decision letter
        out[judge["vendor"]] = out.get(judge["vendor"], 0.0) + n_letters * call_cost(prices, judge["vendor"], judge["model"], jb["input"], jb["output"], batch) * jm
    return out

def cumulative_ledger_path():
    from .build_data import P
    return os.environ.get("LLMBIAS_CUMULATIVE_LEDGER") or P("runs", "cumulative_spend_ledger.jsonl")

def cumulative_spend(path=None):
    by = dict(CUMULATIVE_SEED); path = path or cumulative_ledger_path()
    if os.path.exists(path):
        for line in open(path):
            r = json.loads(line); by[r["vendor"]] = by.get(r["vendor"], 0.0) + r["cost"]
    return by

class BudgetGuard:
    """Hard stop BEFORE a call/retry/batch chunk if its worst case would pass: the run total cap, the run vendor cap
    (min(config, phase cap)), the cumulative $200 cap (seed + persistent ledger + in-flight), or a vendor's cumulative
    balance (OpenAI $20, Anthropic $20). Mock spend is never written to the cumulative ledger."""
    def __init__(self, cap, per_vendor, prices, ledger_path, phase=None, cumulative_path=None):
        self.cap, self.per_vendor, self.prices, self.ledger_path = cap, per_vendor, prices, ledger_path
        self.phase = phase; self.pcaps = PHASE_CAPS.get(phase, {}) if phase else {}
        if phase and phase not in PHASE_CAPS: raise ValueError(f"unknown phase {phase!r}; one of {sorted(PHASE_CAPS)}")
        if self.pcaps: self.cap = min(self.cap, self.pcaps["total"])
        self.cum_path = cumulative_path or cumulative_ledger_path()
        self.lock = threading.Lock()
        self.spent, self.by_vendor = 0.0, {}
        if os.path.exists(ledger_path):
            with open(ledger_path) as f:
                for line in f:
                    r = json.loads(line); self.spent += r["cost"]; self.by_vendor[r["vendor"]] = self.by_vendor.get(r["vendor"], 0) + r["cost"]

    def vendor_cap(self, vendor):
        return min(self.per_vendor.get(vendor, self.cap), self.pcaps.get(vendor, float("inf")))

    def check(self, vendor, model, est_in, max_out, rounds=1, batch=False, committed=0.0, committed_vendor=0.0, setup=None):
        worst = call_worst(self.prices, vendor, model, setup, est_in, max_out, rounds, batch)
        return self.check_amount(vendor, worst, committed, committed_vendor)

    def check_amount(self, vendor, worst, committed=0.0, committed_vendor=0.0):
        """committed* = worst-case cost of batches already submitted but not yet settled."""
        with self.lock:
            if self.spent + committed + worst > self.cap:
                raise BudgetExceeded(scope="total", vendor=vendor, msg=f"total cap ${self.cap:.2f}: spent ${self.spent:.4f} + in-flight ${committed:.4f} + worst ${worst:.4f}")
            vcap = self.vendor_cap(vendor)
            if self.by_vendor.get(vendor, 0) + committed_vendor + worst > vcap:
                raise BudgetExceeded(scope="vendor", vendor=vendor, msg=f"{vendor} cap ${vcap:.2f}: spent ${self.by_vendor.get(vendor,0):.4f} + in-flight ${committed_vendor:.4f} + worst ${worst:.4f}")
            if vendor != "mock":
                cum = cumulative_spend(self.cum_path); tot = sum(cum.values())
                if tot + committed + worst > CUMULATIVE_CAP_USD:
                    raise BudgetExceeded(scope="total", vendor=vendor, msg=f"cumulative cap ${CUMULATIVE_CAP_USD:.2f}: logged ${tot:.4f} + in-flight ${committed:.4f} + worst ${worst:.4f}")
                bal = VENDOR_BALANCE_USD.get(vendor)
                if bal is not None and cum.get(vendor, 0) + committed_vendor + worst > bal:
                    raise BudgetExceeded(scope="vendor", vendor=vendor, msg=f"{vendor} cumulative balance ${bal:.2f}: logged ${cum.get(vendor,0):.4f} + in-flight ${committed_vendor:.4f} + worst ${worst:.4f}")
            return worst

    def record(self, vendor, model, in_tok, out_tok, call_id, batch=False):
        c = call_cost(self.prices, vendor, model, in_tok, out_tok, batch)
        with self.lock:
            self.spent += c; self.by_vendor[vendor] = self.by_vendor.get(vendor, 0) + c
            row = {"call_id": call_id, "vendor": vendor, "model": model, "in": in_tok, "out": out_tok, "batch": batch, "cost": c}
            with open(self.ledger_path, "a") as f: f.write(json.dumps(row) + "\n")
            if vendor != "mock":
                os.makedirs(os.path.dirname(self.cum_path), exist_ok=True)
                with open(self.cum_path, "a") as f: f.write(json.dumps({**row, "ledger": self.ledger_path, "phase": self.phase}) + "\n")
        return c

def estimate(cfg, prices, n_profiles, n_variants, batch=False, tokens=None, n_profiles_by_setup=None):
    """Pre-run estimate. Returns list of rows (one per model x setup + judge) with calls and USD."""
    t = tokens or cfg["est_tokens"]; rows = []
    n_items_all = n_profiles * n_variants * cfg["repeats"]; tot_items = 0
    for m in cfg["models"]:
        for s in cfg["setups"]:
            n_items = (n_profiles_by_setup or {}).get(s, n_profiles) * n_variants * cfg["repeats"]; tot_items += n_items
            rt = t["reasoning"].get(m.get("effort") or "none", 0)
            rounds = 2 if s == 2 else 1
            tool_over = t.get("tool_overhead", {}).get(m["vendor"], 300) if s == 2 else 0
            cin, cout = t["input"] * rounds + tool_over, t["output_visible"] + rt * rounds + (t.get("tool_call_output", 60) if s == 2 else 0)
            usd = n_items * call_cost(prices, m["vendor"], m["model"], cin, cout, batch)
            rows.append({"vendor": m["vendor"], "model": m["model"], "effort": m.get("effort"), "setup": s, "calls": n_items * rounds,
                         "in_tok_per_call": cin, "out_tok_per_call": cout, "usd": round(usd, 3)})
    j = cfg["judge"]; n_letters = tot_items
    n_judge = n_letters * 2  # hedging rubric on every letter + mention judgment on detected spans (upper bound)
    usd = n_judge * call_cost(prices, j["vendor"], j["model"], t["judge_input"], t["judge_output"] + t["reasoning"].get(j.get("effort") or "none", 0), batch)
    rows.append({"vendor": j["vendor"], "model": j["model"], "effort": j.get("effort"), "setup": "judge", "calls": n_judge,
                 "in_tok_per_call": t["judge_input"], "out_tok_per_call": t["judge_output"], "usd": round(usd, 3)})
    return rows
