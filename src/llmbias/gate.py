"""Data-quality gate (plan s.10b). Writes reports/data_report.md, CSVs and figures (+ raw CSV per figure).
  python -m llmbias.gate"""
import json, os, subprocess
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np, pandas as pd
from .build_data import P, build, load_cfg, pair_diff_check, band_check, schema_check

def names_rule_ok(pools, cfg):
    """Each pool name: Crabtree >= min_recognition, or E&H 15.6 (mean >= 3.5, gap >= 1.0, census share >= 30)."""
    cr = pools.name_source == "crabtree2023"
    eh = (pools.name_source == "elder_hayes2023") & (pools.eh_target_mean >= 3.5) & (pools.eh_gap >= 1.0) & (pools.census_share >= 30)
    return ((cr & (pools.race_recognition >= cfg["names"]["min_recognition"])) | eh).all()


def fig(df, col, by, path, bins=20):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    plt.figure(figsize=(6, 3.5))
    for k, g in df.groupby(by):
        plt.hist(g[col].dropna(), bins=bins, alpha=0.5, label=str(k))
    plt.xlabel(col); plt.ylabel("count"); plt.legend(fontsize=7); plt.tight_layout(); plt.savefig(path, dpi=120); plt.close()
    df[[by, col]].to_csv(path.replace(".png", ".csv"), index=False)

def run_gate():
    cfg = load_cfg(); out = build(cfg); rep = P("reports"); figd = os.path.join(rep, "figures"); os.makedirs(figd, exist_ok=True)
    out2 = build(cfg, outdir=P("data", "processed", "_repro_check"))
    repro = out["variants"].to_csv(index=False) == out2["variants"].to_csv(index=False)
    subprocess.run(["rm", "-rf", P("data", "processed", "_repro_check")])
    var, prof = out["variants"], out["profiles"]
    diffs = pair_diff_check(var); bands = band_check(prof)
    tp, pp, npairs = out["tract_pairs"], out["placebo_tract_pairs"], out["name_pairs"]
    checks = {
        "schema": not out["schema_problems"],
        "seed_reproducible": repro,
        "pair_diffs_only_proxy": diffs.empty,
        "synthetic_dti_in_hmda_band": bands.empty,
        "credit_score_range_300_850": bool(prof.credit_score.between(300, 850).all()),
        "ltv_range_0_200": bool(prof.ltv_pct.between(0, 200).all()),
        "income_positive": bool((prof.annual_income > 0).all()),
        "no_missing_in_profile_fields": not prof[["annual_income", "loan_amount", "property_value", "ltv_pct", "dti_pct", "credit_score", "employment_years", "reserves_months"]].isna().any().any(),
        "names_meet_15.6_rule": bool(names_rule_ok(out["name_pools"], cfg)),
        "name_first_names_distinct_within_group": bool(out["name_pools"].groupby(["race", "gender"])["first"].apply(lambda s: s.is_unique).all()),
        ">=5_first_names_per_race_gender": bool(min(out["name_counts"]["rule_15_6"].values()) >= 5),
        "every_black_name_paired_once": bool(npairs.groupby("gender").first_b.apply(lambda s: s.is_unique).all() and len(npairs) == (out["name_pools"].race == "B").sum()),
        "white_partner_unique": bool(npairs.groupby("gender").first_w.apply(lambda s: s.is_unique).all()),
        "name_pairs_same_gender": bool(all(npairs.name_w.notna() & npairs.name_b.notna())),
        "name_pairs_all_within_one_source": bool(npairs.same_source.all()),
        "name_source_recorded_on_name_variants": bool(var[var.name_slot.notna()].name_source.notna().all()),
        "name_pair_id_on_all_profiles": bool(var.name_pair_id.notna().all()),
        "strict_15_5_flag_present": bool(var.name_pair_strict_155.notna().all()),
        "eh_pairs_share_surname": bool((npairs[npairs.shared_surname.notna()].apply(lambda r: r.name_w.split(" ", 1)[1] == r.name_b.split(" ", 1)[1] == r.shared_surname, axis=1)).all()),
        "placebo_same_source_as_base": bool((var[var.variant == "name_w_placebo"].set_index("profile_id").name_source == var[var.variant == "base"].set_index("profile_id").name_source).dropna().all() and (var[var.variant == "name_b_placebo"].set_index("profile_id").name_source == var[var.variant == "name_b"].set_index("profile_id").name_source).dropna().all()),
        "no_name_random_effect_in_models": "name" not in open(P("R", "glmer_fit.R")).read().split("glmer(")[1].split(")")[0] and "name_b" not in open(P("src", "llmbias", "analysis.py")).read().split("def glmer_fit")[1].split("def ")[0].replace("(1 | profile_id)", ""),
        "placebo_names_differ_from_base": bool((var[var.variant.isin(["name_w_placebo"])].set_index("profile_id").applicant_name != var[var.variant == "base"].set_index("profile_id").applicant_name).all()),
        ">=15_tract_pairs": len(tp) >= 15, ">=5_placebo_tract_pairs": len(pp) >= 5,
        "tract_pairs_same_state": bool((tp.tract_a.str[:2] == tp.tract_b.str[:2]).all() and (pp.tract_a.str[:2] == pp.tract_b.str[:2]).all()),
        "tract_pairs_same_metro_not_99999": bool(((tp.msa.astype(str) != "99999").all()) and ((pp.msa.astype(str) != "99999").all())),
        "placebo_minority_gap_<=10pts": bool(((pp.minority_a - pp.minority_b).abs() <= cfg["tracts"]["placebo_max_minority_gap"]).all()),
        "profile_zip_pair_from_own_state": bool((var.dropna(subset=["proxy_state"]).hmda_state == var.dropna(subset=["proxy_state"]).proxy_state).all()),
        "tract_pairs_income_within_10pts": bool(((tp.income_pct_a - tp.income_pct_b).abs() <= 10).all()),
        "tract_pairs_hpi_within_10pts": bool(((tp.hpi_growth_a - tp.hpi_growth_b).abs() <= 10).all()),
    }
    # distributions: borderline sample vs HMDA borderline pool (realism)
    kept = pd.read_csv(P("data", "processed", "profiles.csv"))
    fig(prof, "dti_pct", "stratum", os.path.join(figd, "dti_by_stratum.png"))
    fig(prof, "credit_score", "stratum", os.path.join(figd, "credit_score_by_stratum.png"))
    fig(prof, "ltv_pct", "stratum", os.path.join(figd, "ltv_by_stratum.png"))
    fig(prof.assign(income_k=prof.annual_income / 1000), "income_k", "stratum", os.path.join(figd, "income_by_stratum.png"))
    td = pd.concat([tp.assign(kind="proxy"), pp.assign(kind="placebo")])
    td["income_pct_diff"] = td.income_pct_a - td.income_pct_b; td["hpi_growth_diff"] = td.hpi_growth_a - td.hpi_growth_b
    td["minority_diff"] = td.minority_b - td.minority_a; td["white_denial_diff"] = td.white_denial_b - td.white_denial_a
    for c in ["income_pct_diff", "hpi_growth_diff", "minority_diff", "white_denial_diff"]:
        fig(td, c, "kind", os.path.join(figd, f"tractpair_{c}.png"), bins=15)
    summ = prof.groupby("stratum")[["annual_income", "loan_amount", "property_value", "ltv_pct", "dti_pct", "credit_score", "employment_years", "reserves_months"]].describe().T
    summ.to_csv(os.path.join(rep, "profile_summary.csv"))
    hmda_out = prof.groupby("stratum").hmda_action_taken.apply(lambda s: (s == 3).mean()).rename("hmda_denial_rate")
    hmda_out.to_csv(os.path.join(rep, "hmda_outcome_by_stratum.csv"))
    dr = prof[prof.hmda_action_taken == 3][[f"hmda_denial_reason_{i}" for i in range(1, 5)]].stack().astype(str).str.split(".").str[0].value_counts()
    dr.rename("count").to_csv(os.path.join(rep, "hmda_denial_reasons_sample.csv"))
    excl_summary = out["exclusions"].groupby(["rule", "detail"]).size().rename("n").reset_index()
    excl_summary.to_csv(os.path.join(rep, "exclusions_summary.csv"), index=False)
    pd.DataFrame([{"check": k, "pass": v} for k, v in checks.items()]).to_csv(os.path.join(rep, "gate_checks.csv"), index=False)
    md = ["# Data report (data-quality gate, plan s.10b)", "",
          "Generated by `python -m llmbias.gate`. Raw CSVs for every figure sit next to the PNG in `reports/figures/`.", "",
          "## Gate checks", "", "| check | pass |", "|---|---|"] + [f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in checks.items()] + [
          "", "## Pre-data design changes (recorded before any model run)",
          "- 2026-10-12: HMDA scope expanded from DE only to DE+MD+PA (2024) because DE alone produced 3 matched / 5 placebo tract pairs. Same filters. Tract pairs constrained to the same state and the same MSA.",
          "- Tract pairs are matched on tract income % of MSA (±10 pts) and FHFA tract HPI growth 2019-2024 (±10 pts); owner-occupancy ratio used as a tie-breaker.",
          "- 2026-10-12 (data-gate-v2, amendment 15.3 + Reviewer data-report review): names restricted to DISTINCT first names with recognition >= 0.70 (shortfall below); MSA 99999 (non-metro) pairs dropped; placebo tract minority-share gap capped at 10 pts; each profile's ZIP pairs drawn from the profile's own (HMDA) state; scoring cutoff 54.5.",
          "- Variants: 8 per profile (base, control_dup, name_b, name_w_placebo, name_b_placebo, zip_b, zip_placebo, anchor). The proxy comparison keeps both members' fields identical (name vs name with the same ZIP; ZIP vs ZIP with the same name). The no-proxy anchor is kept as a separate arm (Reviewer #9).",
          "", "## Filter steps", "", out["filter_steps"].to_markdown(index=False), "",
          f"Pool sizes after filters: {out['pool_sizes']}. Sampled: {prof.stratum.value_counts().to_dict()}; pilot = first {int(prof.pilot.sum())} borderline profiles.", "",
          "## Exclusions (missing/exempt and range rules)", "", excl_summary.to_markdown(index=False), "",
          "## Missingness in raw HMDA (key fields)", "", out["missingness"].to_markdown(index=False), "",
          "## HMDA outcome in sampled profiles (realism)", "", hmda_out.to_frame().to_markdown(), "",
          "HMDA denial reasons among sampled denied profiles (codes: 1 DTI, 2 employment, 3 credit history, 4 collateral, 5 cash, 6 unverifiable, 7 incomplete, 8 MI denied, 9 other, 10 NA):", "", dr.to_frame().to_markdown(), "",
          "## Profile field distributions", "", summ.round(2).to_markdown(), "",
          "## Names (amendments 15.5 / 15.6; rule fixed before outcome data)", "",
          "Rule (all four groups): Crabtree et al. 2023 full-name recognition >= 0.70, or Elder & Hayes 2023 target-race mean >= 3.5, gap over other race >= 1.0, census (Tzioumis) target share >= 30%. Stricter 15.5 subset (mean >= 3.75, share >= 50%) flagged for sensitivity.", "",
          "Qualifying distinct first names: 15.6 " + json.dumps(out["name_counts"]["rule_15_6"]) + "; strict 15.5 " + json.dumps(out["name_counts"]["strict_15_5"]), "",
          "Black-name pools (all used):", "",
          out["name_pools"][out["name_pools"].race == "B"][["gender", "first", "name_source", "eh_target_mean", "eh_gap", "census_share", "race_recognition", "class_z", "era_year", "strict_155"]].round(2).to_markdown(index=False), "",
          "Pairs (within gender; Hungarian assignment on class_z gap + era gap/10, same source preferred; E&H pairs share one census surname with 35-60% White and 35-60% Black shares). class_z = E&H 'upper class' minus 'working class', z-scored within source; NaN = no E&H trait ratings for that name (era-only match). era = median SSA birth year 1940-2000 (CRAN babynames 1.0.1).", "",
          npairs.drop(columns=["rec_w", "rec_b"]).round(2).to_markdown(index=False), "",
          f"Residual imbalance: mean |class gap| {npairs.class_gap.abs().mean():.2f} SD over {int(npairs.class_matched.sum())}/{len(npairs)} class-matched pairs; {int((~npairs.class_matched).sum())} pairs lack class data on at least one side; mean |era gap| {npairs.era_gap.abs().mean():.1f} y. Cross-source pairs: {int((~npairs.same_source).sum())}.", "",
          f"## Tract/ZIP pairs: {len(tp)} proxy (low ≤20% vs high ≥50% minority), {len(pp)} placebo (both ≤20%)", "",
          f"By state, proxy: {tp.state.value_counts().to_dict()}; placebo: {pp.state.value_counts().to_dict()}", "",
          "Proxy-pair balance (mean absolute diff): " + json.dumps({c: round(float(td[td.kind == 'proxy'][c].abs().mean()), 3) for c in ["income_pct_diff", "hpi_growth_diff", "minority_diff", "white_denial_diff"]}), "",
          tp.round(3).to_markdown(index=False), "",
          "## Pair-diff check", "", f"{len(diffs)} violations across {var.profile_id.nunique()} profiles x {len(var.variant.unique())} variants.", ""]
    open(os.path.join(rep, "data_report.md"), "w").write("\n".join(md))
    return checks

if __name__ == "__main__":
    c = run_gate(); print(json.dumps(c, indent=1)); raise SystemExit(0 if all(c.values()) else 1)
