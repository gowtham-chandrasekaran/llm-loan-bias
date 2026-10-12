# Model list prices (USD per 1M tokens), retrieved 2026-10-12, ~13:00 EDT

Sources (official vendor pages, fetched 2026-10-12):
- OpenAI: https://platform.openai.com/docs/pricing
- Anthropic: https://docs.anthropic.com/en/docs/about-claude/pricing (the page points to claude.com/pricing as canonical)
- Google: https://ai.google.dev/gemini-api/docs/pricing (page says "Last updated 2026-10-09 UTC")

Model IDs were confirmed against each vendor's models.list endpoint on 2026-10-12, with one tiny completion per vendor (see "Connectivity check").

| Vendor | Model (API id) | Role | Input | Output | Batch in/out | Notes |
|---|---|---|---|---|---|---|
| OpenAI | gpt-5.4-mini | **pilot main (mini-class)** | 0.75 | 4.50 | 0.375 / 2.25 | Reasoning tokens are billed as output; the API exposes reasoning *summaries* only |
| OpenAI | gpt-5.6-luna | alternative | 0.20 | 1.20 | 0.10 / 0.60 | Newer tier; where it sits in the "mini" class is unclear (naming) |
| OpenAI | gpt-5-mini | alternative | 0.25 | 2.00 | 0.125 / 1.00 | |
| OpenAI | gpt-5-nano | **judge (cheapest)** | 0.05 | 0.40 | 0.025 / 0.20 | |
| Anthropic | claude-sonnet-5 | **pilot main (Sonnet class)** | 2.00 | 10.00 | 1.00 / 5.00 | Intro price became standard (page footnote 3). Claude 4.7+ tokenizer gives ~30% more tokens |
| Anthropic | claude-haiku-5-5 | cheaper alternative (Haiku class) | 0.10 | 0.50 | 0.05 / 0.25 | Prompts ≤100k tokens |
| Google | gemini-3.8-flash | **pilot main (Flash class)** | 0.75 | 3.75 | 0.375 / 1.875 | Promotional through 2026-12-31; **doubles to 1.50 / 7.50 on 2027-01-01**. Output includes thinking tokens |
| Google | gemini-3.5-flash-lite | alternative | 0.30 | 2.50 | 0.15 / 1.25 | |

## Pilot cost estimate (repeats included)
Design: 25 borderline profiles × 8 variants (base, control_dup, name_b, name_w_placebo, name_b_placebo, zip_b, zip_placebo, anchor) × 3 repeats = 600 items per model per setup.
Token assumptions: 750 input tokens per call. The measured rendered prompt is ~2,150–2,350 chars (≈560 tokens at 4 chars/token); the extra margin covers tokenizer differences. 350 visible output tokens. Reasoning adds 400 at OpenAI/Google "low" and 0 for Sonnet with thinking off. Judge: 2 calls per letter (upper bound), 350 in / 40 + 50 out.
Estimates come from `python -m llmbias.runner --config configs/pilot.yaml --estimate-only [--batch]`.

| Scenario | Calls (incl. judge) | Standard USD | Batch USD |
|---|---|---|---|
| A. Setups 3+1, 3 vendors (configured: gpt-5.4-mini, claude-sonnet-5, gemini-3.8-flash) | 10,800 | **15.16** | 7.58 |
| B. A + setup 2 (tool-assisted, 2 rounds) | 18,000 | 26.92 | 13.46 |
| C. A with claude-haiku-5-5 instead of Sonnet 5 | 10,800 | 9.46 | n/a |

Per-model/setup breakdown for A: gpt-5.4-mini $2.36 per setup; claude-sonnet-5 $3.00 per setup; gemini-3.8-flash $2.03 per setup; judge $0.39.

**Takeaway:** at standard prices, scenario A (~$15.2) sits right at the $15 pilot cap, and Sonnet alone (~$6.0) is over the $5 per-vendor cap. The code's hard stop will halt Anthropic partway through the run. Fitting under the cap needs one of: batch mode, Haiku, or fewer variants/repeats. These estimates are assumption-based; reasoning-token usage is the largest uncertainty. Gemini spent 58 thinking tokens on a 3-token "Reply OK" prompt.

## Connectivity check (2026-10-12, ~13:01 EDT; one tiny call per vendor, a few cents in total)
- OpenAI gpt-5.4-mini → resolved to `gpt-5.4-mini-2026-03-17`, 8 in / 5 out tokens.
- Anthropic claude-sonnet-5 → call succeeded, but the response began with a ThinkingBlock even though thinking was not requested. My print code crashed on it, so I did not log the token counts. I did not retry, to stay within the one-call limit. Implication: Sonnet 5 may think by default, and the adapter's `thinking: disabled` path has not been verified.
- Google gemini-3.8-flash → 3 in / 1 out / 58 thoughts tokens.

## v2 re-estimate (2026-10-12): batch, all 3 setups, lowest reasoning setting (`python -m llmbias.batch --config configs/pilot_batch.yaml --plan-only`)
Assumptions: 750 input / 350 visible output per call, 0 reasoning (thinking off, verified live). Setup 2 is assumed to always take 2 rounds, with tool overhead measured in the live tool checks (OpenAI ~300, Anthropic ~1,200, Google ~400 input tokens).

| Vendor | Setup 3 | Setup 1 | Setup 2 (all 25 profiles) | Total untrimmed | Total as run (setup 2 trimmed to 17 profiles) |
|---|---|---|---|---|---|
| OpenAI gpt-5.4-mini (+ gpt-5-nano judge) | 0.64 | 0.64 | 0.96 | 2.53 (incl. judge 0.29) | 2.19 |
| Anthropic claude-sonnet-5 | 1.50 | 1.50 | 2.85 | **5.85 (> $5 vendor cap)** | 4.94 |
| Google gemini-3.8-flash | 0.56 | 0.56 | 0.89 | 2.01 | 1.73 |
| **Total** | | | | **10.39** | **8.86** |

Total is under the $15 cap. Only Anthropic's $5 per-vendor cap binds. The pre-seeded trim therefore drops 8 of 25 profiles from setup 2 for all vendors (P000, P005, P002, P006, P013, P017, P020, P007). Raising Anthropic's cap to $6 would keep all 25.

## Live checks 2026-10-12 (~13:20 EDT), via the project adapters: one thinking-off call + one tool call per vendor (docs/live_checks_*.json)
| Vendor/model | Lowest setting | Result | Tool call |
|---|---|---|---|
| OpenAI gpt-5.4-mini | reasoning.effort=none | OK; 18 in / 5 out / 0 reasoning | called score_application with correct args; final JSON approve, 60.129; 412 in / 59 out |
| Anthropic claude-sonnet-5 | thinking disabled | OK; no thinking block; 19 in / 4 out | correct args; approve 60.129; 1,542 in / 169 out |
| Google gemini-3.8-flash | thinking_budget=0 | OK; 0 thought tokens; 10 in / 1 out | correct args; approve 60.129; 534 in / 64 out |
Total cost of these six calls: under $0.01 at list prices.
