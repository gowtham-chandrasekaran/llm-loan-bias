"""Pre-registered analysis (plan s.15, 15.1, 15.2). Stubs are runnable on mock output; confirmatory use excludes pilot rows.
  python -m llmbias.analysis --run runs/<id>"""
import argparse, json, math, os, warnings
import numpy as np, pandas as pd
from scipy import stats
from .build_data import PAIRS
from .regb import ALL_REASONS

KEYS = ["vendor", "model", "effort", "setup"]
HOLM_FAMILY = [f"{m}_{p}" for m in ("M1a", "M1b", "M2", "M3") for p in ("name", "zip")]  # fixed 8 tests

def load_calls(run_dir, confirmatory=False):
    df = pd.DataFrame([json.loads(l) for l in open(os.path.join(run_dir, "calls.jsonl"))])
    if confirmatory:
        df = df[~df.excluded_from_confirmatory.astype(bool)]   # pilot rows never enter confirmatory analyses
    df["decision"] = df.parsed.apply(lambda p: p.get("decision") if isinstance(p, dict) else None)
    df["codes"] = df.parsed.apply(lambda p: p.get("reason_codes", []) if isinstance(p, dict) else [])
    df["effort"] = df.effort.fillna("none")
    return df

def majority(df):
    """Majority decision over repeats per (model, setup, profile, variant). Ties / no valid -> NaN."""
    def maj(s):
        s = s.dropna(); d = (s == "deny").sum(); a = (s == "approve").sum()
        return np.nan if d == a else ("deny" if d > a else "approve")
    return df.groupby(KEYS + ["profile_id", "variant"]).decision.apply(maj).rename("maj").reset_index()

def pair_table(maj, proxy_pairs=PAIRS):
    w = maj.pivot_table(index=KEYS + ["profile_id"], columns="variant", values="maj", aggfunc="first")
    out = []
    for ptype, proxy, a, b in proxy_pairs:
        if a not in w or b not in w: continue
        t = w[[a, b]].dropna().reset_index(); t.columns = KEYS + ["profile_id", "dec_a", "dec_b"]
        t["pair_type"], t["proxy"], t["va"], t["vb"] = ptype, proxy, a, b
        t["flip"] = t.dec_a != t.dec_b
        out.append(t)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

def wilson(k, n, z=1.96):
    if n == 0: return (np.nan, np.nan)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)

def newcombe_paired(x1, x2, z=1.96):
    """Newcombe (1998) method 10: CI for p1 - p2 from paired binaries, using Wilson score limits for each margin.
    Returns dict(diff, lo, hi, degenerate). Degenerate when n == 0 or a radicand is negative."""
    x1, x2 = np.asarray(x1, bool), np.asarray(x2, bool); n = len(x1)
    if n == 0: return {"diff": np.nan, "lo": np.nan, "hi": np.nan, "degenerate": True}
    a, b, c, d = int((x1 & x2).sum()), int((x1 & ~x2).sum()), int((~x1 & x2).sum()), int((~x1 & ~x2).sum())
    p1, p2 = (a + b) / n, (a + c) / n
    l1, u1 = wilson(a + b, n, z); l2, u2 = wilson(a + c, n, z)
    den = (a + b) * (c + d) * (a + c) * (b + d)
    phi = 0.0 if den == 0 else (a * d - b * c) / math.sqrt(den)
    r1 = (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2
    r2 = (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2
    if r1 < 0 or r2 < 0: return {"diff": p1 - p2, "lo": np.nan, "hi": np.nan, "degenerate": True}
    return {"diff": p1 - p2, "lo": p1 - p2 - math.sqrt(r1), "hi": p1 - p2 + math.sqrt(r2), "degenerate": False, "abcd": [a, b, c, d]}

def bootstrap_ci(values, groups=None, stat=np.mean, B=2000, seed=0):
    """Cluster bootstrap over profiles (groups) if given."""
    rng = np.random.default_rng(seed); v = np.asarray(values, float)
    if len(v) == 0: return (np.nan, np.nan)
    if groups is None:
        bs = [stat(v[rng.integers(0, len(v), len(v))]) for _ in range(B)]
    else:
        g = pd.Series(v).groupby(np.asarray(groups)).apply(list); keys = list(g.index)
        bs = [stat(np.concatenate([g[k] for k in rng.choice(keys, len(keys))])) for _ in range(B)]
    return (float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)))

def mcnemar(x_a, x_b):
    """Exact McNemar on paired binaries (e.g., deny indicators). Returns (b, c, p)."""
    x_a, x_b = np.asarray(x_a, bool), np.asarray(x_b, bool)
    b, c = int((x_a & ~x_b).sum()), int((~x_a & x_b).sum())
    p = 1.0 if b + c == 0 else float(min(1.0, 2 * stats.binom.cdf(min(b, c), b + c, 0.5)))
    return b, c, p

def holm(pvals: dict, alpha=0.05):
    """Holm-Bonferroni over a dict name->p. Missing tests in the fixed family are reported as not tested."""
    items = sorted(((k, v) for k, v in pvals.items() if v is not None and not np.isnan(v)), key=lambda x: x[1])
    m = len(HOLM_FAMILY); out, stop, running = {}, False, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, max(running, (m - i) * p)); running = adj
        rej = (not stop) and p <= alpha / (m - i)
        if not rej: stop = True
        out[k] = {"p": p, "p_holm": adj, "reject": rej}
    for k in HOLM_FAMILY:
        out.setdefault(k, {"p": None, "p_holm": None, "reject": False, "note": "not tested"})
    return out

def jsd(c1, c2, codes=None):
    codes = codes or sorted(ALL_REASONS)
    p = np.array([c1.get(c, 0) for c in codes], float) + 1e-12; q = np.array([c2.get(c, 0) for c in codes], float) + 1e-12
    p, q = p / p.sum(), q / q.sum(); m = (p + q) / 2
    return float(0.5 * (p * np.log2(p / m)).sum() + 0.5 * (q * np.log2(q / m)).sum())

def permutation_paired(units_a, units_b, stat_fn, n_perm=10000, seed=0):
    """Within-pair label-swap permutation test. units_* are lists of per-pair observations (aligned).
    stat_fn(list_a, list_b) -> float. Two-sided via |stat| for difference stats; JSD is already >=0."""
    rng = np.random.default_rng(seed); obs = stat_fn(units_a, units_b); n = len(units_a); ge = 0
    for _ in range(n_perm):
        sw = rng.random(n) < 0.5
        A = [b if s else a for a, b, s in zip(units_a, units_b, sw)]; Bv = [a if s else b for a, b, s in zip(units_a, units_b, sw)]
        ge += abs(stat_fn(A, Bv)) >= abs(obs) - 1e-12
    return obs, (ge + 1) / (n_perm + 1)

def _code_counts(list_of_code_lists):
    c = {}
    for codes in list_of_code_lists:
        for x in codes: c[x] = c.get(x, 0) + 1
    return c

def m1a(df, va, vb, n_perm=10000):
    """Setup 3, fixed denials only: JSD of cited codes between variants; per-code paired diffs. Unit = profile x model (codes pooled over repeats)."""
    d = df[(df.setup == 3) & (df.tool_decision == "deny")]
    g = d.groupby(KEYS + ["profile_id", "variant"]).codes.apply(lambda s: sum(s, [])).unstack("variant")
    if va not in g or vb not in g: return None
    g = g[[va, vb]].dropna()
    if len(g) == 0: return None
    A, B = list(g[va]), list(g[vb])
    obs, p = permutation_paired(A, B, lambda a, b: jsd(_code_counts(a), _code_counts(b)), n_perm)
    per_code = {}
    for c in sorted(ALL_REASONS):
        diff = np.array([a.count(c) / max(len(a), 1) - b.count(c) / max(len(b), 1) for a, b in zip(A, B)])
        if diff.any(): per_code[c] = {"mean_diff": float(diff.mean()), "ci": bootstrap_ci(diff)}
    return {"n_units": len(g), "jsd": obs, "p_perm": p, "per_code": per_code}

def m1b(df, judg, va, vb, decision="deny", n_perm=10000):
    """Hedging/discouraging rate difference on fixed decisions (deny = primary; approve = exploratory)."""
    if judg is None or judg.empty: return None
    d = df[(df.setup == 3) & (df.tool_decision == decision)].merge(judg[["call_id", "hedging"]], on="call_id")
    d["hedging"] = d.hedging.astype(float)
    g = d.groupby(KEYS + ["profile_id", "variant"]).hedging.mean().unstack("variant")
    if va not in g or vb not in g: return None
    g = g[[va, vb]].dropna()
    if len(g) == 0: return None
    A, B = list(g[va]), list(g[vb])
    obs, p = permutation_paired(A, B, lambda a, b: float(np.mean(np.subtract(b, a))), n_perm)
    return {"n_units": len(g), "rate_a": float(np.mean(A)), "rate_b": float(np.mean(B)), "diff": obs,
            "ci": bootstrap_ci(np.subtract(B, A), g.reset_index().profile_id), "p_perm": p}

def m2(df, va, vb):
    """Setup 2: override (final decision != tool decision), majority over repeats; McNemar on paired overrides."""
    d = df[df.setup == 2].copy()
    if d.empty: return None
    d["ov"] = d.override.astype(float)
    g = d.groupby(KEYS + ["profile_id", "variant"]).ov.mean().unstack("variant")
    if va not in g or vb not in g: return None
    g = (g[[va, vb]].dropna() > 0.5)
    b, c, p = mcnemar(g[va], g[vb])
    tc = d.groupby("variant").tool_called.mean().to_dict()
    return {"n_units": len(g), "override_a": float(g[va].mean()), "override_b": float(g[vb].mean()), "b": b, "c": c, "p_mcnemar": p,
            "tool_called_rate_by_variant": tc}

def m3(pairs, proxy, setup=1):
    """Flip rate net of control-vs-control chance flips, McNemar on deny indicators."""
    t = pairs[(pairs.setup == setup)]
    pr = t[(t.pair_type == "proxy") & (t.proxy == proxy)]; ct = t[t.pair_type == "control"]
    if pr.empty: return None
    b, c, p = mcnemar(pr.dec_a == "deny", pr.dec_b == "deny")
    k, n = int(pr.flip.sum()), len(pr); ctrl = float(ct.flip.mean()) if len(ct) else 0.0
    # paired (same model x profile) proxy flip vs control flip -> Newcombe paired-difference CI; bootstrap fallback
    j = pr.merge(ct[KEYS + ["profile_id", "flip"]], on=KEYS + ["profile_id"], suffixes=("", "_ctrl"))
    nc = newcombe_paired(j.flip, j.flip_ctrl)
    boot = bootstrap_ci(j.flip.astype(float) - j.flip_ctrl.astype(float), j.profile_id)
    upper = boot[1] if nc["degenerate"] else nc["hi"]
    return {"n_pairs": n, "flips": k, "flip_rate": k / n, "control_flip_rate": ctrl, "n_paired_with_control": len(j),
            "net_flip_rate": float((j.flip.astype(float) - j.flip_ctrl.astype(float)).mean()) if len(j) else np.nan,
            "newcombe": nc, "net_flip_ci_boot": boot, "trigger_upper": upper, "trigger_method": "bootstrap" if nc["degenerate"] else "newcombe",
            "wilson_raw": wilson(k, n), "b_deny_a_only": b, "c_deny_b_only": c, "p_mcnemar": p}

def _name_source_term(d):
    """Pair-level name-source covariate (Reviewer 2026-10-12): source of the names shown in that profile's pair;
    'mixed' if the two arms used different sources. Added as a fixed effect only when >1 level is present."""
    if "name_source" not in d: return d.assign(name_source="NA"), False
    ns = d.groupby("profile_id")["name_source"].agg(lambda s: "|".join(sorted(set(s.dropna().astype(str)))) or "NA")
    d = d.assign(name_source=d.profile_id.map(ns).map(lambda x: "mixed" if "|" in x else x))
    return d, d.name_source.nunique() > 1

NAME_PAIRS_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed", "name_pairs.csv")

def _pair_gap_covs(d, pairs_csv=NAME_PAIRS_CSV):
    """15.7: pair-level |class gap| (SD; 0 when missing), |era gap| (years), class_missing indicator, joined on name_pair_id.
    Returns (d, use) where use is True when the covariates vary (otherwise they are left out as inestimable)."""
    if "name_pair_id" not in d or not os.path.exists(pairs_csv):
        return d.assign(class_gap_abs=0.0, era_gap_abs=0.0, class_missing=0), False
    npairs = pd.read_csv(pairs_csv).set_index("name_pair_id")
    miss = npairs.class_w.isna() | npairs.class_b.isna()
    d = d.assign(class_gap_abs=d.name_pair_id.map(npairs.class_gap.abs().where(~miss, 0.0)).astype(float),
                 era_gap_abs=d.name_pair_id.map(npairs.era_gap.abs()).astype(float),
                 class_missing=d.name_pair_id.map(miss.astype(int)))
    use = d[["class_gap_abs", "era_gap_abs", "class_missing"]].notna().all().all() and d.class_gap_abs.nunique() > 1
    return d, bool(use)

def _gap_terms(d):
    return [c for c in ("class_gap_abs", "era_gap_abs", "class_missing") if d[c].nunique() > 1]

def mixed_logit(df, proxy_variants, setup=1, pair_covs=False):
    """decision ~ proxy x model + effort + state + (1|profile)  [pre-reg 15.1 #3, 15.2]. Variational Bayes GLMM (statsmodels).
    Returns summary dict or note if not estimable (e.g., no variation in mock)."""
    from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
    a, b = proxy_variants
    d = df[(df.setup == setup) & df.variant.isin([a, b]) & df.decision.notna()].copy()
    if d.empty or d.decision.nunique() < 2: return {"note": "not estimable (no outcome variation)"}
    d["deny"] = (d.decision == "deny").astype(int); d["proxy"] = (d.variant == b).astype(int)
    d["state"] = d.get("proxy_state", pd.Series("NA", index=d.index)).fillna("NA").astype(str)
    d, use_src = _name_source_term(d)
    terms = ["proxy"] + (["C(model)", "proxy:C(model)"] if d.model.nunique() > 1 else []) + (["C(effort)"] if d.effort.nunique() > 1 else []) + (["C(state)"] if d.state.nunique() > 1 else []) + (["C(name_source)"] if use_src else [])
    if pair_covs:
        d, use_g = _pair_gap_covs(d); terms += _gap_terms(d) if use_g else []
    f = "deny ~ " + " + ".join(terms)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = BinomialBayesMixedGLM.from_formula(f, {"profile": "0 + C(profile_id)"}, d).fit_vb()
    names = m.model.exog_names
    return {"formula": f + " + (1|profile)", "n": len(d), "fe": {n: {"mean": float(m.fe_mean[i]), "sd": float(m.fe_sd[i])} for i, n in enumerate(names)}}

def glmer_fit(df, proxy_variants, setup=1, rscript="Rscript", pair_covs=False):
    """CONFIRMATORY model (15.3 #6): lme4::glmer via Rscript subprocess, Laplace. Same formula as mixed_logit (VB = sensitivity)."""
    import subprocess, tempfile, shutil
    if not shutil.which(rscript): return {"ok": False, "error": "Rscript not found"}
    a, b = proxy_variants
    d = df[(df.setup == setup) & df.variant.isin([a, b]) & df.decision.notna()].copy()
    if d.empty or d.decision.nunique() < 2: return {"ok": False, "note": "not estimable (no outcome variation)"}
    d["deny"] = (d.decision == "deny").astype(int); d["proxy"] = (d.variant == b).astype(int)
    d["state"] = d.get("proxy_state", pd.Series("NA", index=d.index)).fillna("NA").astype(str)
    d, use_src = _name_source_term(d)
    terms = ["proxy"] + (["model", "proxy:model"] if d.model.nunique() > 1 else []) + (["effort"] if d.effort.nunique() > 1 else []) + (["state"] if d.state.nunique() > 1 else []) + (["name_source"] if use_src else [])
    if pair_covs:  # 15.7 pair-level covariates (name analyses only)
        d, use_g = _pair_gap_covs(d); terms += _gap_terms(d) if use_g else []
    else:
        d = d.assign(class_gap_abs=0.0, era_gap_abs=0.0, class_missing=0)
    f = "deny ~ " + " + ".join(terms) + " + (1 | profile_id)"
    with tempfile.TemporaryDirectory() as td:
        ci, co = os.path.join(td, "d.csv"), os.path.join(td, "o.json")
        d[["deny", "proxy", "model", "effort", "state", "name_source", "class_gap_abs", "era_gap_abs", "class_missing", "profile_id"]].to_csv(ci, index=False)
        r = subprocess.run([rscript, os.path.join(os.path.dirname(__file__), "..", "..", "R", "glmer_fit.R"), ci, co, f], capture_output=True, text=True, timeout=600)
        if not os.path.exists(co): return {"ok": False, "error": (r.stderr or r.stdout)[-800:]}
        return json.loads(open(co).read(), strict=False)

def fallback_trigger(m3_res):
    """Pre-reg 15.3 #5: net flip = proxy-pair flip rate - control-pair flip rate (paired); 95% Newcombe paired-difference CI
    (paired cluster bootstrap if Newcombe is degenerate). Add bank context if the upper bound < 10%."""
    if not m3_res: return None
    return {"upper": m3_res["trigger_upper"], "method": m3_res["trigger_method"], "trigger_fallback": bool(m3_res["trigger_upper"] < 0.10)}

def analyze(run_dir, n_perm=10000):
    meta = json.load(open(os.path.join(run_dir, "run_meta.json")))
    df = load_calls(run_dir)
    jp = os.path.join(run_dir, "judgments.jsonl")
    judg = pd.DataFrame([json.loads(l) for l in open(jp)]) if os.path.exists(jp) else None
    maj = majority(df); pairs = pair_table(maj)
    R = {"run_id": meta["run_id"], "excluded_from_confirmatory": meta["excluded_from_confirmatory"],
         "label": "PILOT - feasibility only, excluded from confirmatory analysis" if meta["excluded_from_confirmatory"] else "confirmatory"}
    pv = {}
    for proxy, (va, vb) in {"name": ("base", "name_b"), "zip": ("base", "zip_b")}.items():
        r1 = m1a(df, va, vb, n_perm); r1b = m1b(df, judg, va, vb, "deny", n_perm); r2 = m2(df, va, vb); r3 = m3(pairs, proxy)
        R[f"M1a_{proxy}"], R[f"M1b_{proxy}"], R[f"M2_{proxy}"], R[f"M3_{proxy}"] = r1, r1b, r2, r3
        R[f"M1b_approve_exploratory_{proxy}"] = m1b(df, judg, va, vb, "approve", n_perm)
        pv[f"M1a_{proxy}"] = r1 and r1["p_perm"]; pv[f"M1b_{proxy}"] = r1b and r1b["p_perm"]
        pv[f"M2_{proxy}"] = r2 and r2["p_mcnemar"]; pv[f"M3_{proxy}"] = r3 and r3["p_mcnemar"]
        R[f"glmer_confirmatory_{proxy}"] = glmer_fit(df, (va, vb), pair_covs=(proxy == "name"))
        R[f"mixed_logit_vb_sensitivity_{proxy}"] = mixed_logit(df, (va, vb), pair_covs=(proxy == "name"))
    R["placebo"] = {f"{p}": m3(pairs.assign(pair_type=pairs.pair_type.replace({"placebo": "proxy"})).query("pair_type=='proxy' or pair_type=='control'"), p)
                    for p in ("name", "name_black", "zip") if not pairs.empty}
    R["placebo_M1a"] = {"name": m1a(df, "base", "name_w_placebo", n_perm), "zip": m1a(df, "base", "zip_placebo", n_perm)}
    R["cross_name_vs_zip_M3"] = m3(pairs.assign(pair_type=pairs.pair_type.replace({"cross": "proxy"}), proxy=pairs.proxy.replace({"name_vs_zip": "x"})), "x") if not pairs.empty else None
    R["holm"] = holm({k: (v if v is not None else np.nan) for k, v in pv.items()})
    hp = R["holm"].get("M3_name", {}).get("p_holm") if isinstance(R["holm"].get("M3_name"), dict) else None
    R["name_level"] = {f"setup{st}": name_level(df, setup=st, holm_p=hp, n_perm=n_perm) for st in sorted(df.setup.unique()) if st in (1, 2)}
    if "name_pair_sensitivity_only" in df and "name_pair_no_class" in df:  # 15.7 (c): 5 clean pairs, descriptive
        clean = df.assign(_dirty=df.name_pair_sensitivity_only.fillna(False).astype(bool) | df.name_pair_no_class.fillna(False).astype(bool))
        R["name_level_clean_pairs_descriptive"] = {f"setup{st}": name_level(clean, setup=st, n_perm=n_perm, exclude="_dirty") for st in sorted(df.setup.unique()) if st in (1, 2)}
    for ex in ("name_pair_sensitivity_only", "name_pair_no_class"):
        R[f"name_level_excl_{ex.replace('name_pair_', '')}"] = {f"setup{st}": name_level(df, setup=st, holm_p=hp, n_perm=n_perm, exclude=ex) for st in sorted(df.setup.unique()) if st in (1, 2)}
    R["name_level_strict_15_5_sensitivity"] = {f"setup{st}": name_level(df, setup=st, strict_only=True, holm_p=hp, n_perm=n_perm) for st in sorted(df.setup.unique()) if st in (1, 2)}
    R["fallback"] = fallback_trigger(R.get("M3_name")) , fallback_trigger(R.get("M3_zip"))
    R["parse_rate"] = float(df.parsed.apply(lambda p: isinstance(p, dict) and p.get("parse_ok")).mean())
    if (df.setup == 2).any(): R["tool_called_rate"] = float(df[df.setup == 2].tool_called.mean())
    # HMDA comparison: Reg B codes -> HMDA denial reasons vs observed HMDA denial reasons on the same profiles (descriptive)
    from .regb import REGB_TO_HMDA
    den = df[(df.decision == "deny") & (df.setup == 1)]
    R["hmda_reason_compare"] = {"llm_cited_as_hmda": pd.Series([REGB_TO_HMDA.get(c) for cs in den.codes for c in cs]).value_counts(normalize=True).round(3).to_dict()}
    json.dump(R, open(os.path.join(run_dir, "analysis.json"), "w"), indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    return R

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--run", required=True); ap.add_argument("--n-perm", type=int, default=10000)
    a = ap.parse_args(); R = analyze(a.run, a.n_perm); print(json.dumps({k: R[k] for k in ["label", "holm", "fallback", "parse_rate"]}, indent=1, default=str))


# ---- Name-level inference (amendments 15.5 / 15.6). No name random effect: too few names. ----
def name_diffs(df, va="base", vb="name_b", setup=1, extra=()):
    """Paired deny difference (majority-of-3) per profile x model: d = deny(vb) - deny(va), tagged with the name pair."""
    d = df[(df.setup == setup)]
    mj = majority(d)
    w = mj.pivot_table(index=KEYS + ["profile_id"], columns="variant", values="maj", aggfunc="first")
    if va not in w or vb not in w: return pd.DataFrame()
    t = w[[va, vb]].dropna().reset_index()
    t["d"] = (t[vb] == "deny").astype(int) - (t[va] == "deny").astype(int)
    meta = d.drop_duplicates("profile_id").set_index("profile_id")
    for c in ("name_pair_id", "name_pair_strict_155", "name_pair_sensitivity_only", "name_pair_no_class") + tuple(extra):
        t[c] = t.profile_id.map(meta[c]) if c in meta else np.nan
    names = d[d.variant == vb].drop_duplicates("profile_id").set_index("profile_id").applicant_name
    t["name_b"] = t.profile_id.map(names)
    return t

def cluster_robust_mean(d, cl):
    """Intercept-only OLS with CR1 cluster-robust SE; t with G-1 df."""
    d = np.asarray(d, float); cl = np.asarray(cl); m = d.mean(); n = len(d)
    G = len(set(cl))
    if G < 2: return {"est": float(m), "se": np.nan, "p": np.nan, "G": G}
    s = pd.Series(d - m).groupby(cl).sum().values
    se = math.sqrt((G / (G - 1)) * (s ** 2).sum()) / n
    tval = m / se if se > 0 else np.nan
    p = float(2 * stats.t.sf(abs(tval), G - 1)) if se > 0 else 1.0
    q = stats.t.ppf(0.975, G - 1)
    return {"est": float(m), "se": float(se), "t": None if se == 0 else float(tval), "df": G - 1, "p": p, "G": G,
            "ci": [float(m - q * se), float(m + q * se)] if se > 0 else [float(m), float(m)]}

def name_sign_flip_perm(d, cl, n_perm=10000, seed=0):
    """Name-level permutation: flip the sign of all of one name pair's differences together. Exact if 2^G <= n_perm."""
    d = np.asarray(d, float); cl = np.asarray(cl); keys = sorted(set(cl)); G = len(keys)
    sums = np.array([d[cl == k].sum() for k in keys]); obs = abs(sums.sum())
    if G <= int(math.log2(n_perm)):
        import itertools
        signs = np.array(list(itertools.product([1, -1], repeat=G)))
    else:
        signs = np.random.default_rng(seed).choice([1, -1], size=(n_perm, G))
    stat = np.abs(signs @ sums)
    return {"p": float((stat >= obs - 1e-12).mean()), "G": G, "exact": G <= int(math.log2(n_perm))}

def name_level(df, va="base", vb="name_b", setup=1, strict_only=False, holm_p=None, n_perm=10000, exclude=None):
    """Per-name estimates, leave-one-name-out, cluster-robust and permutation inference at the name-pair level.
    Robust per 15.5: Holm-significant in the full sample AND every LONO estimate keeps the sign and lies inside the full 95% CI."""
    t = name_diffs(df, va, vb, setup, extra=(exclude,) if exclude and exclude.startswith("_") else ())
    if t.empty or t.name_pair_id.isna().all(): return {"note": "no name-pair data"}
    if strict_only: t = t[t.name_pair_strict_155.astype(bool)]
    if exclude: t = t[~t[exclude].fillna(False).astype(bool)]   # 'name_pair_sensitivity_only' or 'name_pair_no_class'
    if t.empty: return {"note": "no rows in subset"}
    full = cluster_robust_mean(t.d, t.name_pair_id)
    per = t.groupby(["name_pair_id", "name_b"]).d.agg(["mean", "size"]).reset_index().rename(columns={"mean": "est", "size": "n"})
    lono = []
    for k in sorted(t.name_pair_id.unique()):
        s = t[t.name_pair_id != k]
        est = float(s.d.mean()) if len(s) else np.nan
        ok = bool(len(s) and np.sign(est) == np.sign(full["est"]) and full.get("ci") and full["ci"][0] <= est <= full["ci"][1])
        lono.append({"dropped": k, "est": est, "same_sign_inside_ci": ok})
    perm = name_sign_flip_perm(t.d, t.name_pair_id, n_perm)
    lono_ok = all(x["same_sign_inside_ci"] for x in lono)
    return {"n": int(len(t)), "full_cluster_robust": full, "perm_name_level": perm, "per_name": per.to_dict("records"),
            "lono": lono, "lono_all_pass": lono_ok,
            "robust_confirmed": bool(holm_p is not None and holm_p < 0.05 and lono_ok),
            "note": "no name random effect (15.5); clusters = name pairs"}
