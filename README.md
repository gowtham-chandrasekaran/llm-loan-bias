# llm-loan-bias

Do the explanations that AI models write for loan decisions hide biased decisions? This repo is an API-only fair-lending
audit: matched pairs of synthetic mortgage applicants (built from public HMDA data) that differ only in name or ZIP code
are sent to three commercial models, and the decisions and denial letters are compared.

**Start here: [docs/START_HERE.md](docs/START_HERE.md)** (plain-language overview, findings so far, status, how to
reproduce the numbers offline, file map). Commands: [docs/RUNNING.md](docs/RUNNING.md).

Status (Oct 20, 2026): pilot and mini-pilot stage. Nothing here is a confirmatory result. The main run has not started.
