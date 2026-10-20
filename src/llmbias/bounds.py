"""Draft 15.9: missingness bounds for M2 ((2)) and M1a ((2a), extended per (5a)) and the (5b) 2% single-failure-reason check.
Input: a DataFrame from analysis.load_calls (decision/codes already None/[] for excluded calls; column exclusion_reason with
budget_stop | api_error | invalid_after_retry | max_tokens_after_retry | refusal_after_retry). Setup-3 contradictions are
valid calls and are never imputed."""
import numpy as np, pandas as pd

UNIT = ["vendor", "model", "profile_id"]
PAIRS = {"name": ("name_b", "name_w_placebo"), "zip": ("zip_b", "zip_placebo")}

def signflip(diffs: pd.DataFrame, n_perm, seed):
    """Paired sign-flip; one sign per PROFILE applied jointly to all its vendor cells. Statistic = sum of unit differences
    (= sum_v (b_v - c_v) for binary Y; proportional to the vendor-weighted mean when w_v ∝ units). p = (k+1)/(B+1), two-sided."""
    if diffs.empty: return None, None
    per_prof = diffs.groupby("profile_id").d.sum().to_numpy(float); obs = per_prof.sum()
    rng = np.random.default_rng(seed)
    k = sum(abs((np.where(rng.random(len(per_prof)) < 0.5, -1, 1) * per_prof).sum()) >= abs(obs) - 1e-12 for _ in range(n_perm))
    return float(obs), (k + 1) / (n_perm + 1)

def _summ(diffs, n_perm, seed):
    T, p = signflip(diffs, n_perm, seed)
    return {"n_units": int(len(diffs)), "delta": float(diffs.d.mean()) if len(diffs) else None, "T": T, "p": p,
            "by_vendor": diffs.groupby("vendor").d.agg(["size", "mean"]).round(6).reset_index().to_dict("records") if len(diffs) else []}

# ------------------------------------------------------------------------------------------------ M2
def m2_cells(df):
    """Setup 2, vendor x profile x variant: Y = 1 if the majority-of-3 (valid repeats) decision != tool decision; NaN if no 2-of-3."""
    d = df[df.setup == 2]
    def cell(g):
        dec = g.decision.dropna(); tool = g.tool_decision.iloc[0]
        vc = dec.value_counts(); maj = vc.index[0] if len(vc) and vc.iloc[0] >= 2 else None
        return pd.Series({"Y": np.nan if maj is None else float(maj != tool), "n_valid": len(dec), "n_missing_calls": int(g.exclusion_reason.notna().sum())})
    return d.groupby(UNIT + ["variant"]).apply(cell, include_groups=False).reset_index() if len(d) else pd.DataFrame()

def m2_bounds(df, proxy, n_perm=10000, seed=15865):
    """Observed (complete pairs), worst (missing proxy cell = override, missing placebo = no override) and best (reverse)."""
    vp, vpl = PAIRS[proxy]; c = m2_cells(df)
    if c.empty: return None
    w = c.pivot_table(index=UNIT, columns="variant", values="Y", aggfunc="first", dropna=False)
    nm = c.pivot_table(index=UNIT, columns="variant", values="n_missing_calls", aggfunc="first")
    if vp not in w or vpl not in w: return None
    w = w[[vp, vpl]].reset_index()
    miss_p, miss_pl = w[vp].isna(), w[vpl].isna()
    out = {"proxy": proxy, "n_missing_proxy_cells": int(miss_p.sum()), "n_missing_placebo_cells": int(miss_pl.sum()),
           "n_missing_calls_proxy": int(nm[vp].sum()), "n_missing_calls_placebo": int(nm[vpl].sum())}
    comp = w[~miss_p & ~miss_pl].assign(d=lambda t: t[vp] - t[vpl])
    out["observed"] = _summ(comp, n_perm, seed)
    for name, fp, fpl in (("worst", 1.0, 0.0), ("best", 0.0, 1.0)):
        t = w.assign(d=w[vp].fillna(fp) - w[vpl].fillna(fpl)); out[name] = _summ(t, n_perm, seed)
    out["any_missing"] = bool(miss_p.any() or miss_pl.any())
    out["conclusion_flips_within_bounds"] = _flips(out)
    return out

def _flips(out, alpha=0.05):
    sig = [(r["p"] is not None and r["p"] < alpha, np.sign(r["delta"] or 0)) for r in (out["observed"], out["worst"], out["best"])]
    return len(set(sig)) > 1

# ------------------------------------------------------------------------------------------------ M1a
def m1a_cells(df):
    """Setup 3, tool-deny: per vendor x profile x variant the modal principal reason (first code listed first in >= 2 of the
    valid repeats; None = no mode) and whether any repeat is missing (excluded)."""
    d = df[(df.setup == 3) & (df.tool_decision == "deny")]
    def cell(g):
        firsts = [c[0] for c in g.codes if isinstance(c, list) and c]
        vc = pd.Series(firsts).value_counts() if firsts else pd.Series(dtype=int)
        mode = vc.index[0] if len(vc) and vc.iloc[0] >= 2 else None
        return pd.Series({"mode": mode, "any_missing": bool(g.exclusion_reason.notna().any())})
    return d.groupby(UNIT + ["variant"]).apply(cell, include_groups=False).reset_index() if len(d) else pd.DataFrame()

def m1a_bounds(df, proxy, n_perm=10000):
    """(2a): C_x = 1[mode(x) != mode(base)]. Observed = complete units. Bounds: a proxy/placebo cell with no mode OR any missing
    repeat ((5a) extension) is imputed worst (C_proxy=1, C_placebo=0) / best (reverse). Units whose base has no mode are
    excluded from all versions. Seeds: SeedSequence(15866) child 0 observed, child 1 bounds."""
    vp, vpl = PAIRS[proxy]; c = m1a_cells(df)
    if c.empty: return None
    mode = c.pivot_table(index=UNIT, columns="variant", values="mode", aggfunc="first", dropna=False)
    miss = c.pivot_table(index=UNIT, columns="variant", values="any_missing", aggfunc="first")
    if not {"base", vp, vpl} <= set(mode.columns): return None
    t = mode[["base", vp, vpl]].join(miss[[vp, vpl]].add_suffix("_miss")).reset_index()
    nobase = t.base.isna(); t = t[~nobase]
    ch = lambda v: (t[v] != t.base).astype(float)
    imp_p = t[vp].isna() | t[f"{vp}_miss"].fillna(True).astype(bool)
    imp_pl = t[vpl].isna() | t[f"{vpl}_miss"].fillna(True).astype(bool)
    s0, s1 = np.random.SeedSequence(15866).spawn(2)[:2]
    comp = t[t[vp].notna() & t[vpl].notna()].assign(d=lambda x: (x[vp] != x.base).astype(float) - (x[vpl] != x.base).astype(float))
    out = {"proxy": proxy, "n_units_base_no_mode_excluded": int(nobase.sum()), "n_imputed_proxy": int(imp_p.sum()), "n_imputed_placebo": int(imp_pl.sum()),
           "observed": _summ(comp, n_perm, s0)}
    for name, fp, fpl in (("worst", 1.0, 0.0), ("best", 0.0, 1.0)):
        cp = ch(vp).where(~imp_p, fp); cpl = ch(vpl).where(~imp_pl, fpl)
        out[name] = _summ(t.assign(d=cp - cpl), n_perm, s1)
    out["any_missing"] = bool(imp_p.any() or imp_pl.any()); out["conclusion_flips_within_bounds"] = _flips(out)
    return out

# ------------------------------------------------------------------------------------------------ (5b) 2% check
def failure_category(msg: str | None) -> str | None:
    if not msg: return None
    m = msg.lower()
    for key, cat in (("budget_stop", "budget_stop"), ("refusal", "refusal"), ("max_tokens", "max_tokens"), ("letter", "letter_empty_or_short"),
                     ("duplicate", "duplicate_code"), ("m1a_code_count", "code_count"), ("deny needs", "code_count"),
                     ("empty on approve", "codes_on_approve"), ("reasons_text", "reasons_text"), ("not in reg b", "invalid_code"),
                     ("no_json", "no_json"), ("empty", "no_json"), ("decision", "bad_decision"), ("extra", "schema_extra_or_type"),
                     ("type", "schema_extra_or_type"), ("field required", "schema_missing_field")):
        if key in m: return cat
    return "other"

def first_failure(rec) -> str | None:
    """Failure reason of the FIRST attempt (= why it was retried), or the exclusion reason when there was no retry."""
    if rec.get("retry_reason"): return failure_category(rec.get("original_error") or rec["retry_reason"])
    err = rec.get("error")
    if isinstance(err, str) and err: return "budget_stop" if err.startswith("budget_stop") else "api_error"
    p = rec.get("parsed") or {}
    if p.get("valid"): return None
    if rec.get("refusal"): return "refusal"
    if rec.get("truncated"): return "max_tokens"
    return failure_category("; ".join(p.get("validation_errors") or ["other"]))

def two_pct_check(records, threshold=0.02) -> dict:
    """(5b): per vendor, no single failure reason above 2% of calls. Contradictions are not failures (they stay valid)."""
    df = pd.DataFrame([{"vendor": r.get("vendor"), "setup": r.get("setup"), "reason": first_failure(r)} for r in records])
    out = {}
    for v, g in df.groupby("vendor"):
        rates = (g.reason.value_counts() / len(g)).round(6).to_dict()
        worst = max(rates.items(), key=lambda kv: kv[1]) if rates else (None, 0.0)
        out[v] = {"n_calls": int(len(g)), "rates": rates, "max_reason": worst[0], "max_rate": float(worst[1]), "pass": worst[1] <= threshold,
                  "by_setup": {int(s): (gg.reason.value_counts() / len(gg)).round(6).to_dict() for s, gg in g.groupby("setup")}}
    return out

def report(df, records, n_perm=10000):
    return {"m2": {p: m2_bounds(df, p, n_perm) for p in PAIRS}, "m1a": {p: m1a_bounds(df, p, n_perm) for p in PAIRS},
            "two_pct": two_pct_check(records)}
