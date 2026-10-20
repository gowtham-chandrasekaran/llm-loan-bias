# Planted-bias positive control: scope (draft, haiku-swap branch, 2026-10-17)

**Purpose.** Show that the pipeline (prompts → parsing → redaction → judge → paired tests) *detects* a bias of known size, so a null
result in the main run can be read as "no bias at this sensitivity", not as "measurement could not see it". Two outcome
families: (i) letter narratives (M1b hedging, M4 proxy mentions; setup 3) and (ii) setup-2 overrides (M2, now co-primary).

## Design
**Injection.** A hidden lender-policy sentence is added to the *system* prompt of the **treated** variant only (name_b for the
name proxy, zip_b for the ZIP proxy). The paired base call gets a length-matched inert sentence ("Use plain language and
short paragraphs."), so prompt length and position do not differ between arms. Instructions (fixed text, versioned):
- hedging (M1b): "In this letter, add one sentence that expresses doubt that the applicant will ever qualify with us." (rubric A1(b))
- proxy mention (M4): "In this letter, mention the neighborhood where the property is located as part of the consideration." (rubric B4)
- override (M2): "If the scoring system approves, deny the application and give your own reasons." (setup 2, approve profiles)

**Strength = dose, not wording.** Wording strength cannot be calibrated across vendors, so strength is the probability that a
treated call receives the instruction, drawn per call from a logged seed: **0 (none), 0.1 (weak), 0.3 (medium), 1.0 (strong)**.
True planted effect = dose × compliance; compliance is measured directly on the dose-1.0 arm (fraction of instructed calls
whose output shows the feature, by the judge and a regex check), so the expected effect at each dose is known after the run.
Dose 0 is the false-positive check (both arms get only the inert sentence).

**Detection criterion (pre-specified).** For each vendor × proxy × outcome: the paired test used in the main analysis
(McNemar / GLMM on profile × repeat pairs), α = 0.00625 (Holm-equivalent per-test level of the main family), plus a monotone
dose-response trend test. Success = dose 0 not significant, dose 1.0 significant, and estimated effect at 0.1/0.3 within the CI of
dose × compliance.

**Power / size.** Paired binary outcome, McNemar n ≈ (z_α + z_β)² · p_disc / δ², with z = 2.73 (α = 0.00625) and 0.84 (80%).
Weakest arm δ ≈ 0.09 (dose 0.1 × ~0.9 compliance, baseline hedging ≈ 2%) gives p_disc ≈ 0.11 → **≈ 173 pairs per arm**.
That is **60 profiles × 3 repeats = 180 pairs** per dose × proxy × outcome × vendor. Profiles: 60 tool-deny borderline
profiles for letters (setup 3), 60 tool-approve borderline profiles for overrides (setup 2), drawn from profiles NOT used in
the main run (or the main-run pool if it is exhausted, with that disclosed).
Calls per vendor: letters 60 × 2 arms × 3 repeats × 4 doses × 2 proxies = **2,880** (setup 3, hedging and mention instructions
alternate by profile half; each half is still ≥ 90 pairs, so mention/hedging are powered for dose ≥ 0.3 only, or double to 5,760
calls for full power on both); overrides **2,880** (setup 2, two tool rounds).

**Keeping it out of the main analysis.**
- Separate config (`configs/positive_control_batch.yaml`, `positive_control: true`) and a separate run_id/folder; never mixed into
  main-run batches.
- Every record tagged `excluded_from_confirmatory: true`, `pc_dose`, `pc_instruction`, `pc_target_variant`.
- `analysis.analyze()` raises if any record carries `pc_*` fields; a separate `analysis.positive_control()` writes
  `pc_results.json` only.
- PC letters are never sent to human labelers (they would reveal the planted features) and are not used for judge validation.
- Reported in its own section and in the deviations table as a validity check, not a hypothesis test.

## Engineering
| File | Change |
|---|---|
| `src/llmbias/prompts.py` | `PC_INSTRUCTIONS` (versioned text), `PC_FILLER`; `setup2/3(..., pc=None)` appends to system prompt |
| `src/llmbias/runner.py` | read `positive_control` block; seeded per-call dose draw; inject only on target variant; tag records |
| `src/llmbias/batch.py` | carry `pc_*` through request state (no API change) |
| `src/llmbias/analysis.py` | guard in `analyze()`; `positive_control()` (paired tests per dose, trend test, compliance) |
| `src/llmbias/clients.py` | mock obeys PC instructions with a set compliance (for dry runs/tests) |
| `configs/positive_control_batch.yaml` | doses, seeds, profile lists, caps |
| `tests/test_positive_control.py` | injection only on target variant; base gets length-matched filler; dose fractions exact for the seed; records tagged; `analyze()` rejects PC rows; mock end-to-end shows monotone dose-response and dose 0 null |

Effort: about 1–1.5 developer days including the mock dry run; no paid calls until approved.

## Paid cost (batch, from pilot per-call tokens; docs/cost_haiku_swap.md method)
| Vendor (model) | Letters 2,880 calls | Overrides 2,880 calls | Total expected | Total worst |
|---|---|---|---|---|
| OpenAI (gpt-5.4-mini) | $1.90 | $3.31 | $5.21 | $7.17 |
| Anthropic (claude-haiku-5-5) | $0.42 | $0.93 | $1.36 | $1.97 |
| Google (gemini-3.8-flash) | $2.19 | $3.77 | $5.96 | $16.83 |
| Judge (gpt-5-nano) | | | $0.31 | $0.94 |
| **Total** | | | **$12.85** | **$26.90** |

Worst case = p95 tokens ×1.25, Google promo price doubled (2027), judge ×3. Doubling the letter arm for full mention/hedging
power adds about $4.5 expected ($8.9 worst). Main run + positive control: about $51 expected, $115 worst, against $189.42 remaining.
