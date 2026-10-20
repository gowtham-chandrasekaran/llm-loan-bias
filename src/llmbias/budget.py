import json, math, os, threading, yaml

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
    # 15.9.1 (logged sha256 5041167f) item 7: live probe, 2 profiles x 3 vendors x setup 2 x (sync + batch) = 12 calls, no judge.
    # Worst case on the cap basis = $0.0352 (sync 0.0234 at list price + batch 0.0117); cap set by the amendment.
    "minipilot_s2_probe": {"total": 0.15},
    # 15.9.1 item 4: setup-2 re-test, 25 pilot profiles x 8 variants x 3 repeats = 600 calls/vendor (1,800) + judge on 1,800 letters.
    # Same basis as the other caps (p95 tokens x1.25, Google price x2, +5% reruns, judge x3, cent ceiling); per vendor the
    # larger of the pilot token basis (configs/token_basis.yaml) and the core mini-pilot's setup-2 actuals (S2_CORE_P95 below):
    # openai max(0.9775 + judge 0.0974, 1.1387 + 0.0974) = 1.2361 -> 1.24; anthropic max(0.2879, 0.3857) -> 0.39;
    # google max(2.2497, 2.0781) -> 2.25; total 3.88.
    "minipilot_s2_retest": {"total": 3.88, "openai": 1.24, "anthropic": 0.39, "google": 2.25},
}
# Setup-2 per-call p95 tokens (both rounds) of the core mini-pilot run 20261018T124607-e5e8b45a-1a973e-batch (600 final
# records per vendor; operational usage only), used with the token basis for the minipilot_s2_retest caps.
S2_CORE_P95 = {"openai": {"inp95": 1978.0, "out95": 313.0}, "anthropic": {"inp95": 6040.15, "out95": 751.05},
               "google": {"inp95": 1758.0, "out95": 352.1}}
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

def call_worst_cap_basis(prices, vendor, model, setup, est_in, max_out, batch=False):
    """s2-retest fix 6: per-call worst case on the basis the minipilot_s2_retest caps use. Setup 2 = max(pilot token basis,
    S2_CORE_P95 core actuals), each at p95 x1.25 with Google price x2; otherwise call_worst. Used for batch in-flight
    reservations, so a compliance-stop reserve is never below the cap basis."""
    w = call_worst(prices, vendor, model, setup, est_in, max_out, 1, batch)
    b = token_basis(); t = S2_CORE_P95.get(vendor)
    if setup != 2 or t is None or (b["tokens"].get(vendor) or {}).get(2) is None: return w
    m = b["worst_multiplier"]
    core = call_cost(prices, vendor, model, t["inp95"] * m, t["out95"] * m, batch) * (b["worst_google_price_multiplier"] if vendor == "google" else 1.0)
    return max(w, core)

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

RETEST_DESIGN = {"calls_per_vendor": 600, "judged_letters": 1800}

def retest_cap_basis(models=None, judge=None, prices=None):
    """Reviewer cond. (2) on 91baba3: the minipilot_s2_retest cap basis, per vendor max(pilot token basis, S2_CORE_P95 core
    actuals) on the shared cap basis (p95 x1.25, Google price x2, +5% reruns, judge x3 billed to OpenAI, cent ceiling),
    600 setup-2 calls per vendor and 1,800 judged letters. Written to the re-test run_meta (sync and batch)."""
    from .build_data import P
    if models is None:
        cfg = yaml.safe_load(open(P("configs", "minipilot_haiku_batch.yaml"))); models, judge = cfg["models"], cfg.get("judge")
    pr = prices or load_prices(P("configs", "prices.yaml")); b = token_basis(); n = RETEST_DESIGN["calls_per_vendor"]
    pilot = phase_projection(models, judge, {2: n}, pr, judge_letters=RETEST_DESIGN["judged_letters"])
    jv = judge["vendor"] if judge else None
    jc = (RETEST_DESIGN["judged_letters"] * call_cost(pr, judge["vendor"], judge["model"], b["judge_per_item"]["input"], b["judge_per_item"]["output"], True)
          * b["worst_judge_multiplier"]) if judge else 0.0
    out = {}
    for m in models:
        v = m["vendor"]; t = S2_CORE_P95[v]; wm = b["worst_multiplier"]
        core = (n * call_cost(pr, v, m["model"], t["inp95"] * wm, t["out95"] * wm, True) * (b["worst_google_price_multiplier"] if v == "google" else 1.0)
                * (1 + b["worst_rerun_margin"]) + (jc if v == jv else 0.0))
        mx = max(pilot[v], core)
        out[v] = {"model": m["model"], "pilot_basis_usd": round(pilot[v], 4), "core_actuals_usd": round(core, 4), "basis_used": "core_actuals" if core > pilot[v] else "pilot_basis",
                  "max_usd": round(mx, 4), "cap_usd": math.ceil(round(mx * 100, 6)) / 100, "phase_cap_usd": PHASE_CAPS["minipilot_s2_retest"].get(v),
                  "core_p95_tokens": t, "includes_judge": v == jv}
    return {"phase": "minipilot_s2_retest", "rule": "per vendor max(pilot token basis, S2_CORE_P95 core setup-2 actuals); p95 x1.25, Google price x2, "
            "+5% reruns, judge x3 (OpenAI), cent ceiling", "design": dict(RETEST_DESIGN), "core_run": "20261018T124607-e5e8b45a-1a973e-batch",
            "by_vendor": out, "total_cap_usd": PHASE_CAPS["minipilot_s2_retest"]["total"],
            "matches_phase_caps": all(x["cap_usd"] == x["phase_cap_usd"] for x in out.values())}

def cumulative_ledger_path():
    from .build_data import P
    return os.environ.get("LLMBIAS_CUMULATIVE_LEDGER") or P("runs", "cumulative_spend_ledger.jsonl")

# ---- ledger idempotency --------------------------------------------------------------------------------------------
# A keyed row carries attempt / round / batch_id; its key is (call_id, attempt, round, batch_id). Setup-2 batch rounds 1
# and 2 share a call_id (custom_id) and stay distinct by round; a retry is a new attempt; a re-submitted batch has a new
# batch_id (a real, new charge). Sync rows have round = batch_id = None (one row per attempt, both tool rounds summed).
# A keyed row is written at most once per ledger; re-recording the same key is a no-op. Rows written before this fix
# (and unkeyed callers such as the sync judge, which bills several calls under one item_id) have no key and always count,
# so every existing ledger totals exactly as before.
KEY_FIELDS = ("attempt", "round", "batch_id")

def ledger_key(row):
    return (row["call_id"], row["attempt"], row["round"], row["batch_id"]) if "attempt" in row else None

def _cum_key(row):
    k = ledger_key(row)
    return None if k is None else (os.path.realpath(row.get("ledger") or ""),) + k

def read_ledger(path, keyfn=ledger_key):
    """Rows of a ledger with keyed duplicates dropped (first occurrence wins); unkeyed rows always kept."""
    rows, seen = [], set()
    if os.path.exists(path):
        for line in open(path):
            if not line.strip(): continue
            r = json.loads(line); k = keyfn(r)
            if k is not None:
                if k in seen: continue
                seen.add(k)
            rows.append(r)
    return rows, seen

def cumulative_spend(path=None):
    by = dict(CUMULATIVE_SEED); path = path or cumulative_ledger_path()
    for r in read_ledger(path, _cum_key)[0]:
        by[r["vendor"]] = by.get(r["vendor"], 0.0) + r["cost"]
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
        rows, self.keys = read_ledger(ledger_path)
        for r in rows:
            self.spent += r["cost"]; self.by_vendor[r["vendor"]] = self.by_vendor.get(r["vendor"], 0) + r["cost"]
        self._cum_keys = None                     # keyed rows of THIS run already in the cumulative ledger (loaded lazily)

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

    def _cum_seen(self):
        if self._cum_keys is None:
            me = os.path.realpath(self.ledger_path)
            self._cum_keys = {k[1:] for k in read_ledger(self.cum_path, _cum_key)[1] if k[0] == me}
        return self._cum_keys

    def record(self, vendor, model, in_tok, out_tok, call_id, batch=False, key=None):
        """key: {"attempt", "round", "batch_id"} -> idempotent on (call_id, attempt, round, batch_id): a key already in
        the run ledger (or, for paid vendors, in the cumulative ledger for this run) is not written or counted again.
        Each ledger is checked on its own, so a crash between the two appends is repaired on the re-record.
        Returns the cost either way. key=None: legacy unkeyed row (always written)."""
        c = call_cost(self.prices, vendor, model, in_tok, out_tok, batch)
        row = {"call_id": call_id, "vendor": vendor, "model": model, "in": in_tok, "out": out_tok, "batch": batch, "cost": c}
        return self._write(row, key)

    # ---- in-flight reserves at a batch compliance stop (Reviewer cond. 5; s2-retest fix 3) -----------------------------
    # Keys: reserve = ("reserve:<bid>", 0, "reserve", bid), release = ("release:<bid>", 0, "release", bid). Each ledger is
    # handled on its own: reserve() writes a ledger's reserve row unless that ledger already holds it, and nothing once either ledger holds the release;
    # release() writes a ledger's release row only if that ledger holds the reserve (and not yet the release), for exactly
    # the reserved amount. So any crash point (before/between/after the two appends, before the caller persists its flag)
    # nets to the actual spend in each ledger, and a release can never exist without its reserve.
    # Two hold types share this mechanism (s2-retest condition 2 adds the second):
    #   "reserve"     in-flight worst case of a batch at a compliance stop (kinds inflight_reserve / inflight_release)
    #   "placeholder" worst case of an ended (expired / failed / cancelled) batch whose results could not be fetched, so
    #                 its partial charges are not yet booked (kinds unfetched_placeholder / unfetched_release)
    HOLDS = {"reserve": ("reserve", "release", "inflight_reserve", "inflight_release"),
             "placeholder": ("placeholder", "placeholder_release", "unfetched_placeholder", "unfetched_release")}

    @staticmethod
    def _rkey(kind, bid): return (f"{kind}:{bid}", 0, kind, bid)

    def _reserve_amounts(self, path, cum, row_kind="inflight_reserve"):
        """{bid: held cost} of this run's hold rows of one kind in a ledger (run ledger, or this run's rows in the cumulative one)."""
        me = os.path.realpath(self.ledger_path); out = {}
        for r in read_ledger(path, _cum_key if cum else ledger_key)[0]:
            if r.get("kind") == row_kind and (not cum or os.path.realpath(r.get("ledger") or "") == me): out[r["batch_id"]] = r["cost"]
        return out

    def has_reserve(self, batch_id, hold="reserve"):
        """{ledger: bool}: does each ledger hold this batch's hold row?"""
        k = self._rkey(self.HOLDS[hold][0], batch_id)
        return {"run": k in self.keys, "cumulative": k in self._cum_seen()}

    def reserve(self, vendor, model, amount, batch_id, hold="reserve"):
        """Book a batch's worst case (keyed, idempotent) in the run ledger and, for paid vendors, the cumulative one."""
        hk, lk_kind, row_kind, _ = self.HOLDS[hold]
        row = {"call_id": f"{hk}:{batch_id}", "vendor": vendor, "model": model, "in": 0, "out": 0, "batch": True,
               "cost": float(amount), "kind": row_kind, "attempt": 0, "round": hk, "batch_id": batch_id}
        rk, lk = self._rkey(hk, batch_id), self._rkey(lk_kind, batch_id)
        with self.lock:
            if lk in self.keys or lk in self._cum_seen(): return 0.0          # already released (settled): never hold again
            if rk not in self.keys: self._put_run(row, rk)
            if vendor != "mock" and rk not in self._cum_seen(): self._put_cum(row, rk)
        return float(amount)

    def placeholder(self, vendor, model, amount, batch_id):
        return self.reserve(vendor, model, amount, batch_id, hold="placeholder")

    def release(self, vendor, model, batch_id, hold="reserve"):
        """Release a batch's hold, per ledger, only where that ledger holds it; amount = the held cost there.
        Returns {ledger: released amount}."""
        hk, lk_kind, row_kind, rel_kind = self.HOLDS[hold]
        rk, lk = self._rkey(hk, batch_id), self._rkey(lk_kind, batch_id); out = {}
        with self.lock:
            if rk in self.keys and lk not in self.keys:
                amt = self._reserve_amounts(self.ledger_path, False, row_kind)[batch_id]
                self._put_run(self._release_row(vendor, model, batch_id, amt, lk_kind, rel_kind), lk); out["run"] = amt
            if vendor != "mock" and rk in self._cum_seen() and lk not in self._cum_seen():
                amt = self._reserve_amounts(self.cum_path, True, row_kind)[batch_id]
                self._put_cum(self._release_row(vendor, model, batch_id, amt, lk_kind, rel_kind), lk); out["cumulative"] = amt
        return out

    def release_all(self, vendor, model, batch_id):
        """After a batch's actuals are booked: release its in-flight reserve and its unfetched placeholder, where held."""
        out = {}
        for hold in self.HOLDS:
            rel = self.release(vendor, model, batch_id, hold)
            if rel: out[hold] = rel
        return out

    @staticmethod
    def _release_row(vendor, model, bid, amt, lk_kind="release", rel_kind="inflight_release"):
        return {"call_id": f"{lk_kind}:{bid}", "vendor": vendor, "model": model, "in": 0, "out": 0, "batch": True,
                "cost": -float(amt), "kind": rel_kind, "attempt": 0, "round": lk_kind, "batch_id": bid}

    def _put_run(self, row, k):
        self.spent += row["cost"]; self.by_vendor[row["vendor"]] = self.by_vendor.get(row["vendor"], 0) + row["cost"]
        _append(self.ledger_path, row)
        if k is not None: self.keys.add(k)

    def _put_cum(self, row, k):
        os.makedirs(os.path.dirname(self.cum_path) or ".", exist_ok=True)
        _append(self.cum_path, {**row, "ledger": self.ledger_path if k is None else os.path.realpath(self.ledger_path), "phase": self.phase})
        if k is not None: self._cum_seen().add(k)

    def _write(self, row, key):
        c = row["cost"]; vendor = row["vendor"]
        with self.lock:
            k = None
            if key is not None:
                row.update({f: key.get(f) for f in KEY_FIELDS}); k = ledger_key(row)
            if k is None or k not in self.keys: self._put_run(row, k)
            if vendor != "mock" and (k is None or k not in self._cum_seen()): self._put_cum(row, k)
        return c

def _append(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")     # closed (flushed to the OS) before record() returns

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
    j = cfg.get("judge"); n_letters = tot_items
    if not j: return rows                     # no judge (15.9.1 probe)
    n_judge = n_letters * 2  # hedging rubric on every letter + mention judgment on detected spans (upper bound)
    usd = n_judge * call_cost(prices, j["vendor"], j["model"], t["judge_input"], t["judge_output"] + t["reasoning"].get(j.get("effort") or "none", 0), batch)
    rows.append({"vendor": j["vendor"], "model": j["model"], "effort": j.get("effort"), "setup": "judge", "calls": n_judge,
                 "in_tok_per_call": t["judge_input"], "out_tok_per_call": t["judge_output"], "usd": round(usd, 3)})
    return rows
