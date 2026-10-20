# Cost estimate: Haiku swap (offline, no API calls)

Source tokens: `runs/20261012T143812-ff54324d-4745d6-batch/raw/calls.jsonl` (actual usage per call; setup-2 includes both tool rounds). Prices: `configs/prices.yaml` (batch = 50%).
Judge: gpt-5-nano batch, 336 in / 48 out tokens per judged item (pilot actual).

Per-call tokens used (mean / p95):

| vendor | setup | n | input mean | output mean | input p95 | output p95 |
|---|---|---|---|---|---|---|
| anthropic | 1 | 600 | 842 | 519 | 851 | 623 |
| anthropic | 2 | 479 | 3093 | 674 | 3110 | 840 |
| anthropic | 3 | 600 | 916 | 407 | 926 | 537 |
| google | 1 | 600 | 614 | 558 | 619 | 796 |
| google | 2 | 600 | 1672 | 364 | 1684 | 425 |
| google | 3 | 600 | 668 | 272 | 674 | 350 |
| openai | 1 | 600 | 556 | 243 | 561 | 292 |
| openai | 2 | 600 | 1429 | 273 | 1438 | 312 |
| openai | 3 | 600 | 607 | 193 | 613 | 231 |

## mini-pilot (25 profiles = 600 calls x 3 setups per vendor)

Calls per vendor per setup: 600. Worst = p95 tokens x1.25, Google promo price doubled, judge x3, +5% reruns.

| line | expected | worst |
|---|---|---|
| openai gpt-5.4-mini setup 1 | $0.45 | $0.68 |
| openai gpt-5.4-mini setup 2 | $0.69 | $0.98 |
| openai gpt-5.4-mini setup 3 | $0.40 | $0.59 |
| anthropic claude-haiku-5-5 setup 1 | $0.10 | $0.16 |
| anthropic claude-haiku-5-5 setup 2 | $0.19 | $0.29 |
| anthropic claude-haiku-5-5 setup 3 | $0.09 | $0.14 |
| google gemini-3.8-flash setup 1 | $0.84 | $2.72 |
| google gemini-3.8-flash setup 2 | $0.79 | $2.25 |
| google gemini-3.8-flash setup 3 | $0.46 | $1.43 |
| judge (gpt-5-nano) | $0.10 | $0.29 |
| subtotal openai | $1.54 | $2.25 |
| subtotal anthropic | $0.39 | $0.59 |
| subtotal google | $2.08 | $6.40 |
| **total** | **$4.11** | **$9.53** |

## main run (N = 231)

Calls per vendor per setup: 5544. Worst = p95 tokens x1.25, Google promo price doubled, judge x3, +5% reruns.

| line | expected | worst |
|---|---|---|
| openai gpt-5.4-mini setup 1 | $4.18 | $6.31 |
| openai gpt-5.4-mini setup 2 | $6.37 | $9.03 |
| openai gpt-5.4-mini setup 3 | $3.66 | $5.45 |
| anthropic claude-haiku-5-5 setup 1 | $0.95 | $1.44 |
| anthropic claude-haiku-5-5 setup 2 | $1.79 | $2.66 |
| anthropic claude-haiku-5-5 setup 3 | $0.82 | $1.31 |
| google gemini-3.8-flash setup 1 | $7.77 | $25.10 |
| google gemini-3.8-flash setup 2 | $7.26 | $20.79 |
| google gemini-3.8-flash setup 3 | $4.22 | $13.23 |
| judge (gpt-5-nano) | $0.90 | $2.70 |
| smoke test (36 calls = 12/vendor, setup 3, non-batch) | $0.04 | $0.08 |
| subtotal openai | $14.22 | $20.80 |
| subtotal anthropic | $3.56 | $5.42 |
| subtotal google | $19.25 | $59.12 |
| **total** | **$37.98** | **$88.12** |
| remaining budget $189.42 -> left | $151.44 | $101.30 |

Reference: the same main run with claude-sonnet-5 would cost $71.24 for Anthropic alone (expected).
