"""Build borderline profiles + matched variants from public HMDA data. Deterministic given configs/data.yaml seed.
Run: python -m llmbias.build_data"""
import hashlib, json, os
import numpy as np, pandas as pd, yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
P = lambda *a: os.path.join(ROOT, *a)

EXPECTED_COLS = {
    "activity_year": "int", "census_tract": "str", "action_taken": "int", "loan_type": "int", "loan_purpose": "int",
    "lien_status": "int", "occupancy_type": "int", "derived_dwelling_category": "str", "total_units": "str",
    "business_or_commercial_purpose": "int", "reverse_mortgage": "int", "open-end_line_of_credit": "int",
    "loan_amount": "float", "loan_to_value_ratio": "float", "property_value": "float", "income": "float",
    "debt_to_income_ratio": "str", "loan_term": "float", "derived_race": "str", "derived_msa-md": "str",
    "denial_reason-1": "str", "denial_reason-2": "str", "denial_reason-3": "str", "denial_reason-4": "str",
    "tract_minority_population_percent": "float", "tract_to_msa_income_percentage": "float",
    "ffiec_msa_md_median_family_income": "float", "tract_owner_occupied_units": "float",
    "tract_one_to_four_family_homes": "float", "tract_population": "float",
}
DTI_BANDS = {"<20%": (10, 20), "20%-<30%": (20, 30), "30%-<36%": (30, 36), "50%-60%": (50, 60), ">60%": (60, 70)}
JOBS = ["registered nurse", "logistics coordinator", "high school teacher", "electrician", "account manager",
        "pharmacy technician", "software support analyst", "HVAC technician", "insurance claims adjuster",
        "dental hygienist", "retail store manager", "civil engineering technician"]
PROXY_FIELDS = ["applicant_name", "property_zip"]


def load_cfg(path=None):
    with open(path or P("configs", "data.yaml")) as f:
        return yaml.safe_load(f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def schema_check(df):
    """Return list of problems (empty == pass). Checks presence and castability of expected columns."""
    probs = []
    for c, t in EXPECTED_COLS.items():
        if c not in df.columns:
            probs.append(f"missing column {c}"); continue
        if t in ("int", "float"):
            s = pd.to_numeric(df[c].replace({"Exempt": np.nan, "NA": np.nan}), errors="coerce")
            bad = s.isna() & df[c].notna() & ~df[c].isin(["Exempt", "NA"])
            if bad.any():
                probs.append(f"{c}: {int(bad.sum())} non-numeric values")
    return probs


def missingness(df):
    rows = []
    for c in EXPECTED_COLS:
        s = df[c]
        rows.append({"field": c, "n": len(s), "missing": int(s.isna().sum()), "exempt": int((s == "Exempt").sum()),
                     "NA_string": int((s == "NA").sum())})
    return pd.DataFrame(rows)


def clean_and_filter(df, cfg):
    """Apply documented filters in order; returns (kept_df, exclusion_log_df, step_counts_df)."""
    excl, steps = [], [("raw", len(df))]
    num = lambda c: pd.to_numeric(df[c], errors="coerce")
    df = df.copy()
    for c in ["action_taken", "loan_type", "loan_purpose", "lien_status", "occupancy_type",
              "business_or_commercial_purpose", "reverse_mortgage", "open-end_line_of_credit"]:
        df[c] = num(c)
    rules = [
        ("action_taken in {1 originated, 3 denied}", lambda d: d.action_taken.isin([1, 3])),
        ("loan_type == 1 conventional", lambda d: d.loan_type == 1),
        ("loan_purpose == 1 home purchase", lambda d: d.loan_purpose == 1),
        ("lien_status == 1 first lien", lambda d: d.lien_status == 1),
        ("occupancy_type == 1 principal residence", lambda d: d.occupancy_type == 1),
        ("site-built single family, 1 unit", lambda d: (d.derived_dwelling_category == "Single Family (1-4 Units):Site-Built") & (d.total_units == "1")),
        ("not business/reverse/open-end", lambda d: (d.business_or_commercial_purpose == 2) & (d.reverse_mortgage == 2) & (d["open-end_line_of_credit"] == 2)),
    ]
    for name, f in rules:
        m = f(df).fillna(False)
        steps.append((name, int(m.sum())))
        df = df[m]
    # missing / exempt rule: drop if any key field missing, 'NA' or 'Exempt'
    key = ["income", "loan_amount", "property_value", "loan_to_value_ratio", "debt_to_income_ratio", "census_tract",
           "tract_minority_population_percent", "tract_to_msa_income_percentage"]
    bad = df[key].isna().any(axis=1) | df[key].isin(["Exempt", "NA"]).any(axis=1)
    for i in df.index[bad]:
        excl.append({"row_index": int(i), "rule": "missing_or_exempt_key_field",
                     "detail": ",".join(k for k in key if pd.isna(df.at[i, k]) or df.at[i, k] in ("Exempt", "NA"))})
    df = df[~bad]; steps.append(("drop missing/exempt key fields", len(df)))
    for c in ["income", "loan_amount", "property_value", "loan_to_value_ratio", "tract_minority_population_percent",
              "tract_to_msa_income_percentage", "ffiec_msa_md_median_family_income"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    ranges = [
        ("income <= 0", df.income <= 0), ("loan_amount <= 0", df.loan_amount <= 0),
        ("property_value <= 0", df.property_value <= 0),
        ("CLTV <= 0 or > 200", (df.loan_to_value_ratio <= 0) | (df.loan_to_value_ratio > 200)),
        ("loan_amount > 1.5 * property_value", df.loan_amount > 1.5 * df.property_value),
        ("minority% outside [0,100]", ~df.tract_minority_population_percent.between(0, 100)),
    ]
    badr = pd.Series(False, index=df.index)
    for name, m in ranges:
        for i in df.index[m & ~badr]:
            excl.append({"row_index": int(i), "rule": "range", "detail": name})
        badr |= m
    df = df[~badr]; steps.append(("drop range violations", len(df)))
    return df, pd.DataFrame(excl, columns=["row_index", "rule", "detail"]), pd.DataFrame(steps, columns=["step", "rows_remaining"])


def tract_table(raw, cfg):
    t = raw.dropna(subset=["census_tract"]).copy()
    for c in ["tract_minority_population_percent", "tract_to_msa_income_percentage", "ffiec_msa_md_median_family_income",
              "tract_owner_occupied_units", "tract_one_to_four_family_homes", "action_taken"]:
        t[c] = pd.to_numeric(t[c], errors="coerce")
    g = t.groupby("census_tract").agg(
        msa=("derived_msa-md", "first"), state=("state_code", "first"), minority_pct=("tract_minority_population_percent", "first"),
        income_pct=("tract_to_msa_income_percentage", "first"), msa_mfi=("ffiec_msa_md_median_family_income", "first"),
        owner_units=("tract_owner_occupied_units", "first"), homes_1to4=("tract_one_to_four_family_homes", "first"))
    w = t[t.derived_race == "White"]
    dr = w.groupby("census_tract").action_taken.agg(white_n="size", white_denial_rate=lambda s: (s == 3).mean())
    g = g.join(dr)
    g["owner_occ_rate"] = g.owner_units / g.homes_1to4
    hpi = pd.read_csv(P("data", "raw", "fhfa_hpi_at_tract.csv"), dtype={"tract": str})
    hpi = hpi[hpi.state_abbr.isin(cfg["states"])]
    hpi["hpi"] = pd.to_numeric(hpi.hpi, errors="coerce")
    a = hpi[hpi.year == cfg["tracts"]["hpi_base_year"]].set_index("tract").hpi
    b = hpi[hpi.year == cfg["tracts"]["hpi_end_year"]].set_index("tract").hpi
    g["hpi_growth_pct"] = ((b / a - 1) * 100).reindex(g.index)
    rel = pd.read_csv(P("data", "raw", "zcta_tract_rel_2020.txt"), sep="|", dtype=str)
    rel = rel[rel.GEOID_TRACT_20.str[:2].isin(cfg["state_fips"]) & rel.GEOID_ZCTA5_20.notna() & (rel.GEOID_ZCTA5_20 != "")]
    rel["AREALAND_PART"] = pd.to_numeric(rel.AREALAND_PART)
    rel["share"] = rel.AREALAND_PART / rel.groupby("GEOID_TRACT_20").AREALAND_PART.transform("sum")
    top = rel.sort_values("AREALAND_PART", ascending=False).drop_duplicates("GEOID_TRACT_20").set_index("GEOID_TRACT_20")
    g["zcta"] = top.GEOID_ZCTA5_20.reindex(g.index)
    g["zcta_land_share"] = top.share.reindex(g.index)
    return g.reset_index()


def match_tract_pairs(tt, cfg, placebo=False):
    c = cfg["tracts"]
    ok = tt.dropna(subset=["income_pct", "hpi_growth_pct", "zcta", "minority_pct"])
    lo = ok[ok.minority_pct <= c["low_minority_max"]]
    hi = lo if placebo else ok[ok.minority_pct >= c["high_minority_min"]]
    m = lo.merge(hi, on=["state", "msa"], suffixes=("_a", "_b"))
    if placebo:
        m = m[m.census_tract_a < m.census_tract_b]
    m = m[m.zcta_a != m.zcta_b]
    m["di"] = (m.income_pct_a - m.income_pct_b).abs(); m["dh"] = (m.hpi_growth_pct_a - m.hpi_growth_pct_b).abs()
    m = m[(m.di <= c["max_income_pct_diff"]) & (m.dh <= c["max_hpi_growth_diff"])]
    m["d"] = m.di / 10 + m.dh / 10 + (m.owner_occ_rate_a - m.owner_occ_rate_b).abs().fillna(0)
    cands = list(zip(m.d, m.census_tract_a, m.census_tract_b))
    cands.sort()
    zmap = ok.set_index("census_tract").zcta.to_dict()
    used, pairs = set(), []
    for d, x, y in cands:  # greedy one-to-one
        zx, zy = zmap[x], zmap[y]
        if x in used or y in used or zx in used or zy in used: continue
        used |= {x, y, zx, zy}; pairs.append((x, y, d))
    rows, ti = [], tt.set_index("census_tract")
    for x, y, d in pairs:
        A, B = ti.loc[x], ti.loc[y]
        rows.append({"state": A.state, "msa": A.msa, "tract_a": x, "zip_a": A.zcta, "minority_a": A.minority_pct, "income_pct_a": A.income_pct, "hpi_growth_a": round(A.hpi_growth_pct, 2),
                     "owner_occ_a": round(A.owner_occ_rate, 3), "white_denial_a": A.white_denial_rate, "white_n_a": A.white_n,
                     "tract_b": y, "zip_b": B.zcta, "minority_b": B.minority_pct, "income_pct_b": B.income_pct, "hpi_growth_b": round(B.hpi_growth_pct, 2),
                     "owner_occ_b": round(B.owner_occ_rate, 3), "white_denial_b": B.white_denial_rate, "white_n_b": B.white_n,
                     "match_distance": round(d, 3), "placebo": placebo})
    return pd.DataFrame(rows)


# First-name gender is AUTHOR-CODED (Crabtree data has no gender field). Flagged for Researcher review.
GENDER = {"David": "M", "James": "M", "John": "M", "Mark": "M", "Michael": "M", "Richard": "M", "Robert": "M", "Thomas": "M",
          "William": "M", "Mary": "F", "Darnell": "M", "Jermaine": "M", "Roosevelt": "M", "Ebony": "F", "Lakisha": "F",
          "Latasha": "F", "Latonya": "F", "Latoya": "F", "Tamika": "F", "Tanisha": "F"}


def name_pools(cfg):
    s = pd.concat([pd.read_csv(P("data", "raw", "crabtree", f"study-{i}-names.csv")) for i in (1, 2, 3)])
    g = s.groupby(["name", "first", "last", "identity"]).agg(
        n_ratings=("correct", "size"), race_recognition=("correct", "mean"),
        perceived_income_ord=("income.ord", "mean"), perceived_edu_ord=("education.ord", "mean"),
        perceived_citizen=("citizen", "mean")).reset_index()
    g = g[g.identity.isin(["White", "Black or African American"])].copy()
    g["race"] = g.identity.map({"White": "W", "Black or African American": "B"})
    g["gender"] = g["first"].map(GENDER)
    k, out = cfg["names"]["per_group"], []
    for race in "WB":
        for gen in "FM":
            sub = g[(g.race == race) & (g.gender == gen)].sort_values(["race_recognition", "name"], ascending=[False, True])
            pick = sub[sub.race_recognition >= cfg["names"]["min_recognition"]].head(k)
            if len(pick) < k:  # top-up below threshold, flagged
                extra = sub[~sub.name.isin(pick.name)].head(k - len(pick)).assign(below_threshold=True)
                pick = pd.concat([pick.assign(below_threshold=False), extra])
            else:
                pick = pick.assign(below_threshold=False)
            out.append(pick)
    return pd.concat(out).reset_index(drop=True)


def pair_names(pools):
    """Within gender: match each Black name to an unused White name minimizing perceived income+education gap."""
    rows = []
    for gen in "FM":
        W = pools[(pools.race == "W") & (pools.gender == gen)].copy()
        B = pools[(pools.race == "B") & (pools.gender == gen)]
        used = set()
        for _, b in B.iterrows():
            W2 = W[~W.name.isin(used)]
            d = (W2.perceived_income_ord - b.perceived_income_ord).abs() + (W2.perceived_edu_ord - b.perceived_edu_ord).abs()
            w = W2.loc[d.idxmin()]; used.add(w["name"])
            rows.append({"gender": gen, "name_w": w["name"], "name_b": b["name"], "rec_w": w.race_recognition, "rec_b": b.race_recognition,
                         "inc_w": w.perceived_income_ord, "inc_b": b.perceived_income_ord, "edu_w": w.perceived_edu_ord, "edu_b": b.perceived_edu_ord,
                         "below_threshold_b": bool(b.below_threshold)})
    return pd.DataFrame(rows)


def synth_profiles(kept, cfg, rng):
    b = cfg["borderline"]
    dti_num = pd.to_numeric(kept.debt_to_income_ratio, errors="coerce")
    border = kept[dti_num.isin(b["dti_values"]) & kept.loan_to_value_ratio.between(b["ltv_min"], b["ltv_max"])]
    appr = kept[kept.debt_to_income_ratio.isin(["<20%", "20%-<30%"]) & (kept.loan_to_value_ratio <= 80)]
    deny = kept[kept.debt_to_income_ratio.isin([">60%"])]
    pick = lambda d, n: d.sample(n=min(n, len(d)), random_state=int(rng.integers(1 << 31)))
    S = [("borderline", pick(border, cfg["n_borderline"])), ("control_approve", pick(appr, cfg["n_control_approve"])),
         ("control_deny", pick(deny, cfg["n_control_deny"]))]
    rows = []
    for stratum, d in S:
        for idx, r in d.iterrows():
            band = r.debt_to_income_ratio
            if band in DTI_BANDS:
                lo, hi = DTI_BANDS[band]; dti = round(float(rng.uniform(lo, hi - 0.1)), 1); dti_lo, dti_hi = lo, hi
            else:
                v = float(band); dti = round(v + float(rng.uniform(0, 0.9)), 1); dti_lo, dti_hi = v, v + 1
            cs_mu = {"borderline": 680, "control_approve": 765, "control_deny": 630}[stratum]
            cs = int(np.clip(round(rng.normal(cs_mu, 25)), 600, 820))
            rows.append({
                "profile_id": f"P{len(rows):03d}", "stratum": stratum, "hmda_row_index": int(idx),
                "hmda_action_taken": int(r.action_taken), "hmda_dti_band": band, "dti_band_lo": dti_lo, "dti_band_hi": dti_hi,
                "hmda_denial_reason_1": r["denial_reason-1"], "hmda_denial_reason_2": r["denial_reason-2"],
                "hmda_denial_reason_3": r["denial_reason-3"], "hmda_denial_reason_4": r["denial_reason-4"],
                "hmda_derived_race": r.derived_race, "hmda_census_tract": r.census_tract,
                "annual_income": int(r.income * 1000), "loan_amount": int(r.loan_amount), "property_value": int(r.property_value),
                "ltv_pct": round(float(r.loan_to_value_ratio), 1), "loan_term_months": 360,
                "dti_pct": dti, "credit_score": cs,
                "employment_years": int(rng.integers(1, 13)), "job_title": JOBS[int(rng.integers(len(JOBS)))],
                "reserves_months": round(float(rng.uniform(1, 8)), 1),
            })
    P_ = pd.DataFrame(rows)
    P_["monthly_debt_payments"] = (P_.dti_pct / 100 * P_.annual_income / 12).round(0).astype(int)
    pilot_ids = P_[P_.stratum == "borderline"].profile_id.head(cfg["n_pilot"])
    P_["pilot"] = P_.profile_id.isin(pilot_ids)
    return P_, len(border), len(appr), len(deny)


VARIANTS = {  # variant -> (name slot, zip slot)
    "base":         ("W", "A"),
    "control_dup":  ("W", "A"),   # identical to base: control-vs-control (chance flips)
    "name_b":       ("B", "A"),
    "name_w_placebo": ("W2", "A"),  # same race/gender different White name
    "name_b_placebo": ("B2", "A"),  # same race/gender different Black name (vs name_b)
    "zip_b":        ("W", "B"),
    "zip_placebo":  ("W", "A2"),  # two low-minority matched tracts
    "anchor":       (None, None), # no name, no ZIP (separate anchor; not a pair member for proxy tests)
}
# Pair definitions used by analysis: (pair_type, proxy, variant_1, variant_2)
PAIRS = [
    ("proxy", "name", "base", "name_b"),
    ("proxy", "zip", "base", "zip_b"),
    ("placebo", "name", "base", "name_w_placebo"),
    ("placebo", "name_black", "name_b", "name_b_placebo"),
    ("placebo", "zip", "base", "zip_placebo"),
    ("control", "none", "base", "control_dup"),
    ("cross", "name_vs_zip", "name_b", "zip_b"),
]


def build_variants(prof, name_pairs, tract_pairs, placebo_pairs, rng):
    rows = []
    np_by_g = {g: name_pairs[name_pairs.gender == g].reset_index(drop=True) for g in "FM"}
    for i, p in prof.reset_index(drop=True).iterrows():
        gen = "F" if i % 2 == 0 else "M"  # alternate gender strata
        npg = np_by_g[gen]
        j = int(rng.integers(len(npg))); j2 = (j + 1 + int(rng.integers(len(npg) - 1))) % len(npg)
        names = {"W": npg.name_w[j], "B": npg.name_b[j], "W2": npg.name_w[j2], "B2": npg.name_b[j2]}
        tp = tract_pairs.iloc[int(rng.integers(len(tract_pairs)))]
        pp = placebo_pairs[(placebo_pairs.state == tp.state) & (placebo_pairs.tract_a != tp.tract_a) & (placebo_pairs.zip_a != tp.zip_a) & (placebo_pairs.zip_b != tp.zip_a)]
        pp = pp.iloc[int(rng.integers(len(pp)))] if len(pp) else None
        zips = {"A": tp.zip_a, "B": tp.zip_b, "A2": None if pp is None else pp.zip_a}
        tracts = {"A": tp.tract_a, "B": tp.tract_b, "A2": None if pp is None else pp.tract_a}
        for v, (ns, zs) in VARIANTS.items():
            if zs == "A2" and pp is None: continue
            d = p.to_dict()
            d.update({"variant": v, "variant_id": f"{p.profile_id}-{v}", "name_gender": gen,
                      "applicant_name": names[ns] if ns else None, "name_slot": ns,
                      "property_zip": zips[zs] if zs else None, "zip_slot": zs,
                      "proxy_tract": tracts[zs] if zs else None, "proxy_state": tp.state})
            rows.append(d)
    return pd.DataFrame(rows)


def pair_diff_check(var):
    """Every defined pair must differ ONLY in proxy fields; returns DataFrame of violations (empty == pass)."""
    meta = {"variant", "variant_id", "name_slot", "zip_slot", "proxy_tract"}  # proxy_state is identical within a profile
    viol = []
    for pid, g in var.groupby("profile_id"):
        g = g.set_index("variant")
        for ptype, proxy, a, b in PAIRS:
            if a not in g.index or b not in g.index: continue
            ra, rb = g.loc[a], g.loc[b]
            diff = [c for c in g.columns if c not in meta and not (pd.isna(ra[c]) and pd.isna(rb[c])) and ra[c] != rb[c]]
            allowed = {"name": {"applicant_name"}, "name_black": {"applicant_name"}, "zip": {"property_zip"},
                       "none": set(), "name_vs_zip": {"applicant_name", "property_zip"}}[proxy]
            bad = set(diff) - allowed
            must = allowed if ptype != "control" else set()
            missing = must - set(diff)
            if bad or (ptype != "cross" and missing):
                viol.append({"profile_id": pid, "pair": f"{a}|{b}", "unexpected_diffs": sorted(bad), "missing_diffs": sorted(missing)})
    return pd.DataFrame(viol, columns=["profile_id", "pair", "unexpected_diffs", "missing_diffs"])


def band_check(prof):
    bad = prof[~((prof.dti_pct >= prof.dti_band_lo) & (prof.dti_pct < prof.dti_band_hi))]
    return bad[["profile_id", "dti_pct", "hmda_dti_band"]]


def build(cfg=None, outdir=None):
    cfg = cfg or load_cfg()
    outdir = outdir or P("data", "processed")
    os.makedirs(outdir, exist_ok=True)
    rng = np.random.default_rng(cfg["seed"])
    raw = pd.concat([pd.read_csv(P(f), dtype=str, keep_default_na=True) for f in cfg["hmda_files"]], ignore_index=True)
    schema = schema_check(raw)
    miss = missingness(raw)
    kept, excl, steps = clean_and_filter(raw, cfg)
    tt = tract_table(raw, cfg)
    tp = match_tract_pairs(tt, cfg, placebo=False)
    pp = match_tract_pairs(tt, cfg, placebo=True)
    pools = name_pools(cfg); npairs = pair_names(pools)
    prof, n_border, n_appr, n_deny = synth_profiles(kept, cfg, rng)
    var = build_variants(prof, npairs, tp, pp, rng)
    out = dict(schema_problems=schema, missingness=miss, exclusions=excl, filter_steps=steps, tracts=tt, tract_pairs=tp,
               placebo_tract_pairs=pp, name_pools=pools, name_pairs=npairs, profiles=prof, variants=var,
               pool_sizes={"borderline": n_border, "control_approve": n_appr, "control_deny": n_deny})
    for k in ["missingness", "exclusions", "filter_steps", "tracts", "tract_pairs", "placebo_tract_pairs", "name_pools", "name_pairs", "profiles", "variants"]:
        out[k].to_csv(os.path.join(outdir, f"{k}.csv"), index=False)
    with open(os.path.join(outdir, "build_meta.json"), "w") as f:
        json.dump({"seed": cfg["seed"], "schema_problems": schema, "pool_sizes": out["pool_sizes"],
                   "variants_sha256": hashlib.sha256(var.to_csv(index=False).encode()).hexdigest()}, f, indent=2)
    return out


if __name__ == "__main__":
    o = build()
    print("schema problems:", o["schema_problems"]); print(o["filter_steps"].to_string())
    print("pool sizes", o["pool_sizes"]); print("tract pairs", len(o["tract_pairs"]), "placebo", len(o["placebo_tract_pairs"]))
    print(o["tract_pairs"].to_string()); print(o["name_pairs"].to_string())
    print("variants", len(o["variants"]), "diff violations", len(pair_diff_check(o["variants"])))
