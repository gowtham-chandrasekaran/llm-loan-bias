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
