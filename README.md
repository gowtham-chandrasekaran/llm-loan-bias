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
