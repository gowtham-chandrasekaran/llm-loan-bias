# Setup-2 re-test under amendment 15.9.1: plain-language summary

Oct 20, 2026. **Operational results only**: tool use, retries, stops, errors, cost. No effect estimates, no decisions
by variant and no treated-vs-base comparisons have been computed from these runs. The run folders are held until the
15.8.5 labeling closes (checksums in [HELD_FILES.md](HELD_FILES.md)).

## What 15.9.1 changed, and why

In setup 2 ("AI decides, with a scoring tool"), the model is given a credit-scoring tool and then writes its decision
and letter. Until 15.9.1 the model could choose whether to call the tool. In the core mini-pilot (Oct 18), Claude
Haiku 5.5 often skipped it. Out of its 600 setup-2 calls:

- on the first attempt, 513 called the tool, 53 returned a blank letter without calling it, and 34 wrote a full letter
  without calling it;
- after one retry of the 53 blank answers, the final count was 549 tool calls (91.5%), with 16 still blank and 35 letters
  written without the tool.

That is below the pre-stated 95% bar. OpenAI and Google called the tool in 600 of 600 calls each.

Amendment 15.9.1 makes the first-round tool call **required** for all three vendors (OpenAI `tool_choice` naming the
function; Anthropic `tool_choice` naming the tool, with thinking disabled; Gemini function-calling mode `ANY` limited to
the scoring tool). The second round, where the model writes its answer after seeing the score, is unchanged and not
forced. Setup 2 now always shows the model the score before it decides. It no longer measures whether a model would
choose to use the tool. Every setup-2 call must have exactly one forced tool call, otherwise the run stops.

## Probe (Oct 20, 18:13 ET)

A small live check before the re-test: 2 profiles x 3 vendors x setup 2, through both the one-by-one (sync) and the
batch APIs, 12 calls in all, cap $0.15.

- **Passed** on both paths: every call made the forced tool call on its first attempt, returned a valid final answer,
  and made at most one tool call. Haiku's thinking stayed off (0 reasoning tokens, no thinking text).
- No retries, stops or errors. Spend $0.0180.
- In one sync call (OpenAI, an approve-by-score profile) the model's final decision differed from the tool's
  recommendation. Setup 2 allows this, so it counts as a normal result, not a fault.

## Re-test results (Oct 20, 19:02-19:29 ET)

Setup 2 re-run on the same 25 pilot profiles, 8 variants and 3 repeats as the core mini-pilot, for all three vendors:
600 calls per vendor, 1,800 in total, through the batch APIs, plus the judge on all 1,800 letters. Run
`20261020T190210-ee9b9f61-a646ff-batch`, code at commit `fd483bc`.

| | OpenAI `gpt-5.4-mini` | Anthropic `claude-haiku-5-5` | Google `gemini-3.8-flash` |
|---|---|---|---|
| Calls collected | 600 / 600 | 600 / 600 | 600 / 600 |
| Forced tool call on the first attempt | 600 / 600 | 600 / 600 | 600 / 600 |
| Forced tool call, final | 600 / 600 | 600 / 600 | 600 / 600 |
| Retries | 0 | 0 | 0 |
| API errors | 0 | 0 | 0 |
| Spend (actual) | $0.8696 (incl. judge $0.0596) | $0.2220 | $0.7292 |
| Cap | $1.24 | $0.39 | $2.25 |

- **Stops:** none (no compliance stop, no budget stop, no batch left unfetched).
- **Valid answers:** every call returned a schema-valid final answer, with exactly one tool call, none truncated and
  none refused.
- **Haiku reasoning tokens:** 0 in round 1 and 0 in round 2 (`[0, 0]`) on all 600 calls, and no thinking text. So
  "thinking disabled" held with the forced tool call.
- **Judge:** complete. All 1,800 letters were judged (3,090 judge requests, all answered, no budget stop).
- **Spend:** $1.8208 in total against $3.88 in caps. Before the run, the expected cost was $1.70 and the worst case on the
  cap basis was $3.87. The amendment had estimated "about $1.82". Cumulative spend for the project is now **$16.4823**
  of the $200 limit.

### Compared with the core mini-pilot (Haiku only; tool use, not outcomes)

| Haiku setup-2 calls | Core mini-pilot (tool optional) | Re-test (tool forced) |
|---|---|---|
| Called the tool on the first attempt | 513 / 600 | 600 / 600 |
| Blank letter without the tool, first attempt | 53 | 0 |
| Full letter without the tool, first attempt | 34 | 0 |
| Retried | 53 | 0 |
| Called the tool, final | 549 / 600 | 600 / 600 |

Forcing the call removed the problem it was meant to fix. OpenAI and Google were already at 600 / 600 and stayed there.

## A quirk worth knowing: Gemini "thought signatures"

Gemini attaches an opaque `thought_signature` to its answers even with thinking switched off (`thinking_budget` 0).
In the re-test, every Gemini call carried one in round 1 and one in round 2 (107 bytes each, all different). The
round-1 signature has to be sent back to Gemini in round 2 as part of the conversation history, so the live round-2
request differs from the offline test fixture in that history field only. The fixture pins the request template, and
the history is data. The records keep only each signature's sha256 and length, never the value. In Gemini's batch JSON,
the signature arrives as URL-safe base64; the code decodes either form, so the one-by-one and batch paths give the same
hash.

## Next steps

1. **Replace the core setup-2 data (amendment item 5).** The re-test data replace the core mini-pilot's setup-2 data
   unconditionally, whatever they show. The old setup-2 records are archived read-only, are never analysed for
   effects, and are reported only as operational rows in an appendix. Setups 1 and 3 of the core run are unchanged. The
   tool for this is `python -m llmbias.s2_replace`; it has not been run on the real core yet.
2. **Planted-bias check (item 6).** Bias is planted at known sizes to check that the pipeline detects bias of a known
   size before a null result is trusted.
3. **Decision gate (15.9(5b)).** A written go/no-go decision from the mini-pilot results.
4. **Main run: gated.** No paid call after the mini-pilot without the written gate decision.
