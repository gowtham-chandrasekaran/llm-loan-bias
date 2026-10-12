# llm-loan-bias

Audit harness for "Do LLM loan explanations hide biased decisions?". See `docs/design.md`, `data/PROVENANCE.md`, `docs/pricing.md`, `reports/data_report.md`.

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
