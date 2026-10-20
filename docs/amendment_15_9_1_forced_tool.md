# Amendment 15.9.1: forced round-1 tool call in setup 2 (implementation notes)

Governing text: `memos/draft_15.9.1.md`, logged sha256 `5041167fe1ed230086a89b940d7c54baa639aaccae77c4f1487885919de8e85b`
(amendment_log sha256 `b6dc76ad…b1b5`), approved by Gowtham on Oct 20, 2026.

## Item 1: requests (sync clients and batch bodies, every attempt; round 2 unchanged and not forced)

| vendor | round 1 (forced) | round 2 |
|---|---|---|
| OpenAI (Responses) | `tool_choice = {"type": "function", "name": "score_application"}`; strict tool, `parallel_tool_calls: false` | no `tool_choice` (strict tool, `parallel_tool_calls: false` kept) |
| Anthropic (Messages) | `tool_choice = {"type": "tool", "name": "score_application"}`; `thinking: {"type": "disabled"}` | no `tool_choice`; thinking disabled |
| Gemini | `tool_config.function_calling_config = {"mode": "ANY", "allowed_function_names": ["score_application"]}` | `mode: NONE` + `response_json_schema` (final_turn), unchanged |

Anthropic compatibility: forced `tool_choice` (`any` / `tool`) is rejected only with manual extended thinking enabled and on
models that reject forced tool use altogether (Opus 5.5, Sonnet 5.5, Fable 5.1, Mythos 5.1); Haiku 5.5 accepts it, and a forced call skips thinking
(docs.claude.com, "Implement tool use" > "Forcing tool use", read Oct 20, 2026). Our Haiku requests always send
`thinking: {"type": "disabled"}` and `config_guard` refuses any other Anthropic effort, so the combination is valid. Live
confirmation is the probe's job (item 7).

Golden payloads: `tests/fixtures/request_payloads_15_9_1.json` (new) vs `request_payloads_08082d6.json` (before). Exactly 12 of
46 keys differ: `sync|{openai,anthropic,google}|s2|{P000,P001}` (round-1 entry only) and `batch|r1|…|s2|…`, each by the forced
fragment alone. Setups 1 and 3, batch round 2 and the judge are byte-identical (`tests/test_request_payload_golden.py`). The
updated fixture generator reproduces the old fixture byte-identically from 499230e.

## Item 3: compliance

Tool rule on EVERY attempt (Reviewer condition 1 on 91baba3, `output_schema.tool_rule`): an attempt with no tool call
(`no_tool_call`), more than one (`multiple_tool_calls`, `MAX_TOOL_CALLS_PER_CALL = 1`) or a call to another name (`wrong_tool`)
stops the run on that attempt, with no retry, whatever the letter looks like. Only a blank letter (empty answer text or an empty
`letter`) after exactly one correct `score_application` call gets the single 15.9 retry; if the retried letter is still blank the
final verdict is `blank_letter` and the run stops. `output_schema.s2_compliance(rec)` on the final record applies the tool rule
first, then `blank_letter`; any non-None value stops the run (sync: `runner.run` stops after logging the record; batch: no
further submissions, pending requests are written with `error = compliance_stop: …`). `run_meta.compliance_stop` records the
reason. API errors and budget stops stay missingness, not compliance failures.

Round-2 tool requests (round 2 is not forced; Gemini round 2 is mode NONE) are recorded in `tool_calls` with `"round": 2`
and `result: null` (not executed), in sync clients and batch collect, so they count toward `multiple_tool_calls` (Reviewer
condition a). A forced call whose arguments fail to parse or validate (`clients.exec_tool` -> `ToolArgumentError`; OpenAI
batch arguments are no longer parsed in `parse`, so bad JSON cannot crash collect) is an answered attempt: it is billed and
labelled `tool_args_invalid` (a tool-rule reason: stop, no retry) (condition b). Pure API / transport errors stay missing
data, not compliance failures; `run_meta.error_counts` (sync and batch) and the probe report count `api_error`,
`budget_stop`, `compliance_stop` (not sent / cancelled after the stop) and `tool_args_invalid` per vendor.
Count before content: when more than one tool call is made and a later one (e.g. the second) has bad arguments, the stop
is labelled `multiple_tool_calls`, not `tool_args_invalid` (`tool_rule` checks the count, then the name, then the arguments;
the sync clients stop at the bad call and record the calls made so far plus that one).

Batch compliance stop (condition 5): `BatchRun.stop_and_drain` books the worst case of every batch still in flight as a keyed
`inflight_reserve` row in the run and cumulative ledgers (`compliance_stop.inflight_at_stop`, `run_meta.inflight_worst_at_stop_usd`),
cancels those batches (`Backend.cancel`: OpenAI `batches.cancel`, Anthropic `messages.batches.cancel`, Gemini `batches.cancel`),
then collects until nothing is in flight. Partial results of a cancelled batch are recorded at actual cost and the batch's
reserve is released by a keyed negative `inflight_release` row, so the net equals actual spend. A batch that never drains keeps
its reserve, so cumulative spend cannot undercount. `compliance_stop.drain` records completeness. Resume is idempotent.

s2-retest fixes (branch s2-retest-fixes, before the re-test):
- Order at the stop: the plan (in-flight batches, reserve amounts, `stop_ts`, `drain_deadline_ts`) is persisted first;
  then per batch still in flight the keyed reserve row is written to the run and cumulative ledgers and only then
  `b["reserved"]` is persisted. `BudgetGuard.release` writes a ledger's release only where that ledger holds the reserve,
  for exactly the reserved amount; `reserve` writes nothing once either ledger holds the release. A batch that settled
  before its reserve was booked gets `reserve_skipped`. `tests/test_s2_retest_fixes.py` crashes before and after every
  ledger append and state save and checks each ledger nets exactly the actual spend after resume.
- Ended batches (done / cancelled / expired / failed) have their results fetched and billed before the release; OpenAI and
  Gemini `expired` map to `expired` (was failed). A fetch that keeps failing on a non-done batch settles it without results
  and keeps the reserve (`reserve_kept_no_results`).
- The drain deadline is `stop_ts + drain_max_wait_s` (config, default the loop's max wait), persisted, so a resume does not
  extend it. A cancel that raises keeps `cancel_error`, is retried on resume, and its requests are not tagged
  `compliance_stop: cancelled batch` (that tag needs an accepted cancel and a cancelled result).
- Reserves use `budget.call_worst_cap_basis`: setup 2 = max(pilot basis, S2_CORE_P95), as the re-test caps.

Each setup-2 record carries `first_attempt_forced_call_ok` (attempt 1 obeyed the tool rule). `setup_summary` reports
`first_attempt_forced_call_rate` per vendor (re-test summaries) and `probe_s2.check_calls` reports it per path and vendor.

Reasoning tokens (Reviewer condition 3): sync Anthropic `reasoning_tokens` is summed over both rounds, as batch collect does,
and is None when either round lacks the count, so round-1 thinking fails the probe's Haiku check.

## Items 4 and 7: phase caps (`budget.PHASE_CAPS`)

- `minipilot_s2_probe`: total $0.15 (sync + batch together; the batch path gets $0.15 minus the sync path's actual spend).
- `minipilot_s2_retest`: total $3.88; OpenAI $1.24 (incl. judge), Anthropic $0.39, Google $2.25. Same basis as the other caps
  (p95 tokens x1.25, Google price x2, +5% reruns, judge x3, cent ceiling), 600 setup-2 calls per vendor and 1,800 judged letters,
  taking per vendor the larger of the pilot token basis and the core mini-pilot's setup-2 actuals (`budget.S2_CORE_P95`):
  OpenAI max(1.0749, 1.2361), Anthropic max(0.2879, 0.3857), Google max(2.2497, 2.0781). The test
  `test_retest_caps_reproduced_on_cap_basis` recomputes all three.

## Item 7: probe

`configs/minipilot_s2_probe.yaml`, `src/llmbias/probe_s2.py`, `scripts/run_s2_probe.py`.
- Profiles (deterministic; the guard re-derives them): the 25 pilot profiles on the base variant, sorted by profile_id; the
  lowest profile_id whose `score_application` decision is approve (P001) and the lowest that is deny (P000).
- 2 profiles x 3 vendors x setup 2 x 1 repeat, sync path (`runner.run`) then batch path (`batch.start`) = 12 calls,
  24 API requests (48 at most if every call were retried; 6 batch jobs on the batch path: 3 vendors x 2 rounds).
- Pass, per call (automated, `probe_s2.check_call`): forced round-1 tool call present (also on attempt 1 if retried),
  schema-valid final, at most 1 tool call per attempt, and for Haiku: effort none, no thinking text, numeric thinking tokens
  equal to 0. Plus 6 answered calls per path and no budget / compliance stop. A failed sync path stops the work before the
  batch path. Exit code 1 = FAIL: return to amendment; the re-test must not start.
- Without `--live` the script runs a free mock dry run (mocks emulating each vendor; the batch path builds the real vendor
  bodies and checks round 1 forced / round 2 not). `--allow-paid` without `--live` is refused. The cost estimate is printed
  before any call: worst $0.0352 on the cap basis ($0.0703 if every call were retried) vs the $0.15 cap; expected $0.0167.
- Outputs: `runs/probe_s2/<probe_id>/{sync,batch}/…` with a `PROBE_EXCLUDED_FROM_ANALYSIS` marker; every record and run_meta
  carry `probe` + `excluded_from_analysis`. `analysis.load_calls` / `analyze` refuse a run under a marker, with a probe
  run_meta, or with any probe-tagged record.

### Probe run log (live probe 20261020T181308-684ca6-live, from 0d9f54b; PASS, $0.01798, 18 ledger rows tagged minipilot_s2_probe)
- (a) Gemini round-2 payloads. The request fixture (`tests/fixtures/request_payloads_15_9_1.json`, sha256 3fda5441…)
  pins the request TEMPLATE: prompts, tools, tool_config, generation config and the round structure. The round-2 history
  is DATA. It is the model's own round-1 turn sent back (live: including the opaque `thought_signature` Gemini attaches to
  the function-call part) plus our tool result. It therefore differs per call and is not part of the template. The
  batch round-2 fixture entries are built with history None (contents = the user turn only), and the sync fixture's
  round 2 uses fake round-1 parts without a signature. So the live round-2 payload differs from the fixture in the
  history only. Thinking is off (`thinking_budget` 0), and the fixture is unchanged. From s2-retest condition 3 on, the
  response side logs each signature as sha256 + length (never the raw value; see below).
- (b) Sync OpenAI P001: the forced round-1 tool call returned approve (score 61.877 ≥ threshold 54.5), and the final
  letter says deny (`override` true). This is a valid setup-2 override: the model may disagree with the tool. It is
  not a fault, and the probe criteria (forced call present, schema-valid final, ≤ 1 tool call) all passed. On the batch
  path the same profile ended approve.

## Items 4-6: setup-2 re-test (`configs/minipilot_s2_retest.yaml`, `src/llmbias/s2_retest.py`, `scripts/run_s2_retest.py`)
- Design: setups [2] only. The 25 pilot profiles x 8 variants x 3 repeats give 600 calls per vendor and 1,800 in total,
  with batch mode, the judge on the 1,800 letters, and phase `minipilot_s2_retest`. Everything else is identical to
  `configs/minipilot_haiku_batch.yaml`: seeds, variants, models and efforts, judge, max tokens, prompt version, lexicon
  hash, M1a flag, Google schema mode, chunking and est_tokens. `retest_guard` re-derives all of this, plus
  `amendment_sha256`, `replaces_setup2_of` = core run 20261018T124607-e5e8b45a-1a973e-batch, and
  `require_all_profiles: true`. That flag makes `BatchRun.init` refuse rather than trim setup-2 profiles.
- Expected cost: $1.70 on mean tokens (OpenAI 0.7221 incl. judge, Anthropic 0.1938, Google 0.7859), versus the memo's
  "about $1.82". This is an expected figure, not a cap; the caps and their basis are unchanged, so no amendment is needed.
- Caps: OpenAI 1.24, Anthropic 0.39, Google 2.25, total $3.88 (= `PHASE_CAPS`). The cap basis (`budget.retest_cap_basis`,
  written to run_meta `cap_basis`) is, per vendor, max(pilot token basis, core setup-2 p95 actuals) at p95 x1.25, with
  Google price x2, +5% reruns, judge x3 billed to OpenAI, and a cent ceiling.
  Worst case on that basis: OpenAI 1.2362, Anthropic 0.3857, Google 2.2497, total 3.8716. Expected (mean tokens): 0.7221,
  0.1938, 0.7859, total 1.7018.
- Driver: `python scripts/run_s2_retest.py` runs a free mock dry run. Mocks emulate each vendor at its batch price, the
  real vendor bodies are built offline, and a mock judge is used. The driver prints the per-vendor estimate against the
  caps first, then shows the item-5 replacement on a COPY of the core. `--estimate-only` prints just the estimate.
  `--live` also needs `--probe-report` pointing to a passing live probe report, and Gowtham's direct "run it".
- Blind keys (condition 1): `judge.BlindKey` writes the judge key only to `<private>/<run_id>/blind_key.jsonl`. The
  private root is `/home/box/private/blind_keys`, or `LLMBIAS_PRIVATE_DIR`; the dry run uses `/home/box/private/blind_keys_dryrun`.
  The directory is mode 700 and the file 600. Its sha256 is appended to the per-run MANIFEST and the top-level MANIFEST
  (both 600). A re-judge keeps the earlier key as `blind_key.superseded-<ts>.jsonl`. Nothing goes into the run folder.

## Item 5: replacement of the core setup-2 data (`src/llmbias/s2_replace.py`)
`python -m llmbias.s2_replace --core runs/<core> --retest runs/<retest> [--out runs/<core>+s2retest]`. This runs after the
re-test and its judge. The replacement is unconditional: it does not look at any outcome.
1. Validate. The core is phase minipilot. The re-test is phase minipilot_s2_retest, setups [2], with `replaces_setup2_of`
   = the core and the logged hash. Neither run is a probe. Every re-test batch is collected (none pending, submitted or
   unfetched). Both sides cover exactly the same (vendor, profile, variant, repeat) grid with one record per cell, and
   the models are the same. The re-test has been judged.
2. Archive (read-only, nothing deleted). `<core>/superseded_setup2_15_9_1/` holds:
   - `calls_setup2.jsonl`: the core setup-2 lines, byte for byte.
   - `judgments_setup2.jsonl`: their judgments.
   - `operational.json`: the appendix rows only. Per vendor, it gives first-attempt and final counts of tool,
     blank-letter no-tool, full-letter no-tool and error. On the real core, Anthropic is first attempt 513 / 53 / 34,
     retries 36 / 16 / 1, final 549 tool; OpenAI and Google are 600/600.
   - `MANIFEST.json`: sha256 of every file and of the core sources.
   - A `SUPERSEDED` marker.
   Files are 0444 and the folder 0555. A re-run verifies the hashes and refuses if anything changed.
3. Merge. `<out>/calls.jsonl` holds the core setup 1 and setup 3 lines plus all re-test setup-2 lines, each tagged
   `replacement_source`. `judgments.jsonl` is split the same way. `run_meta.json` is the core meta plus
   `setup2_replacement` (provenance, source and output hashes, counts). There is no spend ledger here: spend stays in
   the run ledgers and the cumulative ledger.
4. Pointer. `<core>/SETUP2_SUPERSEDED.json` (0444). From then on, `analysis.refuse_superseded` makes `load_calls`,
   `analyze`, `labels.item_pool` and the judge refuse the core folder and anything inside the archive, and names the
   merged folder. The core `calls.jsonl` and `judgments.jsonl` are never modified. `positive_control.core_gate` (an
   operational reader) still reads the core batch state, so the item-6 planted check is unaffected.

## s2-retest condition 2: unfetched placeholder
An ended batch (expired, failed or cancelled) whose results fetch fails `RESULTS_FETCH_TRIES` (3) times may still have
partial charges the vendor billed. Unless a compliance-stop reserve already covers it, `BudgetGuard.placeholder` books
a keyed worst-case row in the run ledger and the cumulative ledger. The row is ("placeholder:<bid>", 0, "placeholder",
bid), kind `unfetched_placeholder`, and its amount is the batch's `worst`, the amount checked against the caps at
submit. The ledgers can therefore only overcount. The batch becomes `unfetched`, and its requests keep `unfetched` and
an error saying so. Later passes and resumes retry the fetch (`BatchRun._refetch`). Only a fetch that succeeds bills
the requests that had no result, and then `release_all` writes `unfetched_release` (and the in-flight release, if
reserved). The release is per ledger, only where the hold exists, for the held amount, and once only. A hold is never
re-booked after its release. run_meta lists `unfetched_batches`.

## s2-retest condition 3: response logging (response side only; the request fixture stays at 3fda5441)
- Every record has `reasoning_tokens_by_round`: [r1] or [r1, r2] for setup 2, next to the summed `reasoning_tokens`
  (None if any round is None). This covers sync OpenAI, Anthropic and Gemini and every batch vendor.
- Gemini (sync and batch) also records `usage_metadata`, the full usage object of each round, and `thought_signatures`:
  [{round, part, sha256, len}] of every part's `thought_signature`. Hash and length are over the decoded bytes, so the
  sync (bytes) and batch (URL-safe base64 JSON from google-genai; standard base64 also accepted) forms agree. On sync,
  `thoughts_token_count` is kept as null when the SDK leaves it unset, instead of being dropped. The raw value is never written to the record.
  Note: batch `raw/batch_responses.jsonl` and `batch_state.json` (the round-2 history) still hold the raw signatures,
  because round 2 must send them back.
