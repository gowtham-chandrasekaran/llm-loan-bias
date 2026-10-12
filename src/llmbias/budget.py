import json, os, threading, yaml

class BudgetExceeded(Exception):
    pass

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

class BudgetGuard:
    """Hard stop: refuses a call if spent + worst-case cost of that call would exceed the total or per-vendor cap.
    Worst case = estimated input tokens + max_output_tokens (x2 if a tool round-trip is possible). Ledger persisted."""
    def __init__(self, cap, per_vendor, prices, ledger_path):
        self.cap, self.per_vendor, self.prices, self.ledger_path = cap, per_vendor, prices, ledger_path
        self.lock = threading.Lock()
        self.spent, self.by_vendor = 0.0, {}
        if os.path.exists(ledger_path):
            with open(ledger_path) as f:
                for line in f:
                    r = json.loads(line); self.spent += r["cost"]; self.by_vendor[r["vendor"]] = self.by_vendor.get(r["vendor"], 0) + r["cost"]

    def check(self, vendor, model, est_in, max_out, rounds=1):
        worst = call_cost(self.prices, vendor, model, est_in * rounds, max_out * rounds)
        with self.lock:
            if self.spent + worst > self.cap:
                raise BudgetExceeded(f"total cap ${self.cap:.2f}: spent ${self.spent:.4f} + worst ${worst:.4f}")
            vcap = self.per_vendor.get(vendor, self.cap)
            if self.by_vendor.get(vendor, 0) + worst > vcap:
                raise BudgetExceeded(f"{vendor} cap ${vcap:.2f}: spent ${self.by_vendor.get(vendor,0):.4f} + worst ${worst:.4f}")

    def record(self, vendor, model, in_tok, out_tok, call_id):
        c = call_cost(self.prices, vendor, model, in_tok, out_tok)
        with self.lock:
            self.spent += c; self.by_vendor[vendor] = self.by_vendor.get(vendor, 0) + c
            with open(self.ledger_path, "a") as f:
                f.write(json.dumps({"call_id": call_id, "vendor": vendor, "model": model, "in": in_tok, "out": out_tok, "cost": c}) + "\n")
        return c

def estimate(cfg, prices, n_profiles, n_variants, batch=False, tokens=None):
    """Pre-run estimate. Returns list of rows (one per model x setup + judge) with calls and USD."""
    t = tokens or cfg["est_tokens"]; rows = []
    n_items = n_profiles * n_variants * cfg["repeats"]
    for m in cfg["models"]:
        for s in cfg["setups"]:
            rt = t["reasoning"].get(m.get("effort") or "none", 0)
            rounds = 2 if s == 2 else 1
            cin, cout = t["input"] * rounds + (300 if s == 2 else 0), t["output_visible"] + rt * rounds
            usd = n_items * call_cost(prices, m["vendor"], m["model"], cin, cout, batch)
            rows.append({"vendor": m["vendor"], "model": m["model"], "effort": m.get("effort"), "setup": s, "calls": n_items * rounds,
                         "in_tok_per_call": cin, "out_tok_per_call": cout, "usd": round(usd, 3)})
    j = cfg["judge"]; n_letters = n_items * len(cfg["models"]) * len(cfg["setups"])
    n_judge = n_letters * 2  # hedging rubric on every letter + mention judgment on detected spans (upper bound)
    usd = n_judge * call_cost(prices, j["vendor"], j["model"], t["judge_input"], t["judge_output"] + t["reasoning"].get(j.get("effort") or "none", 0), batch)
    rows.append({"vendor": j["vendor"], "model": j["model"], "effort": j.get("effort"), "setup": "judge", "calls": n_judge,
                 "in_tok_per_call": t["judge_input"], "out_tok_per_call": t["judge_output"], "usd": round(usd, 3)})
    return rows
