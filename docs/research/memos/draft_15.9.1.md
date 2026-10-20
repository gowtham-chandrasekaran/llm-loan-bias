> **PENDING APPROVAL — NOT LOGGED.** Amendment 15.9.1 is a draft. It is not in the amendment log (`../amendment_log.md`) and has no effect until Gowtham approves it directly after Reviewer hash sign-off and Project Owner review. The probe and the setup-2 re-test it describes have not been run. Copied verbatim from `research/memos/draft_15.9.1.md` (sha256 of the original, before this header was added: `5041167fe1ed230086a89b940d7c54baa639aaccae77c4f1487885919de8e85b`) on Oct 20, 2026.

---

# Amendment 15.9.1 (dated Oct 18, 2026): forced round-1 tool call in setup 2

**Status:** awaiting Reviewer hash sign-off, Project Owner review, and Gowtham's direct confirmation. Amends draft_15.9.md (logged sha256 8c30a41f3243f133ccbca9dba4e4e22e9b249530e96cff58d14298b7543e241b).

## Trigger
The mini-pilot core (commit ca9a5e1, run 20261018T124607-e5e8b45a-1a973e-batch) failed the pre-stated setup-2 tool-call criterion (15.9 line 171, ">= 95%") for Anthropic Haiku 5.5: final 549/600 = 91.5%. 51 final records skipped the tool: 16 invalid blank letters and 35 valid letters written without tool use. First attempts: 513 tool / 53 blank-letter no-tool / 34 full-letter no-tool. Retries (53): 36 tool / 16 blank / 1 full. OpenAI and Google: 600/600 tool use. No effect estimates were computed or viewed.

## Change
1. This is a deliberate design change after a failed pre-stated criterion, not a correction of an oversight. 15.9 line 192 states: "The tool call is optional in our design and its rate is itself measured, so we do not force it with `tool_choice`." That sentence is replaced by: "The round-1 tool call is forced for all vendors by naming the single tool `score_application` (Anthropic `tool_choice = {"type":"tool","name":"score_application"}`; OpenAI `tool_choice = {"type":"function","name":"score_application"}`; Gemini `function_calling_config.mode = ANY` with `allowed_function_names = ["score_application"]`). Round 2 is unchanged and does not force a tool; `max_tool_calls_per_call = 1` remains asserted." The system-prompt mitigation line from 15.9 is kept for all vendors.
2. Construct: setup 2 narrows from "the model chooses whether to consult the scorer" to "the model is always shown the scorer before deciding". This matches an assistive-scorer deployment, and M2 is defined against the tool's deterministic decision. Voluntary tool use is no longer measured; the paper must state this.
3. 15.9 line 171 ("Setup-2 tool-call rate >= 95%") becomes an implementation compliance check: the forced round-1 call rate must equal 100%; anything lower is a code bug and stops the run. All other validity and missingness thresholds are unchanged. The blank-letter "Pending scorecard evaluation" pattern is kept as a named failure reason.

## Re-test and data replacement
4. Setup 2 is re-run on the 25 pilot profiles for all three vendors (1,800 calls plus judge) under phase `minipilot_s2_retest`, caps $3.88 total (OpenAI $1.24 incl. judge, Anthropic $0.39, Google $2.25); expected about $1.82.
5. Replacement rule, logged before any re-test call: the new setup-2 data replace the core setup-2 data **unconditionally**, whatever the re-test shows. The superseded data are archived read-only, never analysed for effects, and reported only as operational rows (first-attempt and final counts per vendor) in an appendix. Setups 1 and 3 from the core are unchanged.
6. The planted check runs after the re-test, and its M2 arm uses the forced setting.

## Probe before the re-test
7. Before the re-test, a live probe runs under phase `minipilot_s2_probe` (cap $0.15): 2 pilot profiles (1 tool-approve, 1 tool-deny) x 3 vendors x setup 2, on both the sync and batch paths. Pass = forced round-1 tool call present, schema-valid final, at most 1 tool call, and Haiku reporting thinking disabled with numeric thinking_tokens. Any failure stops the work and returns to amendment; the re-test does not start. Probe outputs are excluded from all analyses and from the replacement data; only pass/fail and operational rows are reported. The probe and the re-test each require Gowtham's separate direct "run it" to Developer.

## Ledger
8. Spent to date per spend_ledger (budget.py basis): $14.6435 (OpenAI 3.6086, Anthropic 7.4071, Google 3.6278): pilot-v1 seed $10.5810, smoke 1 $0.0372, smoke 2 $0.0380, core $3.9873. Worst case after the probe ($0.15) and re-test ($3.88) caps: $18.6735 of the $200 limit. The pilot-v1 raw ledger was re-checked and sums exactly to its seed at batch prices (15,753 rows, 0 mismatches); repeated call_ids are setup-2 rounds 1 and 2 and retries billed under one call_id, not duplicates.
