"""Name pools and pairs under pre-registration amendments 15.5 / 15.6 (final rule, fixed 2026-10-12, before outcome data).

Qualifying first names (identical rule for all four race x gender groups):
  * Crabtree et al. 2023: full-name forced-choice recognition >= 0.70, or
  * Elder & Hayes 2023 (15.6): target-race mean >= 3.5 (1-5), target minus other-race mean >= 1.0,
    census (Tzioumis) target-race share >= 30%.
  Stricter 15.5 subset (sensitivity only): E&H mean >= 3.75, gap >= 1.0, share >= 50%  (Crabtree >= 0.70 also counts).
Source assignment: a first name qualifying under both sources is assigned to Elder & Hayes (its White pools are large
enough to allow within-source pairing and it carries class traits); Crabtree-only names stay Crabtree.
Pairing (within gender): every qualifying Black first name gets one White partner, same source where possible,
chosen without replacement to minimise |class_z difference| + |era difference| / 10 years.
  class: E&H 'upper class' minus 'working class' rating (traits file, 336 names); Crabtree perceived income + education.
         z-scored within source over that source's qualifying names. Missing class -> era-only match, flagged.
  era:   median birth year 1940-2000 from SSA national baby-name counts (CRAN babynames 1.0.1, SSA through 2017).
Surnames: Crabtree names keep their rated full names. E&H names were rated as first names alone, so each E&H pair shares one
surname (drawn, seeded, from frequent 2010-Census surnames with 35-60% White and 35-60% Black shares); the two arms then differ
only in the first name. E&H placebo names reuse the base name's surname.
"""
import os
import numpy as np, pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RAW = os.path.join(ROOT, "data", "raw")
RULE_156 = dict(mean=3.5, gap=1.0, share=30.0)
RULE_155 = dict(mean=3.75, gap=1.0, share=50.0)
TARGET = {"W": ("white", "African.American", "pctwhite"), "B": ("African.American", "white", "pctblack")}


def eh_rule(N, race, rule):
    t, o, s = TARGET[race]
    return (N[t] >= rule["mean"]) & (N[t] - N[o] >= rule["gap"]) & (N[s] >= rule["share"])


def era_table(path=None):
    a = pd.read_csv(path or os.path.join(RAW, "ssa_name_decade.csv"))
    a = a[(a.decade >= 1940) & (a.decade <= 2000)]
    def med(g):
        g = g.sort_values("decade"); c = g.n.cumsum(); return float(g.decade[c >= c.iloc[-1] / 2].iloc[0]) + 5
    return {(n, s): med(g) for (n, s), g in a.groupby(["name", "sex"])}


def eh_candidates():
    N = pd.read_csv(os.path.join(RAW, "eh", "nameratings.tsv"))  # comma-separated despite the extension
    T = pd.read_csv(os.path.join(RAW, "eh", "traits.tsv"))
    cls = dict(zip(T.name, T["upper class"] - T["working class"]))
    rows = []
    for race in "WB":
        t, o, s = TARGET[race]
        q = N[eh_rule(N, race, RULE_156)].copy()
        q["strict_155"] = eh_rule(q, race, RULE_155)
        for _, r in q.iterrows():
            rows.append(dict(first=r["name"], race=race, gender=r.gender, name_source="elder_hayes2023", eh_target_mean=r[t],
                             eh_gap=r[t] - r[o], census_share=r[s], race_recognition=np.nan, class_raw=cls.get(r["name"], np.nan),
                             strict_155=bool(r.strict_155), crab_full_name=None, crab_alt_name=None))
    return pd.DataFrame(rows)


def crab_candidates(ratings, thr):
    q = ratings[ratings.race_recognition >= thr].sort_values(["race_recognition", "name"], ascending=[False, True])
    rows = []
    for (first, race, gen), g in q.groupby(["first", "race", "gender"]):
        rows.append(dict(first=first, race=race, gender=gen, name_source="crabtree2023", eh_target_mean=np.nan, eh_gap=np.nan,
                         census_share=np.nan, race_recognition=g.race_recognition.iloc[0],
                         class_raw=float(g.perceived_income_ord.iloc[0] + g.perceived_edu_ord.iloc[0]), strict_155=True,
                         crab_full_name=g.name.iloc[0], crab_alt_name=g.name.iloc[1] if len(g) > 1 else None))
    return pd.DataFrame(rows)


def name_pools_v3(ratings, cfg):
    thr = cfg["names"]["min_recognition"]
    eh, cr = eh_candidates(), crab_candidates(ratings, thr)
    key = lambda d: set(zip(d["first"], d.race, d.gender))
    both = key(eh) & key(cr)
    cr["also_qualifies_other_source"] = [k in both for k in zip(cr["first"], cr.race, cr.gender)]
    eh["also_qualifies_other_source"] = [k in both for k in zip(eh["first"], eh.race, eh.gender)]
    pools = pd.concat([eh, cr[~cr.also_qualifies_other_source]], ignore_index=True)
    pools["class_z"] = pools.groupby("name_source").class_raw.transform(lambda s: (s - s.mean()) / s.std(ddof=0))
    era = era_table()
    pools["era_year"] = [era.get((f, g), np.nan) for f, g in zip(pools["first"], pools.gender)]
    return pools.sort_values(["race", "gender", "name_source", "first"]).reset_index(drop=True)


def counts(pools, strict=False):
    p = pools[pools.strict_155] if strict else pools
    return {f"{r}{g}": int(p[(p.race == r) & (p.gender == g)]["first"].nunique()) for r in "WB" for g in "FM"}


def _cost(c, r):
    dc = (c.class_z - r.class_z).abs(); de = (c.era_year - r.era_year).abs() / 10
    has = dc.notna()
    return (dc.fillna(0) + de.fillna(3)) + np.where(has, 0, 100), has  # prefer candidates with class data


SURNAME_MIN_COUNT = 10000
SURNAME_MIN_WB_TOTAL = 80.0  # pctwhite + pctblack >= 80: without it the literal |W-B| rule picks Asian surnames (Chung, Xiong: ~1% each)
W_CLASS, W_ERA_PER_10Y = 1.0, 1.0   # cost weights: 1 SD of class index == 10 years of era (documented in design.md)
FLAG_CLASS_SD, FLAG_ERA_Y = 1.0, 10.0


def _surnames(seed=None, k=30):
    """Shared surnames for E&H pairs: common 2010-Census surnames (count >= 10k) with the most even White/Black split,
    i.e. smallest |pctwhite - pctblack|. Deterministic (no seed needed); returned in that order."""
    s = pd.read_csv(os.path.join(RAW, "Names_2010Census.csv"))
    for c in ("pctwhite", "pctblack", "count"): s[c] = pd.to_numeric(s[c], errors="coerce")
    s = s[s["count"] >= SURNAME_MIN_COUNT].dropna(subset=["pctwhite", "pctblack"]).copy()
    s = s[s.pctwhite + s.pctblack >= SURNAME_MIN_WB_TOTAL]
    s["wb_gap"] = (s.pctwhite - s.pctblack).abs()
    s = s.sort_values(["wb_gap", "count"], ascending=[True, False]).head(k)
    s["surname"] = s.name.str.title()
    return list(s.surname), s[["surname", "count", "pctwhite", "pctblack", "wb_gap"]].reset_index(drop=True)


def pair_names_v3(pools, seed):
    """Optimal (Hungarian) assignment within gender: cost = |class_z gap| + |era gap|/10 + 100 if cross-source
    + 50 if class data missing on the White side while the Black name has it + 5 if the Black name meets 15.5 but the White does not."""
    from scipy.optimize import linear_sum_assignment
    sur, _ = _surnames(seed)
    rows, k = [], 0
    for gen in "FM":
        B = pools[(pools.race == "B") & (pools.gender == gen)].sort_values(["name_source", "first"]).reset_index(drop=True)
        W = pools[(pools.race == "W") & (pools.gender == gen)].sort_values(["name_source", "first"]).reset_index(drop=True)
        C = np.zeros((len(B), len(W)))
        for i, b in B.iterrows():
            dc = (W.class_z - b.class_z).abs(); de = (W.era_year - b.era_year).abs() / 10
            C[i] = (W_CLASS * dc.fillna(0) + W_ERA_PER_10Y * de.fillna(3) + np.where(W.name_source != b.name_source, 100, 0)
                    + np.where(pd.notna(b.class_z) & W.class_z.isna(), 50, 0) + np.where(b.strict_155 & ~W.strict_155, 5, 0))
        ri, ci = linear_sum_assignment(C)
        for i, j in zip(ri, ci):
            b, w = B.loc[i], W.loc[j]
            surname = sur[k % len(sur)] if "elder_hayes2023" in (b.name_source, w.name_source) else None; k += 1
            full = lambda r: r.crab_full_name if r.name_source == "crabtree2023" else f"{r['first']} {surname}"
            rows.append(dict(name_pair_id=f"{gen}{len(rows):02d}", gender=gen, name_w=full(w), name_b=full(b), first_w=w["first"], first_b=b["first"],
                             source_w=w.name_source, source_b=b.name_source, same_source=w.name_source == b.name_source,
                             shared_surname=surname, class_w=w.class_z, class_b=b.class_z, class_gap=w.class_z - b.class_z,
                             class_matched=bool(pd.notna(w.class_z) and pd.notna(b.class_z)), era_w=w.era_year, era_b=b.era_year,
                             era_gap=w.era_year - b.era_year, strict_155=bool(w.strict_155 and b.strict_155), strict_155_black=bool(b.strict_155),
                             no_class=not (pd.notna(w.class_z) and pd.notna(b.class_z)),
                             sensitivity_only=bool((pd.notna(w.class_z) and pd.notna(b.class_z) and abs(w.class_z - b.class_z) > FLAG_CLASS_SD)
                                                   or abs(w.era_year - b.era_year) > FLAG_ERA_Y),
                             rec_w=w.race_recognition, rec_b=b.race_recognition, eh_mean_w=w.eh_target_mean, eh_mean_b=b.eh_target_mean))
    return pd.DataFrame(rows)


def placebo_v3(pools, first, race, gen, source, base_full):
    """Same race, gender and source, different first name, nearest class/era; E&H placebo reuses the base surname."""
    r = pools[(pools["first"] == first) & (pools.race == race) & (pools.gender == gen)].iloc[0]
    g = pools[(pools.race == race) & (pools.gender == gen) & (pools.name_source == source) & (pools["first"] != first)]
    if len(g):
        c = g.loc[_cost(g, r)[0].idxmin()]
        if source == "crabtree2023": return c.crab_full_name, "different_first_name"
        return f"{c['first']} {base_full.split(' ', 1)[1]}", "different_first_name"
    if source == "crabtree2023" and r.crab_alt_name: return r.crab_alt_name, "same_first_diff_surname"
    return None, "none"
