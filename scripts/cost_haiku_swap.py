"""Offline cost estimate for the Haiku swap (no API calls). Token counts come from the pilot's raw/calls.jsonl
(actual per-call usage, setup-2 totals include both tool rounds and the tool definition). Anthropic counts measured on
claude-sonnet-5 are reused for claude-haiku-5-5 (same post-4.7 tokenizer generation; Haiku's tool system prompt is 286 vs
354 tokens, ignored = conservative). Writes docs/cost_haiku_swap.md."""
import json, sys, yaml, pandas as pd
RUN = "runs/20261012T143812-ff54324d-4745d6-batch"
PR = yaml.safe_load(open("configs/prices.yaml"))
MODELS = {"openai": "gpt-5.4-mini", "anthropic": "claude-haiku-5-5", "google": "gemini-3.8-flash"}
JUDGE = ("openai", "gpt-5-nano")
REMAINING = 189.42                   # $200 - $10.58 pilot (pilot_observations.md)
VARIANTS, REPEATS = 8, 3

def price(v, m, batch=True):
    p = PR[v][m]; k = p.get("batch_discount", 1) if batch else 1
    return p["input"] * k / 1e6, p["output"] * k / 1e6

rows = [json.loads(l) for l in open(f"{RUN}/raw/calls.jsonl")]
d = pd.DataFrame([{"vendor": r["vendor"], "setup": r["setup"], "inp": r.get("input_tokens") or 0, "out": r.get("output_tokens") or 0} for r in rows])
d = d[d.inp > 0]
tok = d.groupby(["vendor", "setup"]).agg(inp=("inp", "mean"), out=("out", "mean"), inp95=("inp", lambda x: x.quantile(.95)), out95=("out", lambda x: x.quantile(.95)), n=("inp", "size"))
led = [json.loads(l) for l in open(f"{RUN}/raw/spend_ledger.jsonl")]
jl = [r for r in led if (r["vendor"], r["model"]) == JUDGE]
n_items = sum(1 for _ in open(f"{RUN}/raw/judgments.jsonl"))
j_in, j_out = sum(r["in"] for r in jl) / n_items, sum(r["out"] for r in jl) / n_items       # per judged item (letter + spans)

def per_call(v, s, case):
    t = tok.loc[(v, s)]
    if case == "expected":
        i, o = t.inp, t.out * (1.12 if (v == "google" and s == 1) else 1.0)       # +12% Google setup-1 output (token fix)
    else:
        i, o = t.inp95 * 1.25, t.out95 * 1.25                                      # p95 tokens, +25% length/tokenizer margin
    pi, po = price(v, MODELS[v])
    if case == "worst" and v == "google": pi, po = 2 * pi, 2 * po                  # promo ends 2026-12-31 (price doubles)
    return i * pi + o * po

def judge_item(case):
    pi, po = price(*JUDGE); m = 1.0 if case == "expected" else 3.0               # v1.2 prompts are ~2-3x longer
    return (j_in * pi + j_out * po) * m

def table(n_profiles, case, rerun=0.0):
    out = {}; calls = n_profiles * VARIANTS * REPEATS
    for v in MODELS:
        out[v] = {s: calls * per_call(v, s, case) * (1 + rerun) for s in (1, 2, 3)}
    out["judge"] = {"all": 3 * 3 * calls * judge_item(case)}
    return out, calls

SMOKE_PER_VENDOR = 12   # plan: smoke test = 36 calls, 12 per vendor, setup 3 only

def smoke(case):   # 12 setup-3 calls x 3 vendors at NON-batch prices
    tot = 0
    for v in MODELS:
        for s in (3,):
            t = tok.loc[(v, s)]; pi, po = price(v, MODELS[v], batch=False)
            f = 1 if case == "expected" else 1.25 * (2 if v == "google" else 1)
            tot += SMOKE_PER_VENDOR * f * ((t.inp if case == "expected" else t.inp95) * pi + (t.out if case == "expected" else t.out95) * po)
    return tot

lines = ["# Cost estimate: Haiku swap (offline, no API calls)", "",
         f"Source tokens: `{RUN}/raw/calls.jsonl` (actual usage per call; setup-2 includes both tool rounds). Prices: `configs/prices.yaml` (batch = 50%).",
         f"Judge: gpt-5-nano batch, {j_in:.0f} in / {j_out:.0f} out tokens per judged item (pilot actual).", "",
         "Per-call tokens used (mean / p95):", "", "| vendor | setup | n | input mean | output mean | input p95 | output p95 |", "|---|---|---|---|---|---|---|"]
for (v, s), t in tok.iterrows(): lines.append(f"| {v} | {s} | {int(t.n)} | {t.inp:.0f} | {t.out:.0f} | {t.inp95:.0f} | {t.out95:.0f} |")
res = {}
for label, N, rerun in (("mini-pilot (25 profiles = 600 calls x 3 setups per vendor)", 25, 0.0), ("main run (N = 231)", 231, 0.0)):
    for case in ("expected", "worst"):
        t, calls = table(N, case, rerun=0.0 if case == "expected" else 0.05)
        res[(label, case)] = t
    lines += ["", f"## {label}", "", f"Calls per vendor per setup: {calls}. Worst = p95 tokens x1.25, Google promo price doubled, judge x3, +5% reruns.", "",
              "| line | expected | worst |", "|---|---|---|"]
    tot = {"expected": 0, "worst": 0}
    for v in list(MODELS) + ["judge"]:
        for s in (["all"] if v == "judge" else (1, 2, 3)):
            e, w = res[(label, "expected")][v][s], res[(label, "worst")][v][s]; tot["expected"] += e; tot["worst"] += w
            name = f"{v} {MODELS.get(v, 'gpt-5-nano')} setup {s}" if v != "judge" else "judge (gpt-5-nano)"
            lines.append(f"| {name} | ${e:.2f} | ${w:.2f} |")
    if N == 231:
        se, sw = smoke("expected"), smoke("worst"); tot["expected"] += se; tot["worst"] += sw
        lines.append(f"| smoke test (36 calls = 12/vendor, setup 3, non-batch) | ${se:.2f} | ${sw:.2f} |")
    for v in MODELS:
        lines.append(f"| subtotal {v} | ${sum(res[(label,'expected')][v].values()):.2f} | ${sum(res[(label,'worst')][v].values()):.2f} |")
    lines.append(f"| **total** | **${tot['expected']:.2f}** | **${tot['worst']:.2f}** |")
    if N == 231: lines.append(f"| remaining budget ${REMAINING:.2f} -> left | ${REMAINING - tot['expected']:.2f} | ${REMAINING - tot['worst']:.2f} |")
# Sonnet comparison for the main run
sv = 0
for s in (1, 2, 3):
    t = tok.loc[("anthropic", s)]; pi, po = price("anthropic", "claude-sonnet-5"); sv += 231 * 24 * (t.inp * pi + t.out * po)
lines += ["", f"Reference: the same main run with claude-sonnet-5 would cost ${sv:.2f} for Anthropic alone (expected)."]
open("docs/cost_haiku_swap.md", "w").write("\n".join(lines) + "\n"); print("\n".join(lines))
