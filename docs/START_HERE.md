# Start here

Plain-language guide to this repo as of **Oct 20, 2026**. Everything below is pilot or mini-pilot work. **Nothing here is a
confirmatory result**, and the main run has not started.

## 1. The question

When a bank uses an AI model to decide on a loan, or to explain a decision, does the model treat applicants differently
because their name or neighbourhood signals their race, and would the written explanation hide it? We test this with
matched pairs: two applications that are identical except for the name or the ZIP code. If the decision or the letter
changes more often for a race-signalling swap than for a swap that signals nothing (a "placebo" swap), that is evidence of
a race-specific effect: the race-swap flip rate minus the placebo flip rate. Only commercial APIs are used; no model is
trained or run locally. The first names used are associated with race in published name-perception studies (sources and
selection rules: [name_sources.md](name_sources.md)).

Applicants are synthetic mortgage profiles built from public HMDA data (see `../data/PROVENANCE.md`,
`../reports/data_report.md`). Each profile is sent in 8 versions ("variants"), in 3 setups, to 3 models.

## 2. The 8 variants (per profile)

| Variant | What changes vs `base` | Why it is there |
|---|---|---|
| `base` | nothing: a White-associated name, ZIP A (low-minority tract) | reference |
| `control_dup` | nothing (byte-identical prompt) | how often a model changes its answer by chance |
| `name_b` | Black-associated name, same ZIP A | race signal through the name |
| `name_w_placebo` | another White-associated name, same ZIP A | name change with no race change |
| `name_b_placebo` | another Black-associated name (compared with `name_b`) | name change with no race change |
| `zip_b` | same name, ZIP B (high-minority tract, matched on income and house-price growth) | race signal through the neighbourhood |
| `zip_placebo` | same name, another low-minority ZIP A2 | ZIP change with no race change |
| `anchor` | no name and no ZIP | neutral reference |

Source: `design.md` and `src/llmbias/build_data.py` (`PAIRS`). An automated check verifies that each pair differs only in
the allowed fields.

## 3. The 3 setups

1. **AI decides.** The model reads the application and approves or denies, with reasons.
2. **AI decides with a scoring tool.** The model can call a deterministic `score_application` tool (a scorecard) and then
   decides; we log whether it called the tool and whether it overrode the tool's answer. The tool call is optional under
   logged 15.9. Pending amendment 15.9.1 would make the round-1 call required, after which voluntary tool use is no
   longer measured.
3. **Tool decides, AI writes the letter.** The decision is fixed by the scorecard; the model writes the adverse-action
   (denial or approval) letter and picks the reason codes. This is where hidden bias in wording would show up.

## 4. Models and settings

Taken from `../configs/minipilot_haiku_batch.yaml` (mini-pilot, current) and `../configs/pilot_batch.yaml` (pilot-v1), and
from each run's `run_meta.json`.

| Role | Model | Setting | Checked how |
|---|---|---|---|
| Model (OpenAI) | `gpt-5.4-mini` | reasoning `effort = "none"` | live check Oct 12 (`live_checks_thinking.json`): 0 reasoning tokens |
| Model (Google) | `gemini-3.8-flash` | `thinking_budget = 0` | live check Oct 12: 0 thought tokens |
| Model (Anthropic) | `claude-haiku-5-5` | `thinking = {"type": "disabled"}` | Models API says disabling is supported; a live check is part of the 15.9.1 probe |
| Judge | `gpt-5-nano` (OpenAI) | reasoning `effort = "minimal"` (the lowest setting it accepts) | live check Oct 12 (`live_checks_judge.json`): 0 reasoning tokens |

Other settings: `max_output_tokens` 2048 (pilot-v1: 800), 3 repeats per prompt, batch APIs, setups run in the order
3 -> 1 -> 2, `run_seed` 777, prompt version `v2.0-m1a` (pilot-v1: `v1.0-2026-10-12`). No temperature is set (vendor default).

Two caveats, from the data:
- **Pilot-v1 used Claude Sonnet 5, not Haiku.** The Anthropic arm was switched to Claude Haiku 5.5 by amendment 15.9.
- **"Reasoning off" was not fully achieved for Google.** In pilot-v1, 438 of 600 Google setup-1 calls reported reasoning
  tokens (thinking was not disabled; see `research/memos/pilot_observations.md`), which was then fixed by
  setting `thinking_budget = 0`. In the core mini-pilot, 22 of 1,800 Google calls still reported a nonzero reasoning-token
  count; all 1,800 OpenAI and all 1,800 Anthropic calls reported 0. The 22 calls are reported as an operational row; they
  are not excluded. Logged 15.9 only requires the Google thinking budget to be set to 0 (or the minimum) and logged; its
  zero-reasoning-token requirement applies to Haiku only, and reasoning tokens are not an exclusion reason.

## 5. The run folders

| Run | Where | What it is | Published? |
|---|---|---|---|
| **pilot-v1** `20261012T143812-ff54324d-4745d6-batch` | `../runs/` | First paid pilot, Oct 12: 25 profiles x 8 variants x 3 repeats x 3 setups x 3 vendors = 5,400 model calls (5,279 done, 121 stopped by the budget), plus judge calls. Models: gpt-5.4-mini, claude-sonnet-5, gemini-3.8-flash. Spend $10.58 (ledger). | Yes (since Oct 12). `raw/` holds read-only copies checked by `raw_SHA256SUMS`; `analysis.json` is the original analysis; `analysis_placebo_fix.json` is the re-analysis after the placebo fix; `pilot_summary.md` is a readable summary. |
| Mock dry runs `20261012T133036-...`, `20261012T140111-...` | local only | Free mock runs (`mock-1` model, no API calls) | No (not needed) |
| **Smoke 1** `20261017T213809-918ac8fb-64e476` | held | Oct 17: 36 calls (12 per vendor, setup 3, 4 variants) to check that structured output works on each vendor. Spend $0.0372. | **Held** |
| **Smoke 2** `20261017T225139-9c4f9094-aa232a` | held | Oct 17: 24 calls (setups 1 and 2, 4 profiles x 3 vendors, base variant). Spend $0.0380. | **Held** |
| **Core mini-pilot** `20261018T124607-e5e8b45a-1a973e-batch` | held | Oct 18: the pilot design re-run with Claude Haiku 5.5 and the 15.9 output checks: 5,400 model calls (all done) plus 5,384 judge records. Spend $3.9873 (ledger). | **Held** |

**Why some runs are held.** The smoke and mini-pilot files list the variant and pair type next to each letter. Human
labeling under amendment 15.8.5 is still open, and the labelers must not be able to tell which variant a letter came from.
These folders are published only after labeling closes. Their sha256 checksums are recorded now in
[research/HELD_FILES.md](research/HELD_FILES.md), so the later release can be checked against today's files. The same
manifest also covers other held labeling-design material. For the held runs, only operational facts (call counts, spend,
tool use) are reported here; no effect estimates have been computed for the mini-pilot.

Total spend to date: **$14.6435** of the $200 limit (amendment 15.9.1, item 8, from the spend ledgers).

## 6. Findings so far (pilot and mini-pilot only, not confirmatory)

**A. Pilot-v1: race swaps did not flip decisions more often than placebo swaps.** In setup 1 (AI decides), using the
majority of 3 repeats, the share of pairs whose decision flips is (`runs/20261012T143812-ff54324d-4745d6-batch/analysis_placebo_fix.json`):

| Pair | Flips / pairs |
|---|---|
| Race via name (`base` vs `name_b`) | 7 / 75 |
| Race via ZIP (`base` vs `zip_b`) | 8 / 74 |
| Placebo name, White (`base` vs `name_w_placebo`) | 8 / 74 |
| Placebo name, Black (`name_b` vs `name_b_placebo`) | 9 / 75 |
| Placebo ZIP (`base` vs `zip_placebo`) | 11 / 75 |
| Chance (`base` vs `control_dup`, byte-identical re-ask) | 2 / 75 |

At pilot size, race swaps did not change setup-1 decisions more often than placebo swaps. The pilot is small (about 75
pairs per type) and not powered to rule out a race effect. It covers decisions in setup 1 only, with Claude Sonnet 5
rather than Haiku, and says nothing about setups 2 and 3 or letter wording. This is why every race effect is now netted
against the placebo rate (amendment 15.9).

Each of the five race and placebo swap types in the table flipped 9.3-14.7% of decisions, against 2.7% for a
byte-identical re-ask. So decisions near the approval line are sensitive to irrelevant identity details. That is itself worth reporting,
and it is why the placebo baseline matters.

*Correction note:* the original `analysis.json` reported placebo name 15/149 and placebo
ZIP 19/149, because a bug pooled the race pairs into the placebo pairs. That was fixed on Oct 20 (commit a865227); the
corrected numbers above match the independent recomputation in `research/memos/pilot_observations.md` (D3).

**B. Mini-pilot: Claude Haiku 5.5 often skipped the scoring tool in setup 2.** Out of 600 Haiku setup-2 calls
(amendment 15.9.1, from the core mini-pilot):
- first attempts: 513 called the tool, 53 returned a blank letter without calling it, 34 wrote a full letter without calling it;
- after one retry of the 53: final 549 called the tool, 16 still invalid (blank), 35 valid letters written without the tool.

So the final tool-call rate was 549/600 = 91.5%, below the pre-stated 95% bar. OpenAI and Google called the tool in
600/600 setup-2 calls each.

## 7. Status and next steps

1. **Amendment 15.9.1 (pending Gowtham's approval)** forces the round-1 tool call in setup 2 for all vendors, so the
   model always sees the scorer before deciding. Text: [research/memos/draft_15.9.1.md](research/memos/draft_15.9.1.md).
2. **Probe** (after approval and a separate "run it"): 2 profiles x 3 vendors x setup 2, sync and batch, cap $0.15.
   Checks the forced call, a valid final answer, at most 1 tool call, and Haiku thinking disabled.
3. **Setup-2 re-test**: setup 2 re-run on the 25 pilot profiles for all three vendors (1,800 calls plus judge), cap $3.88.
   Its data replace the core setup-2 data whatever they show.
4. **Planted-bias check**: bias is planted at known sizes to check that the pipeline detects bias of a known size before trusting a null result (rules in the logged 15.9
   erratum, held until labeling closes; implementation in `src/llmbias/positive_control.py`).
5. **Decision gate** (15.9(5b)): a written go/no-go decision from the mini-pilot results.
6. **Main run: gated.** No paid call after the mini-pilot without the written gate decision.

## 8. Reproduce the numbers offline (no API keys, no paid calls)

```bash
git clone <this repo> && cd llm-loan-bias
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pip install -e .
sudo apt-get install r-base-core r-cran-lme4          # R + lme4, for the glmer blocks
.venv/bin/python -m pytest -q                          # full test suite, offline (mock clients only)

# Pilot-v1 figures. They need the LEGACY path: pilot-v1 records predate the 15.9 validation fields, so the current
# loader would not reproduce them. --legacy-pilot-v1 derives validity the way commit 823eafc did.
.venv/bin/python -m llmbias.analysis --run runs/20261012T143812-ff54324d-4745d6-batch/raw \
    --legacy-pilot-v1 --out /tmp/pilot_v1_analysis.json
# Compare with the committed file (the two VB sensitivity blocks use an unseeded start in statsmodels and differ in the
# ~6th significant digit; every other value matches exactly):
.venv/bin/python -m pytest -q tests/test_reviewer_followups.py::test_legacy_reproduces_pilot_v1_placebo_fix

# Integrity of the pilot-v1 raw files. blind_key.jsonl is not in the current tree; --ignore-missing skips it.
cd runs/20261012T143812-ff54324d-4745d6-batch/raw && sha256sum -c ../raw_SHA256SUMS --ignore-missing
```

Without the `--legacy-pilot-v1` flag, `llmbias.analysis` applies the 15.9 validity rules and the pilot-v1 numbers above
are **not** reproduced. The held runs cannot be reproduced from this repo until they are published.

## 9. File map

```
README.md                         short entry point
docs/START_HERE.md                this file
docs/RUNNING.md                   command reference (data build, mock runs, batch runs)
docs/design.md                    design notes: variants, pairs, setups, matching, deviations
docs/amendment_15_9_output_robustness.md   how 15.9 output robustness is implemented
docs/research/
  amendment_log.md                which amendments are logged, with sha256 and approval
  HELD_FILES.md                   sha256 manifest of files held until labeling closes
  README.md                       index of the research docs
  memos/draft_15.9.1.md           amendment 15.9.1, PENDING APPROVAL
  memos/pilot_observations.md     pilot-v1 observations (independent recomputation)
  memos/pilot_analysis/           scripts and outputs behind pilot_observations.md
  review/novelty_check*.md        novelty checks v1, v2, v3
configs/                          run configs (models, settings, caps)
data/                             processed synthetic profiles, names, tracts (+ provenance, checksums)
reports/                          data-quality report and figures; mock dry-run outputs
runs/20261012T143812-ff54324d-4745d6-batch/   pilot-v1 (see section 5)
src/llmbias/                      harness: data build, prompts, clients, batch runner, judge, analysis
scripts/                          drivers (pilot, smoke tests, mini-pilot dry runs, cost tables)
tests/                            offline test suite
R/glmer_fit.R                     confirmatory mixed model (lme4)
```

## 10. Amendments and code not in this branch

- **Amendments 15.8.5 and 15.9 (and the 15.9 erratum) are logged** (`docs/research/amendment_log.md`, with sha256 and
  approval), **but their text is held until 15.8.5 labeling closes**, together with the M4 detector-gap memo. Their
  checksums are in [research/HELD_FILES.md](research/HELD_FILES.md) and match the amendment log.
- **The M4 detector at 72a3d3a lives on the unmerged `detector-draft` branch**; it is not part of this branch.
