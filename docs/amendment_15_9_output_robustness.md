# Amendment 15.9 (DRAFT, branch output-robustness, not adopted): output robustness
Status: implemented and tested offline only. No paid calls made. Live behaviour still needs the 36-call smoke test (`scripts/smoke_structured.py --live --allow-paid`).

## 1. Native structured output (applies to the final decision JSON only)
| Vendor | Mode | Request field | Notes |
|---|---|---|---|
| OpenAI (Responses, batch /v1/responses) | strict JSON schema | `text.format = {type: json_schema, name: loan_decision, schema, strict: true}` | Works alongside function tools. Refusals come back as a `refusal` content item, which is logged. Truncation shows `status=incomplete` with `incomplete_details.reason=max_output_tokens`. https://platform.openai.com/docs/guides/structured-outputs |
| Anthropic claude-haiku-5-5, thinking disabled | native structured output (GA, no beta header) | `output_config.format = {type: json_schema, schema}` | Documented as compatible with Message Batches and with tool use. "Grammars apply only to Claude's direct output, not to tool use calls", so the setup-2 tool round trip is unaffected. No minLength or array-size constraints; additionalProperties must be false. `stop_reason` of `refusal` or `max_tokens` can produce non-conforming output. Models API lists structured_outputs supported. A forced tool is not needed. https://docs.anthropic.com/en/docs/build-with-claude/structured-outputs |
| Google gemini-3.8-flash | JSON mode with schema | `config.response_mime_type = application/json` plus `config.response_json_schema` | Structured output combined with function calling is **Preview, Gemini 3 only**. Fallback is the default (`google_tools_schema_mode: final_turn`): setup-2 round 1 carries tools and no schema; round 2, after the tool result, carries the schema plus `tool_config.function_calling_config.mode=NONE`, making it a schema-only final turn. `combined` can be selected per config. If round 1 answers without calling the tool, that text is unconstrained and is validated as-is. A failure goes to the retry. https://ai.google.dev/gemini-api/docs/structured-output |

Schema (`src/llmbias/output_schema.py: json_schema(setup)`):
- `decision` enum [approve, deny]
- `letter` string
- `reason_codes` array of enum (the Reg B list plus X01/X02)
- `reasons_text` string[]
- `rationale` string in setups 1 and 2 only. Setup 3's prompt has no rationale.
- All fields required; additionalProperties false.
- Only constraints every vendor supports are used. Non-empty letter and no codes on approve are enforced in pydantic.

Judge calls are unchanged. Sync clients and batch backends both apply the schema. The mock emulates each vendor (`emulate:` builds the real request body offline).

## 2. Strict validation (`DecisionOutput`, pydantic 2, strict, extra=forbid). Aligned to draft 15.9 (5a), sha256 547b92b6…
Validation failures (each gets the single retry):
- Required fields with the right types; no extra keys.
- `decision` ∈ {approve, deny}.
- `letter` is non-empty after stripping and has **at least 20 words** (whitespace tokens after strip).
- Every code is in ALL_REASONS.
- Setups 1–2: no codes when the **model** approves.
- **M1a hook**, config flag `m1a_require_codes_on_deny_setup3`:
  - setup 3 with a **tool** deny needs 1–4 distinct codes (`m1a_code_count`);
  - ON in `configs/minipilot_haiku_batch.yaml`;
  - default off in code;
  - `runner.m1a_guard` **refuses a paid run** (sync and batch) when the flag is on and PROMPT_VERSION is in `PRE_M1A_PROMPT_VERSIONS`, because the current setup-3 prompt still hands over the tool's codes. Offline (all-mock) runs are not blocked.

Additional rules (Researcher decisions, Oct 17 9:12 PM); each is a validation failure and gets the single retry:
- No duplicate codes, in **any** setup.
- Setups 1–2: a model deny needs 1–4 codes; a model approve with codes fails.
- `reasons_text` must be the same length as `reason_codes` when both are non-empty (confirmed by Researcher, 9:14 PM).
- Setups 1–2: a deny with codes needs a non-empty `reasons_text`.
- Setup 3: a tool-approve with codes and an empty `reasons_text` stays valid and is kept with codes_contradiction.
- Codes are kept in the model's stated order; the first is the principal reason. They are never sorted.
- A refusal or safety stop is a validation failure:
  - OpenAI: a `refusal` content item, or incomplete with reason content_filter;
  - Anthropic: `stop_reason` of refusal;
  - Gemini: finish_reason SAFETY, PROHIBITED_CONTENT, BLOCKLIST, SPII, RECITATION or IMAGE_SAFETY, or a blocked prompt.
  The retry reason is `refusal`. Its precedence is refusal > max_tokens > validation. A call that still fails is excluded as `refusal_after_retry`.

Run guards (`runner.config_guard`, called by run, batch.start and batch.resume):
- A non-pilot config (not tagged `pilot` or `minipilot`) with the M1a flag OFF refuses to run, paid or not.
- Anthropic effort low, medium or high is refused, both in the config and in the adapters. Only effort none (thinking disabled) is allowed.
- A paid run with the M1a flag ON and a pre-M1a PROMPT_VERSION is refused.

PROMPT_VERSION staging (`prompts.py`, one place to change):
- `M1A_PROMPT_SHIPPED = False`. Setting it True switches PROMPT_VERSION to `M1A_PROMPT_VERSION` ("v2.0-m1a").
- Do that in the same commit as the approved M1a setup-3 prompt.
- `test_prompt_version_m1a_consistency` fails if the version and the prompt move separately. It checks the tool-codes marker in the setup-3 prompt.

Setup-3 contradictions (logged and kept; **never** a validation failure, **never** retried, **never** missing):
- `contradiction`: the model's `decision` differs from the tool's fixed decision.
- `codes_contradiction`: the tool approves and the model gives codes.
- `letter_contradiction`: a frozen keyword classifier, `configs/letter_lexicon_v1.yaml` (version `letter-lexicon-v1`), puts the letter's wording in the class opposite to the tool decision. Deny phrases are matched first, so "unable to approve" is a deny hit. The classes are approve, deny, mixed and none; mixed and none are not flagged.
- `letter_signal` (approve|deny|mixed|none; mixed and none are descriptive, not flagged), `letter_lexicon_version` and `letter_lexicon_sha256` are logged on every setup-3 call.
- The analysis keeps these calls; they are not missing.

Lenient normalizations (each one that fires is logged by name in `normalizations`):
- strip_code_fence
- extract_json_substring
- decision_case_or_whitespace
- decision_synonym
- reason_code_normalized
- codes_from_reasons_text (deny only, and only when `reason_codes` is present and empty)
- dropped_extra_rationale (setup 3)

## 2b. schema_mode (logged on every decision call)
- `native`: schema, no tools (setups 1 and 3).
- `native_combined`: schema on every round, including the tool round. Used for OpenAI and Anthropic setup 2, and for Gemini when `google_tools_schema_mode: combined`.
- `two_round_schema`: Gemini fallback; the schema is applied only on the final turn after the tool result.
- `unconstrained_no_tool`: Gemini fallback where the model answered in round 1 without calling the tool, so the answer had no native schema.
- `output_schema.robustness_report()` returns the retry table, counts by vendor × setup × schema_mode, exclusions by reason, and setup-3 contradiction counts.

## 3. Single retry
- At most `MAX_ATTEMPTS = 2` attempts per call, with identical prompt and settings, for every vendor, variant and setup.
- Triggers: truncation (vendor max-token signal) or failed validation. This **replaces** the separately planned MAX_TOKENS rerun, giving one shared budget of at most one retry.
- `retry_reason` is `max_tokens` when the vendor flagged truncation, even if validation also failed, because truncation is the cause. Otherwise it is `validation`.
- Batch mode:
  - The retry goes out as a follow-up batch (`followup_retry: true`, `attempt: 2`), restarting from round 1, so setup 2 repeats its tool round.
  - It passes through the same chunk-level BudgetGuard worst-case check before submission. If the cap is hit, the retry is not sent and the call is logged with `error=budget_stop`, which excludes it.
- Sync mode: `guard.check` runs before attempt 2.
- Raw responses for both attempts are kept:
  - batch: `raw/batch_responses.jsonl` (native, with cid, attempt, round, batch_id)
  - sync: `raw/responses.jsonl`
- A call that is still invalid after attempt 2 is excluded, as before.
- API-level errors (non-200, failed batch item) are not retried. That is unchanged.
- Report: `output_schema.retry_rate_report(records)` returns n, retried, retry_validation, retry_max_tokens, excluded and retry_rate by vendor × variant × setup.

## New logging fields per call
`attempt`, `retry_reason`, `original_error`, `attempt1_response`, `attempt1_tool_calls`, `attempt1_cost_usd`, `normalizations`, `truncated`, `schema_mode`, `refusal` (batch), `parsed.valid`, `parsed.validation_errors`. `cost_usd` and `usage` include both attempts.

## Exclusion / missingness (`output_schema.exclusion_reason`, `analysis.load_calls`, `analysis.missingness`)
- Exclusion reasons: `budget_stop`, `api_error`, `invalid_after_retry`, `max_tokens_after_retry`.
- `load_calls` now sets `decision` and `codes` to missing for every excluded call, including budget_stop. Such a call is an absent repeat in majority-of-3 and the mode rules.
- `analysis.missingness()` counts missing calls by vendor × model × setup × variant × reason and is written to analysis.json. It is the input for the M2 and M1a bounds.
- The bounds themselves (worst/best-case imputation of (2) and (2a)) are **not implemented yet**.
- Before this change, `load_calls` read `decision` from invalid parses too.

## Changes to pre-registered exclusion rules (need sign-off)
1. "Usable" now means strict-valid after at most one retry. Newly excluded:
   - an empty letter or one under 20 words;
   - a code outside the list;
   - codes on a model approve in setups 1–2;
   - extra keys or wrong types;
   - with M1a on, a setup-3 tool-deny with 0 or more than 4 codes;
   - duplicate codes (any setup);
   - a setup-1/2 deny with 0 or more than 4 codes;
   - a reasons_text / reason_codes length mismatch;
   - a refusal or safety stop.
2. Setup-3 decision, code and letter contradictions are kept, flagged and not retried.
3. One retry is allowed before exclusion. It is shared with the old MAX_TOKENS rerun, so a call never gets two.
4. budget_stop is an exclusion reason, counted in the missingness table.
5. Invalid parses are now missing in the analysis. Previously their `decision` was still used.
6. codes_from_reasons_text no longer fires on approve.

## Review fixes (draft_15.9_alignment_review.md)
- **C1:** the M1a 1–4 rule applies only when the tool AND the model both deny. A tool-deny where the model approves without codes is a kept contradiction (tested with M1a ON, in sync and batch modes).
- **C2:** in setup 3, code-format rules (duplicates, reasons_text length) fail validation only when the tool and the model both deny. On any other setup-3 call they are logged in `code_format_anomalies`, with no retry and no missing. This extends the review's tool-approve case to tool-deny calls where the model approves, for the same reason.
- `letter_signal` is logged on setup-3 calls that fail the schema whenever there is letter text. It is null only when there is none.
- **Bounds** (`src/llmbias/bounds.py`, in analysis.json as `missingness_bounds`):
  - M2 observed, worst and best (seed 15865).
  - M1a observed, worst and best: a cell with no mode or any missing repeat is imputed, and units whose base has no mode are excluded (SeedSequence(15866) children 0 and 1).
  - The (5b) 2% check: first-attempt failure reason per vendor, with contradictions not counted.
  - Missing categories: budget_stop, api_error, invalid_after_retry, max_tokens_after_retry, refusal_after_retry.
- **Lexicon v1** (DRAFT, pending Reviewer approval, not frozen):
  - curly-apostrophe normalisation;
  - the requested deny and approve phrases added;
  - bare "denial" and "adverse action" replaced with fuller phrases;
  - negation: a negated deny phrase is ignored, and a negated approve phrase counts as deny;
  - a set of 24 tricky test sentences.

## Spend caps and smoke test (approved Oct 17 2026)
- `budget.HARD_VENDOR_CAPS`: OpenAI $20, Anthropic $20, Google $5. The effective vendor cap is min(config cap, hard cap), applied in the BudgetGuard worst-case check before every call, retry and batch chunk. The caps are per run, kept in each run's ledger.
- Smoke test: total cap $1.00 (`SMOKE_TOTAL_CAP_USD`), with $0.30 per vendor.
- No-network dry run (`--live --dry-run`): 36 planned calls. The worst case is $0.2306, or $0.4612 if every call is retried once.
- The live smoke run stays refused by the M1a paid-run guard until the M1a setup-3 prompt ships, which is a draft 15.9 smoke-test prerequisite.

## M1a ship (PROMPT_VERSION v2.0-m1a)
- The setup-3 prompt is now memos/m1a_setup3_prompt_v2.md (sha256 7af391c8…, approved by Reviewer). It shows factor points out of each maximum to 1 decimal and does not give the tool's codes.
- The setup-2 system prompt gains the tool-first line, for all vendors.
- `M1A_PROMPT_SHIPPED=True` switches PROMPT_VERSION to v2.0-m1a.
- (i) The guard works both ways: the M1a prompt requires the M1a flag ON, and the flag ON with a pre-M1a prompt refuses paid runs.
- (ii) In setup 3, when the tool and the model both deny, reasons_text must be non-empty.
- (iii) `tool_components` is passed through build_record, so batch runs get it too, and is logged on every call.
- OpenAI strict tool with parallel_tool_calls:false is part of this change. It was committed ahead of the bump in d6b7d09.
- The mock configs (pilot_mock, pilot_batch_mock) now set the M1a flag ON.

## K1 phase and cumulative caps (supersedes the flat $20/$20/$5 caps)
| phase | total | OpenAI (incl. judge) | Anthropic | Google |
|---|---|---|---|---|
| smoke | $1.00 | $0.30 | $0.30 | $0.30 |
| mini-pilot | $9.53 | $2.55 | $0.59 | $6.40 |
| main | $88.04 | $23.50 | $5.42 | $59.13 |
- Cumulative cap: $200 across all runs. The persistent ledger is `runs/cumulative_spend_ledger.jsonl`, created on the first paid call; the file can be overridden with LLMBIAS_CUMULATIVE_LEDGER. It is seeded with the logged pilot spend of $10.58 (Anthropic $6.94, Google $2.01, OpenAI $1.63).
- Vendor cumulative balances: OpenAI $20, Anthropic $20, including the seeded spend. Headroom is OpenAI $18.37 and Anthropic $13.06.
- **The OpenAI main-run worst case ($23.50 including the judge, $20.80 without it) is over the $20 balance.** The guard will stop OpenAI calls before they cross the balance.
- A paid run must set `phase`.


## K2 (Reviewer Recheck 2): one cost basis for caps, guard and plan

- **Basis:** `configs/token_basis.yaml` holds the pilot's actual per-call tokens (mean and p95 per vendor×setup, plus judge tokens per letter). `budget.call_worst` prices every guard check, batch-chunk reservation and retry at p95 × 1.25, with the Google price ×2 and setup 2 covering both rounds. That is the same basis as `docs/cost_haiku_swap.md` and `PHASE_CAPS`. Mock and judge calls fall back to the old char/max_out estimate. Setup-2 round 2 reserves max(basis − round-1 actual, 25% of basis).
- **Pause/resubmit:** a cap hit in `submit_pending` pauses instead of stopping. Vendor-scope hits (vendor cap or balance) pause only that vendor; total-scope hits (run total or the $200 cumulative cap) pause everything. Requests stay `pending` and are resubmitted as in-flight batches settle at actual cost. A final `budget_stop` is written only when nothing is in flight and the caps still block, and each record carries the blocking reason. The sync runner skips only the stopped vendor.
- **Trim:** `plan_trim` projects on the same basis and trims setup-2 profiles against min(config, phase) caps.
- **Projection vs caps ("fix whichever is wrong"):** the old $2.642 OpenAI mini-pilot projection came from stale `est_tokens` and a letters × 2 judge estimate. That projection was the wrong side and is replaced. The caps were also rounded from rounded parts: OpenAI mini-pilot 2.25 + 0.29 = 2.54, but the exact figure is 2.5431, and Google main is 59.1206. Those two are now cent ceilings ($2.55, $59.13). The totals ($9.53, $88.04) are unchanged and still cover the exact sums ($9.528, $88.037).
- **OpenAI balance:** the main-run guard stops OpenAI cleanly at the remaining balance ($20 − $1.6328 = $18.367) as a vendor-scope stop, and the other vendors continue. `plan_trim` reports `vendor_balance_remaining` and `balance_limited`, but does not trim on the balance. That is K3's decision.


## Code support (descriptive, packet-only): `llmbias.code_support`

This counts reason codes a model gave that the factor points shown in its own prompt do not support. It is descriptive only: no inference, no effect estimate, and not a validation or PASS criterion.

- **Factor-to-code map:** this is the scorecard table in `scoring.COMPONENTS`. Credit score → X02, DTI → C09, LTV → C23, reserves → X01, employment → C07. The code text is in `regb.py`.
- **Supported** means the mapped factor scored below its maximum. That matches the scorecard's own rule, which gives a code only when the gap is above zero.
- **Unsupported** means the factor is at full points, for example X01 with reserves at 10.0 / 10 (reserves of 6 months or more).
- **Not factor-mapped** covers Reg B codes with no scorecard factor. They are counted separately and are never treated as unsupported.
- **Packet-only:** the points always come from the model's own prompt.
  - Setup 3 (M1a v2) uses the "- factor: p / max" lines exactly as shown, to 1 dp. A factor is full when p ≥ max.
  - Setups 1–2 take the applicant values shown in the prompt and convert them with the scorecard thresholds. Full means credit ≥ 760, DTI ≤ 36%, LTV ≤ 80%, reserves ≥ 6 months, tenure ≥ 5 years.
  - The logged `tool_components` are used only when the record has no prompt, and those cases are flagged `source: record`.
- **Population:** valid model-deny outputs, since approve outputs carry no codes. Results are reported per vendor × setup.
- The metric appears in the `smoke2_setups12.py` summary.

## Mini planted-bias check, 15.9 (5b): `llmbias.positive_control`

Built to the signed rulings memo `memos/planted_check_rulings_15.9.md` (sha256 `843fcabd8b392da40e24678e4bc51045fef4d069d42a3382c2f451301274ea17`). The governing spec is 15.9 (`8c30a41f…`).

- **Files:**
  - config: `configs/minipilot_pc_batch.yaml`, phase `minipilot_pc`;
  - driver: `scripts/run_minipilot_pc.py`, which has a no-network `--dry-run`;
  - tests: `tests/test_positive_control.py`.
  - The core mini-pilot config is unchanged.
- **Texts and codes:** version `pc-v1`, with inert sentences length-matched in characters (R1/R2). The M1a instructed codes are drawn with SeedSequence(15867) child 0. The code map sits in the config (sha256 `7ee9d9ed…`), and the guard re-derives it.
- **Regex:** `PC_M1B_REGEX` v2 is frozen with sha256 `fb931ecd…`, which is checked at import and in the config. It is applied with `re.search` (no flags) to `" ".join(letter.lower().split())`.
- **Compliance (R4):** treated minus base on valid dose-1.0 calls, pooled over proxies, per vendor × outcome. For M1b it is judge OR regex, with judge-only and regex-only reported alongside.
- **Dose-0 (R5):** an exact two-sided sign-flip test per vendor × proxy × outcome (15 cells). Only pass/fail and `n_complete_units` are written.
  - Implementation choice for the majority-of-3 rule: Y = 1 if at least 2 valid repeats show the feature, Y = 0 if at least 2 do not, otherwise the unit is incomplete. M1a uses the (2a) mode (a code listed first in at least 2 repeats).
- **Caps and retries (R6/R7):** caps are $3.41 total, OpenAI $0.96 (including the judge), Anthropic $0.26, Google $2.21. There is no trimming; a worst case above any cap refuses the run. One identical retry is allowed. Failures are reported as separate pc rows (rates only).
- **Run order and judge (R8):** the check refuses to start until the core run is fully collected. Vendors with a core budget stop or a Haiku thinking violation are marked "not run". The judge sees M1b letters only, with the hedging prompt only. "Not evaluable" and "not run" never count as pass.
- **Packet notes** (written verbatim to `pc_mini_packet.md`):
  - the R5 sentence;
  - "The M1b regex can false-match factual 'never' sentences, which is why the judge-only rate is reported alongside."

## Implementation notes (planted check)

- co-batching of treated and base pairs is guaranteed on first submission only; retries and Gemini round 2 are regrouped.
- Majority of 3: a unit counts if at least 2 valid repeats agree; otherwise it is incomplete. Incomplete units are reported per cell.
- Dose-0 failure and contradiction rows are pooled over role in the packet; the role split happens only after unblinding. Dose-1.0 rows stay split.
- Haiku gate fails closed: if any core Haiku call lacks a numeric reasoning_tokens or a thinking field (or there are no Haiku core calls), Haiku is "not verified" and its planted calls are not submitted.
