# Design notes, deviations from plan, and open decisions (Developer, 2026-10-12)

## What is implemented (maps to plan s.15 / 15.1 / 15.2 and Reviewer novelty check)
- **Variants per profile (8):** `base` (White name W, ZIP A), `control_dup` (identical to base, for the chance-flip floor), `name_b` (Black name, ZIP A), `name_w_placebo` (another White name, ZIP A), `name_b_placebo` (another Black name vs `name_b`), `zip_b` (name W, high-minority ZIP B), `zip_placebo` (name W, another low-minority ZIP A2), `anchor` (no name, no ZIP).
- **Pairs** (`build_data.PAIRS`): proxy name, proxy ZIP, 3 placebo pairs, control-vs-control, and cross (name_b vs zip_b; proxy vs proxy directly). An automated diff verifies that each pair differs only in the allowed fields.
- **Names:** Crabtree et al. (2023) perception data. 10 names per race × gender cell (W/B × F/M), ranked by race-recognition rate. Within gender, each Black name is matched to an unused White name on perceived income + education.
- **ZIP/tracts:** low (≤20% minority) vs high (≥50%) tracts in the same state and MSA, with tract income % of MSA within ±10 pts and FHFA HPI growth 2019–24 within ±10 pts. The owner-occupancy ratio is a tie-breaker. Matching is greedy one-to-one on both tracts and ZIPs. Placebo pairs are two low-minority tracts under the same rules, in the same state as the profile's main pair.
- **Repeats:** 3 per variant. **Field order** is shuffled per call with seed = sha256(run_seed|profile|repeat), which is logged. It does not depend on the variant, so both members of a pair see the same order.
- **Setups:** 1 (LLM decides), 2 (LLM with executed `score_application` tool; logs `tool_called`, tool args/results, `override`), 3 (tool decides, LLM writes the letter; logs the full letter, codes and override). Setup 3 runs first.
- **Reasoning capture:** OpenAI reasoning summaries, Anthropic thinking blocks, Gemini thought summaries (`reasoning`, `reasoning_kind`).
- **Reg B:** the models cite Form C-1 codes C01–C24. Two creditor-added codes are allowed under App. C ¶3: X01 cash/reserves and X02 credit score. The tool's reason codes use the same list. The four HMDA `denial_reason` fields are carried into every call record, and a Reg B → HMDA crosswalk (`regb.REGB_TO_HMDA`, author-defined) is used for the comparison.
- **Blinding** (`blind.py`): deterministic redaction of name tokens, ZIPs, 5-digit numbers, street addresses, tract numbers and state names. The judge and label export never see variant labels; `blind_key.jsonl` holds the mapping. **M4** spans are detected on the original text (name/ZIP/location/demographic keywords, skipping salutation lines), and only the redacted span is judged.
- **Labels:** `labels.py export` writes ≥100 shuffled, blinded items (hedging + span tasks) to `labels_for_gowtham.csv`. `labels.py kappa` reports Cohen's κ per task and flags κ≥0.7.
- **Analysis** (`analysis.py`): majority-of-3 decisions; flip rate net of the control-vs-control rate; exact McNemar; mixed-effects logistic `deny ~ proxy × C(model) + C(effort) + C(state) + (1|profile)` (statsmodels BinomialBayesMixedGLM, variational Bayes); within-pair label-swap permutation (10,000) for M1a (JSD of codes on fixed denials, plus per-code paired diffs) and M1b (hedging rate); Holm over the fixed 8-test family; cluster-bootstrap 95% CIs; Wilson CI fallback trigger; fixed approvals on M1b reported as exploratory. Pilot runs are tagged `excluded_from_confirmatory: true` and `load_calls(confirmatory=True)` drops them.
- **Cost controls:** `BudgetGuard` checks the worst-case cost of each call against the total and per-vendor caps *before* the call, and keeps a persisted ledger. Paid vendors refuse to run without `--allow-paid`. Retries use exponential backoff on transient errors.

## data-gate-v2 changes (2026-10-12, amendment 15.3 + Reviewer data-report review)
- **Names:** distinct first names per race x gender, every full name >= 0.70 recognition, surnames from Crabtree. Crabtree's 100 White + 100 Black names contain only 20 distinct first names, so the target of 10 is unreachable. Qualifying distinct first names: **W-F 1 (Mary), W-M 9, B-F 7, B-M 1 (Jermaine)**; shortfalls 9 / 1 / 3 / 9. Per 15.3 #3 the analysis is restricted to qualifying names (Roosevelt max 0.49 and Darnell max 0.69 excluded). Pairing within gender on perceived income + education: female = 7 pairs, all with Mary Meyer as the White name; male = 1 pair (David Ryan vs Jermaine Wood). Placebo names: a different first name where the group has one (W-M, B-F); otherwise the same first name with the second-best surname (W-F: Mary Ryan; B-M: Jermaine Jackson).
- **Tracts:** MSA 99999 excluded from matching; gate check `tract_pairs_same_metro_not_99999`; placebo |minority gap| <= 10. Each profile's ZIP pairs come from its own HMDA state (removes the 55% cross-state mismatch Reviewer flagged).
- **Scoring cutoff 54.5** (15.3 #2). Pilot fixed-denial rate: 12/25 = **48%** (borderline overall 50.7%). The borderline median in the current DE+MD+PA sample is 54.39; 54.5 came from the earlier DE-only sample, and the pre-registered constant 54.5 was kept.
- **Batch mode** (`llmbias.batch`): OpenAI Batch (/v1/responses), Anthropic Message Batches, Gemini Batch (inlined). Polling, atomic resumable state (`batch_state.json`, `--resume`), two-round tool flow for setup 2, and a chunk-level hard stop: each batch is sized so that spent + in-flight worst case + this chunk's worst case (input estimate + max_output_tokens at batch price) <= the total and per-vendor caps.
- **Projected-cost trimming:** if projected batch cost exceeds the total cap OR any per-vendor cap, setup-2 profiles are dropped in a pre-seeded random order (`trim_seed`), the same profiles for every vendor; repeats are never cut. **Conflict:** 15.3 #1 says "repeats are cut for setup 2 first"; Gowtham's instruction (drop profiles, never repeats) was implemented. 15.3 #1 needs updating.
- **Reasoning:** lowest setting per vendor in config (OpenAI effort "none", Anthropic thinking disabled, Gemini thinking_budget 0), each verified live. `reasoning_tokens` and `visible_output_tokens` are logged separately (Anthropic: SDK value if exposed).
- **Fallback trigger:** Newcombe (1998, method 10) paired-difference CI on proxy flip minus control flip per (model, profile) unit; paired cluster bootstrap if degenerate.
- **Confirmatory GLMM:** `R/glmer_fit.R` (lme4 1.1.37, R 4.5.0, Laplace nAGQ=1, bobyqa) called via subprocess; VB fit kept as sensitivity.
- **System prompt says "Follow ECOA/Regulation B":** a design choice applied equally to all arms (Reviewer minor); it may lower base rates.

## Deviations from the plan / items needing Researcher sign-off
1. **Gender of first names is author-coded.** The Crabtree data has no gender field, and SSA baby-name data could not be downloaded (blocked).
2. *(superseded in v2: see above)* **Black male names have weak race recognition.** 7 of 10 are below 0.70 (range 0.62–0.75), against 0.85–0.93 for White male names. The female cell is strong (0.79–0.89). Recommendation: treat the female cell as primary, or report name effects by gender cell.
3. **Census 2010 surname file** was downloaded but is not used in name selection, since Crabtree's perception scores take its place.
4. **Credit score, job, tenure and reserves are synthetic** (HMDA lacks them). Credit score ~N(680, 25) for borderline cases, clipped to 600–820. DTI in 40–49 is HMDA's exact integer plus U(0, 0.9).
5. *(resolved in v2: cutoff 54.5)* **Scorecard calibration:** the author-defined scorecard at threshold 60 denies 72% of pilot borderline profiles, while HMDA's real denial rate for the sampled borderline profiles is 4%. This works for setup 3 (fixed denials are the primary unit) but is not realistic. Decision needed: keep 60 or set the threshold at, e.g., the borderline median (54.5).
6. **"Control" redefined:** per Reviewer #9, the proxy comparisons keep the same fields (W vs B name with the same ZIP; ZIP A vs B with the same name). The plan's "neutral name, no ZIP" control is kept as the separate `anchor` arm.
7. **ZIP = ZCTA with the largest land overlap with the tract.** Some ZIPs span tracts with different demographics.
8. **The white-applicant denial rate per tract** is computed from all 2024 HMDA rows (actions 1/3) with small n (9–200). Mean absolute difference across proxy pairs: 0.083. Reported, not matched.
9. *(resolved in v2: glmer is confirmatory)* **Mixed model:** uses statsmodels' variational Bayes GLMM rather than a frequentist glmer. To be decided whether R/lme4 is wanted for the confirmatory run.
10. *(resolved in v2: Newcombe)* **Wilson fallback on the *net* rate:** computed as the Wilson upper bound on the raw proxy flip rate minus the control flip rate (point estimate). Researcher to confirm.
11. *(v2: implemented, but live batch submission is untested; no paid batch was authorized)* **Batch API** Only the estimator models batch pricing. The synchronous runner and hard stop are implemented.
12. *(v2: thinking-off and tool calling verified live for the sync adapters; batch backends untested live; the judge still runs synchronously, not via batch)* **Real adapters** Their tool-calling and thinking/summary capture paths have not been exercised against live APIs, since no pilot calls were allowed.

## data-gate-v3 changes (2026-10-12, amendments 15.5 / 15.6; Anthropic cap)
- **Name rule (15.6, final, fixed before outcome data):** Crabtree et al. 2023 full names with recognition >= 0.70, or Elder & Hayes 2023
  (data/raw/eh/nameratings.tsv, copied from /workspace/research/eh; sha256 59c5e598...) with target-race mean >= 3.5, gap over the other race >= 1.0,
  Tzioumis census target share >= 30%. Same rule for all four groups. Qualifying distinct first names: WF 134, WM 102, BF 14, BM 5
  (BM = Darius, Jermaine, Lamont, Terrell, Tyrone). Strict 15.5 subset (mean >= 3.75, share >= 50%): WF 67, WM 54, BF 7, BM 2 (Jermaine, Tyrone).
- **Source assignment:** every Black name that qualifies under Crabtree also qualifies under E&H, so it goes to E&H (the source with large
  White pools and class traits). Result: all 19 pairs fall within E&H. `name_source` is recorded on every name variant and call record and
  enters the GLMMs as a fixed effect when it has more than one level (it is constant in v3, so it drops out). Crabtree still contributes eligible names that end up unused.
- **Pairing:** one White partner per Black first name, within gender, using a Hungarian assignment. Cost = |class_z gap| + |era gap|/10 years, plus penalties
  for cross-source pairs, a missing White class score when the Black name has one, and a non-strict White partner for a strict Black name.
  class_z = E&H "upper class" minus "working class" (traits file, 336 names), z-scored within source. Era = median SSA birth year 1940-2000
  (CRAN babynames 1.0.1, SSA 1880-2017; ssa.gov blocks our egress). 8 of 19 pairs lack class ratings on at least one side, so those are matched on era only (limitation).
- **Surnames:** E&H rated first names alone, so each E&H pair shares one frequent 2010-Census surname with 35-60% White and 35-60% Black shares
  (seeded). The two arms differ only in the first name. Placebo names keep the base surname and come from the same source.
- **Profile assignment:** pairs are cycled round-robin over a seeded shuffle within gender, so each pair is used about equally (F pairs 6-7 profiles each, M pairs 17).
- **Name-level inference (15.5), with no name random effect:** `analysis.name_level` gives per-name paired deny differences (majority-of-3), CR1
  cluster-robust mean with name-pair clusters (t, G-1 df), an exact or Monte Carlo name-level sign-flip permutation, and leave-one-name-out (LONO). LONO passes when the sign is kept and the
  estimate falls inside the full-sample 95% CI. `robust_confirmed` = Holm-adjusted M3_name < .05 AND all LONO pass. The same analysis is run
  on the strict 15.5 subset (`name_level_strict_15_5_sensitivity`).
- **Budget:** Anthropic per-vendor cap raised to $7 (total stays $15), per Gowtham, so all 25 profiles stay in setup 2 with no trim.
- Candidate sources evaluated but not integrated: see docs/name_sources.md.

## data-gate-v3.1 (2026-10-12)
- **Fallback-bound tie (0.434 for name and ZIP): not a bug.** In the mock run both proxies had 6/25 flips and 0 control flips, so both had the
  same 2x2 table [0,6,0,19]. Newcombe depends only on those counts, so the bounds matched. Different profiles flipped (name: P000,P011,P014,P016,P020,P022;
  ZIP: P004,P011,P014,P016,P019,P022). The real gap was that the mock could not inject ZIP bias at all. It now accepts `bias: {zip: p, zip_treated: [...]}`.
  With name 0.9 and ZIP 0.25 the bounds differ (name 8/25 flips, upper 0.516; ZIP 3/25, upper 0.300). The regression test is `test_fallback_bounds_track_each_proxy`.
- **Pairing:** Hungarian (scipy linear_sum_assignment) within gender, one unique White partner per Black name. Cost = 1.0 x |class_z gap| + 1.0 x |era gap|/10 y
  (1 SD == 10 years), plus penalties: 100 cross-source, 50 when the White class score is missing but the Black one exists, 5 for a non-strict White partner of a strict Black name.
  `sensitivity_only` = class gap > 1 SD or era gap > 10 y. `no_class` = class rating missing on either side. Analyses: `name_level_excl_sensitivity_only`, `name_level_excl_no_class`.
- **Shared surnames:** 2010 Census surnames with count >= 10k, ranked by smallest |pctwhite - pctblack|. Added constraint: pctwhite + pctblack >= 80. Without it, the literal rule picks
  Asian surnames with ~1% White and ~1% Black (Chung, Xiong, Singh), which would add an Asian signal. Deviation flagged for the Reviewer.
- Judge: batch by default (`--sync` = debug only); gpt-5-nano `minimal` verified live, 0 reasoning tokens (docs/live_checks_judge.json).
- openpyxl moved to requirements-dev.txt (dev only).
