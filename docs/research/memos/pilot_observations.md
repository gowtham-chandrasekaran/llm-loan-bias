# Pilot observations memo (Researcher, 2026-10-12)

Run: `runs/20261012T143812-ff54324d-4745d6-batch/`. **PILOT, FEASIBILITY ONLY.** Per plan 15.1 item 4, nothing here is a confirmatory result. The pilot is used only for feasibility, the fallback trigger (15.3 #5), and power (15.1 #5). Raw files were not modified: `raw/` passes `raw_SHA256SUMS`, and all analysis was written to `research/memos/pilot_analysis/`. The setup-2 override rates below are feasibility diagnostics, not findings.

## 1. Independent verification of the headline numbers
I recomputed everything from `raw/calls.jsonl`, `raw/judgments.jsonl` and `raw/spend_ledger.jsonl` with my own code (`pilot_analysis/verify.py`), without importing `llmbias.analysis`.

| Item | Developer | Recomputed | Status |
|---|---|---|---|
| Spend | $10.58 | $10.581 (ledger: OpenAI 1.539, Anthropic 6.940, Google 2.009, judge 0.094) | OK. `run_meta.spent_usd` = $10.487 because it leaves out the judge; minor. |
| Parse rate | 96.2% | 96.24% of planned (5197/5400); 98.45% of completed (5197/5279) | OK, but the 96.2% counts the 121 budget-stopped calls as failures. State the denominator. |
| Name net flips | 0.067 [0.009, 0.145] | 7/75 flips − 2/75 control = 0.0667; Newcombe [0.0090, 0.1455]; paired table a,b,c,d = 2,5,0,68; profile bootstrap [0.013, 0.133] | Matches |
| ZIP net flips | 0.081 [0.019, 0.164] | 8/74 − 2/74 = 0.0811; Newcombe [0.0194, 0.1645]; bootstrap [0.027, 0.149] | Matches. n = 74 because one Google zip_b cell had no majority (parse failures). |
| Fallback | not triggered | Upper bounds 0.145 and 0.164 are both ≥ 0.10, so no fallback | Correct under 15.3 #5 |
| Tool-call rate (setup 2) | 100% for all vendors | 1.00 / 1.00 / 1.00 | OK |
| Override (feasibility only) | OAI 18.2%, Anth 4.6%, Goog 0% | Per call: 0.1817 / 0.0465 (parsed calls; 0.0459 if all completed calls) / 0. Majority-of-3: 0.185 / 0.042 (n = 192) / 0 | Matches per call. The pre-registered unit is majority-of-3, and Anthropic is incomplete (see §2a). |

There are no numerical discrepancies. There are problems with interpretation, and they matter more than the numbers:

- **D1. All name flips come from one vendor.** Name flips by vendor: OpenAI 7/25, Anthropic 0/25, Google 0/25. ZIP flips: OpenAI 6/25, Anthropic 2/25, Google 0/24. Control flips: OpenAI 2/25, others 0/25. The pooled net flip rate is effectively an OpenAI instability rate.
- **D2. The flips have no direction.**
  - Name: the proxy variant was denied in 3 flips and base in 4 (exact McNemar p = 1.0).
  - ZIP: 4 vs 4 (p = 1.0).
- **D3. Placebo pairs flip as often as proxy pairs, or more.**
  - The base vs zip_placebo flip rate is 11/75 = 0.147, net 0.120 [0.026, 0.220].
  - The base vs name_w_placebo flip rate is 8/74, net 0.081. That is numerically identical to the ZIP proxy, but I checked and the flipped pairs are different, so this is a coincidence.
  - The control arm (control_dup) uses a byte-identical prompt to base: same field order and same sha256. Placebo arms change surface text. So control_dup probably **underestimates** chance flips caused by any textual change, and the "net" flip rate overstates proxy-specific flips.
  - This does not change the pre-registered trigger decision, which uses control. It does mean the 0.067 and 0.081 rates should not be read as bias signals.

## 2. Reviewer's checks
### (a) Anthropic budget stop (121 of 600 setup-2 calls)
- **Variant:** perfectly balanced. 15 stopped calls per variant, 16 for anchor. χ²(7) = 0.07, p = 1.0. Proxy vs the rest: Fisher p = 1.0. Name vs ZIP: 15 vs 15.
- **Decision type:** approve 72/312 stopped, deny 49/288. χ² p = 0.08, a mild tilt toward tool-approve profiles.
- **Structure (the real issue):** every stopped call is **repeat 2**, and the stops fall on profiles P009 (1 call) and P010–P024 (8 each). The batch submitted repeats 0 and 1 for all profiles before repeat 2. As a result:
  - only 79 of 200 profile×variant cells have all 3 repeats
  - only 9 of 25 profiles are complete across all variants
  - 121 cells rely on a majority of 2, where a 1–1 split counts as missing
- **Pre-registration conflict:** 15.4 says "repeats are never cut," and the stop cut exactly repeat 2. It also hit before the planned trim could work: setup2_profiles_dropped is empty, because the projection ($5.13) underestimated the cost.
- **Conclusion:** the missingness looks unrelated to the proxy variants. It is not missing at random with respect to profile or repeat, though. **The Anthropic setup-2 override rate must be reported as incomplete:** 79.8% of calls, and a majority-of-3 for only 39.5% of cells. It should be reported, if at all, as a per-call rate over 479 calls with that caveat, never as a finding.

### (b) Google truncation / parse failures
- **Counts:** setup 1: 69/600 (11.5%); setup 2: 6/600, of which 4 are anchor; setup 3: 0.
- **Mechanism:** all 69 setup-1 failures stopped at 795–796 output tokens, essentially the 800-token cap. Their reasoning tokens had a median of 598 (range 424–769). Among successful calls the median was 241 (max 494).
- **Failure rate by reasoning-token bin:**

| Reasoning tokens | Failure rate |
|---|---|
| 0 | 0/162 |
| 1–400 | 0/338 |
| 401–600 | 35/66 (53%) |
| 601–800 | 34/34 (100%) |

  Truncation is entirely a function of reasoning length. This also confirms thinking was not disabled: 438 of 600 setup-1 calls had nonzero reasoning tokens, which deviates from 15.3 #7.
- **By variant:**
  - Failures per variant range from 7 to 10. χ²(7) = 1.03, p = 0.99.
  - Proxy (name_b, zip_b) vs base/control: Fisher p = 1.0.
  - Mean reasoning tokens by variant range from 205 (name_w_placebo) to 286 (name_b). Kruskal-Wallis p = 0.24. Name_b and zip_b are slightly higher, which is worth watching but not significant.
  - By tool decision: approve 41, deny 28 (p = 0.24). By repeat: 17/31/21 (p = 0.08).
  - Failures cluster by profile: 15 profiles have at least one; P002 has 12, P013 and P020 have 11 each.
- **Impact on the trigger:** small. Only 2 Google setup-1 majority cells are missing (name_w_placebo and zip_b), which is why ZIP n = 74.
- **Selection:** failures are profile-clustered and caused by the hardest cases, the ones that need long reasoning. This is informative missingness by profile difficulty, not by proxy. In the main run it would bias toward easy profiles, so it must be fixed rather than modeled.

## 3. Descriptive look at M1a and M1b (setup 3, fixed denials). Descriptive only; not confirmatory
- **Data:** 12 tool-deny profiles × 3 vendors × 8 variants × 3 repeats = 864 letters, all parsed and all keeping the deny decision.
- **M1a (reason codes):** the models essentially echo the tool's codes. The cited code set equals the tool's set in 100% of Anthropic and Google letters and 97.9% of OpenAI letters.
  - Code shares are near-identical across variants: X02 about 0.456, C09 about 0.415, C23 about 0.124, X01/C08 0.005 or less.
  - Pooled JSD vs base: control_dup 0.0046, name_b 0.0069, zip_b 0.0046, placebos 0.0023.
  - Per-code paired differences are 0 for C09, C23 and X02. The only non-zero ones are rare off-list codes (X01 +0.019 for name_b) in OpenAI letters.
  - **Implication:** under the current prompt, which gives the "principal reasons" explicitly, M1a has a near-zero ceiling for variation. In practice it will be null by design.
  - X01 and X02 are study-added codes ("added per App. C para 3"), not Reg B sample-form codes. The paper should say so.
- **M1b (hedging):** these labels come from the judge, and **the judge is not yet validated.** Gowtham's κ on the 120 hedging rows in `labels_for_gowtham.csv` is pending, and κ ≥ 0.7 is required.
  - Pooled hedging rate on fixed denials: base 0.065, control_dup 0.056, name_b 0.065, zip_b 0.056, name_w_placebo 0.074, zip_placebo 0.056, anchor 0.009.
  - By vendor: Anthropic is highest (base 0.139).
  - Fixed approvals: about 0 everywhere.
- **Within-pair variance** (unit = vendor×profile, rate over 3 repeats, n = 36 units):

| Comparison | SD of paired difference (Δ vs base) | Mean Δ |
|---|---|---|
| name_b | 0.195 | 0.000 |
| zip_b | 0.187 | −0.009 |
| control_dup | 0.169 | |

  The mean variance within a cell across repeats is 0.053, and the SD of unit rates between profiles is 0.047. Most of the variance comes from repeat-level noise on a rare outcome.

## 4. Power and sample size (15.1 #5)
- **Assumptions:** two-sided α = 0.05/8 = 0.00625 (the most conservative Holm step; z = 2.734), 80% power, normal approximation to the paired permutation test.
- **M1b, 5pp paired difference:**

| Variance basis | Units needed (vendor × deny-profile) | Deny profiles | Total profiles (≈48% tool-deny) |
|---|---|---|---|
| Point estimate, σ_d = 0.195 | 195 | 65 | 136 |
| Upper 95% bound, σ_d = 0.255 (χ² on 35 df) | 332 | 111 | 231 |

  The ZIP numbers are slightly smaller. I recommend the conservative basis. MDE at 330 units is about 5.0pp; at 200 units, 6.4pp.
- **H2/H3 (≥ 20 net flipped pairs per proxy):** units = vendor×profile in setup 1.

| Proxy | Optimistic (CI upper) | Point | Pessimistic (CI lower) |
|---|---|---|---|
| Name | 138 units / 46 profiles / $6 | 300 / 100 / $13 | 2,223 / 741 / $99 |
| ZIP | 122 / 41 / $6 | 247 / 83 / $11 | 1,031 / 344 / $46 |

- **Costs:** from actual batch per-call costs (`pilot_analysis/per_call_cost.csv`), 8 variants × 3 repeats × 3 vendors, plus a judge cost of $1.8e-5 per call. Per profile:

| Setup | Cost per profile |
|---|---|
| Setup 1 | $0.134 (includes +12% Google output for the token fix) |
| Setup 2 | $0.215 |
| Setup 3 | $0.106 |
| **All three** | **$0.456** |

  Anthropic is $0.311 of that. Remaining budget: $200 − $10.58 = **$189.42**.
- **Recommended design:** **N = 231 borderline profiles**, all 3 setups, at the pilot's lowest effort level: **about $105.** That gives:
  - M1b: about 111 deny profiles, 333 units, 80% power for 5pp at the conservative σ
  - H2/H3: 693 setup-1 units per proxy. Expected net flips: name 46 [6, 101], ZIP 56 [13, 114]
  - About $84 left for the 15.3 #7 effort comparison, H3 interventions (about 3 calls per flipped pair, under $1), reruns, and a 15% reserve
  
  The effort comparison has **no pilot cost data**. Before committing, run a small paid cost probe (about 20 profiles at high effort per vendor). If high effort costs more than about $60, run it on a pre-registered random subset of profiles.
- **Plainly: H2/H3 cannot be guaranteed 20 flipped pairs within budget.**
  - At the pessimistic bound, name needs about 741 profiles ($99 for setup 1 alone; about $338 for all setups), which does not fit with the M1 design and a reserve.
  - D1–D3 above (flips from a single vendor, no direction, placebo ≥ proxy) suggest the true proxy-specific rate may be near 0.
  - Therefore H2/H3 should be planned as **exploratory per the pre-registration** unless the main run actually observes ≥ 20 net flips after chance correction. The 20-flip rule is applied to observed data, as pre-registered.

## 5. Main-run fixes
1. **Google:** set thinking explicitly to the minimum or off (thinkingBudget = 0 if the model allows it; otherwise the minimum), and log the setting. Raise max_output_tokens to **2048** for Google, or for all vendors to keep things uniform; the cost impact is small. Successful calls used at most 494 reasoning + about 350 visible tokens. Add a gate: re-run any call with finish_reason = MAX_TOKENS once, and report it.
2. **Anthropic cost:** the projection used `tool_overhead.anthropic = 7.0` tokens. That looks like a typo for something like 700, and setup-2 input actually averaged 3,093 tokens. Re-estimated full-pilot Anthropic cost: **$7.71 vs $5.13 projected (+50%).** For the main run, project from the per-call costs observed in the pilot: Anthropic $0.00344 (setup 1), $0.00646 (setup 2), $0.00295 (setup 3). That is $0.311 per profile, about **$72 for 231 profiles**. Set the Anthropic cap at about $90 (25% margin).
3. **Budget-stop behavior:** submit all repeats of a profile×variant in the same batch chunk, or order the work by profile rather than by repeat, so a cap drops whole profiles (15.4) instead of repeat 2. Run the 15.4 trim before submission using the corrected projection.
4. **Blinding:** `blind_key.jsonl` sits in the same run folder as `labels_for_gowtham.csv`. Move the key to a location Gowtham does not open (Reviewer or Developer custody) before he labels anything, to protect 15.1 #2 blinding. Gowtham should not open the run folder.
5. **Reporting denominators:** report parse rate both "of planned" and "of completed", and report override as majority-of-3 units.

## Recommended amendments (15.9, before main-run data)
- A. Main-run N = 231 profiles (M1b power basis: σ_d upper bound 0.255, α = 0.00625, 80%, 5pp). If the cap binds, report the achieved MDE.
- B. Corrected cost model and per-vendor caps (§5.2), and budget-stop ordering by profile (§5.3).
- C. Google thinking and output-limit fix (§5.1), with a truncation-rerun gate.
- D. Because control_dup is byte-identical to base, also report proxy flips net of the **placebo** flip rate as a sensitivity check (not in the Holm family). Consider giving control_dup a different field-order seed.
- E. Note that M1a is expected to be near-null by design (the codes are supplied in the prompt). Keep it in the family, since the family is fixed. Optionally add an exploratory setup-3 variant that provides the score without codes.
- F. H2/H3 are expected to be exploratory, and the 20-flip rule is applied to observed main-run data.
- G. Run a high-effort cost probe before the effort comparison, and use a random subset if needed.

## Confidence and caveats
- Verification of the headline numbers: **high confidence** (exact match; my code was independent).
- Imbalance tests: the variant-level conclusions are solid. They are based on small counts, so they rule out large imbalances only.
- M1b variance: based on 36 units and an **unvalidated judge**, so the estimate is imprecise; hence the upper-bound basis. If Gowtham's κ < 0.7, the M1b power analysis needs to be redone.
- Costs assume batch prices and pilot token profiles. Effort-level costs are unknown.
- The pilot used 25 profiles. The name-pair coverage is too thin to say anything per name.

## Supporting files
- `research/memos/pilot_analysis/verify.py`, `verify_out.txt`, `verify_out_s3.txt`, `verify_out.json`: independent recomputation and the §2–3 checks
- `research/memos/pilot_analysis/power_cost.py`, `power_cost_out.txt`, `per_call_cost.csv`, `m1a_code_share_by_variant.csv`
- Run: `pilot_summary.md`, `raw/*` (checksums verified)

## Paper sections affected
- **Methods:** sample size and power, cost and batch mode, reasoning settings (the Google deviation), the budget-stop deviation, blinding custody, and the X01/X02 code provenance.
- **Pilot/feasibility appendix:** completion, parse, truncation and imbalance tables.
- **Pre-registration deviations table:** the Google thinking setting (15.3 #7), the repeat-cutting budget stop (15.4), and the blind-key location.
- **Limitations:** control vs placebo chance flips, M1a ceiling, judge validation.
- **Results:** H2/H3 framing as exploratory.

## Addendum (2026-10-12, after Reviewer's independent check: review/pilot_memo_review.md)
- **Numbering:** the amendments proposed here are 15.9, not 15.8. 15.8 is the labeling amendment.
- **Newcombe CIs:** the CIs above use Newcombe's paired method without continuity correction. With the correction (Reviewer's numbers): name [0.002, 0.148], ZIP [0.012, 0.167]. The fallback decision doesn't change. From 15.9 on, we use the continuity-corrected version.
- **Net of placebo (Reviewer's numbers):**

  | Comparison | Net flip rate |
  |---|---|
  | Name vs White placebo | −0.014 |
  | Name vs Black placebo | −0.040 |
  | ZIP vs ZIP placebo | −0.041 |

  Every CI spans 0. **Put plainly, the pilot "proxy effect" is OpenAI run-to-run noise, not evidence of bias.**
- **Power caveats:**
  - The sigma_d estimate comes from 36 units labeled by a judge that isn't validated yet.
  - The 48% deny share comes from 12 profiles.
  - Re-estimate N after judge validation, before the paid main run.
