# Running the harness (commands)

Moved from the top-level README on Oct 20, 2026. Plain-language overview: [START_HERE.md](START_HERE.md). See also `design.md`, `../data/PROVENANCE.md`, `pricing.md`, `../reports/data_report.md`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .
scripts/download_data.sh                      # raw public data + checksum verification
python -m llmbias.gate                        # build data + data-quality gate + report
python -m pytest -q
python -m llmbias.runner --config configs/pilot_mock.yaml    # free mock dry run -> runs/<run_id>/
python -m llmbias.judge --run runs/<run_id> && python -m llmbias.analysis --run runs/<run_id>
python -m llmbias.labels export --run runs/<run_id> --n 120
python -m llmbias.runner --config configs/pilot.yaml --estimate-only [--batch]
# Paid runs need an explicit --allow-paid flag and keys in a git-ignored .env / environment.
```

## v2: batch pilot (pilot of record)
```bash
sudo apt-get install r-base-core r-cran-lme4         # confirmatory glmer (R/glmer_fit.R)
python -m llmbias.batch --config configs/pilot_batch_mock.yaml            # free mock batch dry run
python -m llmbias.batch --config configs/pilot_batch.yaml --plan-only     # projected cost + pre-seeded setup-2 trim
python -m llmbias.batch --config configs/pilot_batch.yaml --allow-paid    # REAL pilot (needs approval)
python -m llmbias.batch --resume runs/<run_id> --allow-paid               # resume after interruption
```

## 15.9.1 probe and setup-2 re-test (commands)
```bash
python scripts/run_s2_probe.py                      # free mock dry run of the probe (--live = real calls, needs approval)
python scripts/run_s2_retest.py --estimate-only     # per-vendor cost estimate vs the minipilot_s2_retest caps
python scripts/run_s2_retest.py                     # free mock dry run of the re-test (+ replacement demo on a copy of the core)
python scripts/run_s2_retest.py --live --probe-report runs/probe_s2/<probe_id>/probe_report.json   # REAL (needs approval)
python -m llmbias.s2_replace --core runs/<core> --retest runs/<retest>   # 15.9.1 item 5 replacement
```

### 15.9.1 setup-2 re-test: cap basis (`minipilot_s2_retest`)

Phase caps (`budget.PHASE_CAPS["minipilot_s2_retest"]`): total $3.88; OpenAI $1.24 (incl. judge), Anthropic $0.39, Google $2.25.
The basis per vendor is max(pilot basis, S2_CORE_P95): the larger of the pilot token basis (`configs/token_basis.yaml`) and
the core mini-pilot's setup-2 p95 actuals (`budget.S2_CORE_P95`, run 20261018T124607-e5e8b45a-1a973e-batch). It uses the
shared cap basis: p95 tokens x1.25, Google price x2, +5% reruns, judge x3 billed to OpenAI, cent ceiling. The design is
600 setup-2 calls per vendor and 1,800 judged letters.

| vendor | pilot basis | core actuals (S2_CORE_P95) | used | cap |
|---|---|---|---|---|
| OpenAI (incl. judge) | $1.0749 | $1.2361 | core actuals | $1.24 |
| Anthropic | $0.2879 | $0.3857 | core actuals | $0.39 |
| Google | $2.2497 | $2.0781 | pilot basis | $2.25 |

A re-test run (sync or batch, `phase: minipilot_s2_retest`) writes this to `run_meta.cap_basis` (`budget.retest_cap_basis()`,
with `matches_phase_caps`). Exact values are in run_meta: OpenAI core actuals come to $1.2362 unrounded; the $1.2361 above is
the sum of the rounded parts. The cap is the same either way.

Setup-2 compliance (Reviewer conditions on 91baba3, `docs/amendment_15_9_1_forced_tool.md`):
- Tool rule on every attempt, with no retry. Round-2 tool requests count toward `multiple_tool_calls`, and bad forced-call
  arguments are `tool_args_invalid`.
- API and transport errors are missing data; `run_meta.error_counts` counts them.
- At a batch compliance stop, in-flight batches are cancelled and drained. Their worst case is booked as a keyed reserve
  until each batch is collected (`run_meta.inflight_worst_at_stop_usd`).
