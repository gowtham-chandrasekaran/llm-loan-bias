# Held files (not published)

Prepared Oct 20, 2026 for the `publish-2026-10-20` branch; updated the same day, and again for `publish-retest` (15.9.1 probe and setup-2 re-test). These files exist but are **held out of the public repo until the 15.8.5 human labeling closes**, so that nothing published can unblind the labelers. The sha256 values let anyone check later that the files published then are the files that existed now.

Paths are relative to the project workspace on the research machine (`llm-loan-bias/` is this repo; `research/` and `scratch/` sit next to it). Sizes are in bytes.

**Withheld hashes.** For held files under 1 KB the content is short and structured enough that a published hash could be checked against guesses, so the hash is withheld here and recorded privately with the project owner.

## Logged amendments and memos held until labeling closes

Amendments 15.8.5 and 15.9 (and the 15.9 erratum) are **logged** (see `amendment_log.md`), but their text is held until labeling closes. The sha256 values below match the amendment log.

| Path | Size | sha256 | Reason |
|---|---:|---|---|
| `research/memos/draft_15.8.5_v2.md` | 15,895 | `34b9a94f1ac0cd14e8c76541f5891bab9e67934fc0761e4cfcce7ec7a560452b` | Amendment 15.8.5, **logged** (amendment log sha256 matches). held until 15.8.5 labeling closes; describes the labeling design (labeler identity / item composition) |
| `research/memos/draft_15.9.md` | 77,313 | `8c30a41f3243f133ccbca9dba4e4e22e9b249530e96cff58d14298b7543e241b` | Amendment 15.9, **logged** (amendment log sha256 matches). held until 15.8.5 labeling closes; describes the labeling design (labeler identity / item composition) |
| `research/memos/planted_check_rulings_15.9.md` | 21,849 | `843fcabd8b392da40e24678e4bc51045fef4d069d42a3382c2f451301274ea17` | 15.9 erratum / planted-check rulings, **logged** (amendment log sha256 matches). held until 15.8.5 labeling closes; describes the labeling design (labeler identity / item composition) |
| `research/memos/detector_gap.md` | 15,623 | `b45a309637a478a207b1fc4bdaf40b090d742632f5d48fd9a44b910754626248` | M4 detector gap memo (not an amendment). held until 15.8.5 labeling closes; describes the labeling design (labeler identity / item composition) |

## Run folders held for labeling blindness

| Path | Size | sha256 | Reason |
|---|---:|---|---|
| `llm-loan-bias/runs/20261018T124607-e5e8b45a-1a973e-batch/calls.jsonl` | 38,338,482 | `468dd74669e46580c26d34feb8e4139ebec00d03df596a522a153787c5c1d89a` | held until 15.8.5 labeling closes; contains variant/pair_type |
| `llm-loan-bias/runs/20261018T124607-e5e8b45a-1a973e-batch/batch_state.json` | 45,713,692 | `5e82cad17400fe07a84ce237d4b7b490290ae141cc1fb897f68f9d1e6fc9606a` | held until 15.8.5 labeling closes; contains variant/pair_type (batch request bodies) |
| `llm-loan-bias/runs/20261018T124607-e5e8b45a-1a973e-batch/judge_batch_state.json` | 28,322,153 | `4525c001910079aab41c15f59c4ae6f271314b3ecaddb6bbdb865354c155fd5f` | held until 15.8.5 labeling closes; contains variant/pair_type (judge request bodies) |
| `llm-loan-bias/runs/20261018T124607-e5e8b45a-1a973e-batch/judgments.jsonl` | 7,210,600 | `8c256ccd38651ff1392a09c635b7fa23e326da33b243efeace9d23fd540cc675` | held until 15.8.5 labeling closes; joins to calls.jsonl (variant/pair_type) by call_id/custom_id |
| `llm-loan-bias/runs/20261018T124607-e5e8b45a-1a973e-batch/raw/batch_responses.jsonl` | 56,107,724 | `f625cf6f04663949ac42f49e09897973d4dfea5ac15d87dd4fa1ea9531e0d58d` | held until 15.8.5 labeling closes; joins to calls.jsonl (variant/pair_type) by call_id/custom_id |
| `llm-loan-bias/runs/20261018T124607-e5e8b45a-1a973e-batch/run_meta.json` | 3,349 | `14ccd6debd6cbe424feecea3ceb438eac6ca97543b81c6df6b844b874faa8f7c` | held until 15.8.5 labeling closes; contains variant/pair_type (variant list, config) |
| `llm-loan-bias/runs/20261018T124607-e5e8b45a-1a973e-batch/spend_ledger.jsonl` | 2,363,197 | `91b1a0426007acd9cc18204aa98d30266bc452d4e77b36a17cee6fcd8d9b486d` | held with its run folder until 15.8.5 labeling closes (operational; no letters) |
| `research/smoke_runs/20261017T213809-918ac8fb-64e476/calls.jsonl` | 251,307 | `642119eef783d41c810ab1b7c9086a5d3f102805829ce44b2147ddda4b71dd1c` | held until 15.8.5 labeling closes; contains variant/pair_type |
| `research/smoke_runs/20261017T213809-918ac8fb-64e476/raw/responses.jsonl` | 41,430 | `16f76939b10a216a62ef83c896b23714f56f7260a9a32168e6a1cae9eec2f807` | held until 15.8.5 labeling closes; joins to calls.jsonl (variant/pair_type) by call_id/custom_id |
| `research/smoke_runs/20261017T213809-918ac8fb-64e476/run_meta.json` | 1,995 | `6b1c9b64b6eef2c3664772c1d7fc9e939018a4152503da7e92eb3c0357c79bfb` | held until 15.8.5 labeling closes; contains variant/pair_type (variant list, config) |
| `research/smoke_runs/20261017T213809-918ac8fb-64e476/code_support.json` | 747 | hash withheld (low entropy); recorded privately | held with its run folder until 15.8.5 labeling closes (operational; no letters) |
| `research/smoke_runs/20261017T213809-918ac8fb-64e476/spend_ledger.jsonl` | 5,620 | `5fa7a1d16ce7686bb3690e9bb446a94b207d49ab4eef7ffa8380c87cb5225945` | held with its run folder until 15.8.5 labeling closes (operational; no letters) |
| `research/smoke_runs/smoke2-20261017T225139/smoke2.yaml` | 874 | hash withheld (low entropy); recorded privately | held until 15.8.5 labeling closes; contains variant/pair_type (variant list, config) |
| `research/smoke_runs/smoke2-20261017T225139/20261017T225139-9c4f9094-aa232a/calls.jsonl` | 169,208 | `8fc4e23a7cf3801918ae825055521dee534eb5fc81000ae3cc8374152e74d7de` | held until 15.8.5 labeling closes; contains variant/pair_type |
| `research/smoke_runs/smoke2-20261017T225139/20261017T225139-9c4f9094-aa232a/raw/responses.jsonl` | 35,414 | `ee94f233bc721c90a2ee93f75f3d91af53d4903a8298ca222f33059b762ceb15` | held until 15.8.5 labeling closes; joins to calls.jsonl (variant/pair_type) by call_id/custom_id |
| `research/smoke_runs/smoke2-20261017T225139/20261017T225139-9c4f9094-aa232a/run_meta.json` | 2,015 | `98f38c03466b1a060df1aee6116589f266419da7f1be50c0a49f37e9857b51e9` | held until 15.8.5 labeling closes; contains variant/pair_type (variant list, config) |
| `research/smoke_runs/smoke2-20261017T225139/20261017T225139-9c4f9094-aa232a/smoke2_summary.json` | 4,044 | `d99e11f5467f439b3f882c0462ce1a9eb88145ee0815bb417bc702f60f1206a2` | held with its run folder until 15.8.5 labeling closes (operational; no letters) |
| `research/smoke_runs/smoke2-20261017T225139/20261017T225139-9c4f9094-aa232a/spend_ledger.jsonl` | 3,777 | `db7f9710fb5224443f1f56cc8c2e7b326627ea750d6269dd9c45ffc7acbbf874` | held with its run folder until 15.8.5 labeling closes (operational; no letters) |

Total: 18 files, 178,575,628 bytes. `raw/batch_responses.jsonl` of the core mini-pilot is 56.1 MB (over GitHub's 50 MB warning size; under the 100 MB hard limit) and will need Git LFS or compression when it is published.

## 15.9.1 probe and setup-2 re-test (added Oct 20, 2026 for `publish-retest`)

The live 15.9.1 probe (Oct 20, 18:13 ET) and the live setup-2 re-test (Oct 20, 19:02-19:29 ET) are held like the other
post-pilot run folders: the re-test files list the variant next to each letter, and the Reviewer's hold list allows no
run folder except pilot-v1 until 15.8.5 labeling closes. The free mock dry-run folders of the probe
(`runs/probe_s2/*-dryrun`) are local only and not listed (like the earlier mock dry runs). Plain-language operational
summary: [s2_retest_summary.md](s2_retest_summary.md).

Config files derived from the public configs (`*.yaml` below) are treated as low entropy whatever their size, because
their content can be rebuilt from `configs/`; their hashes, like those of files under 1 KB, are recorded privately.

| Path | Size | sha256 | Reason |
|---|---:|---|---|
| `llm-loan-bias/runs/probe_s2/PROBE_EXCLUDED_FROM_ANALYSIS` | 106 | hash withheld (low entropy); recorded privately | probe marker; held with the probe folder (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/PROBE_EXCLUDED_FROM_ANALYSIS` | 106 | hash withheld (low entropy); recorded privately | probe marker; held with the probe folder (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/batch/20261020T181343-03f17f09-7fb656-batch/batch_state.json` | 60,963 | `8321d6f51f2f90ef2a255108fbab3f50b0356d44ad26d2414726f9b80f1708e7` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/batch/20261020T181343-03f17f09-7fb656-batch/calls.jsonl` | 43,640 | `a37dddd5d5ed630560649713a6e50fc025852700708a3622af9d9c931c192f4d` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/batch/20261020T181343-03f17f09-7fb656-batch/raw/batch_responses.jsonl` | 32,733 | `d3b02d23bcf26249e402ad060c011eb31fe43b2811a7eaee7a005fa6aa2f4ddf` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/batch/20261020T181343-03f17f09-7fb656-batch/run_meta.json` | 3,284 | `17faf4590239dc8f739c9ef9a26cab88a63f04ef757dffcc4d06e9d9f32835a2` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/batch/20261020T181343-03f17f09-7fb656-batch/spend_ledger.jsonl` | 2,816 | `8031b975b6d7d34c36c97be124afba110d8efdf73a0c2d5b620d19b70ac2349a` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/batch_config.yaml` | 1,024 | hash withheld (low entropy); recorded privately | probe config as run; held with the probe folder (Reviewer hold list) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/probe_report.json` | 9,401 | `65fae01fd5553b8592cfb06860b322a807caa6a351125319c9d178f1835a91d6` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/sync/20261020T181308-0c554c7d-672ba6/calls.jsonl` | 44,348 | `886bba0fa3ce6333bf2932543c2857bd9976331d9d3fe87d25d2b2979d189339` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/sync/20261020T181308-0c554c7d-672ba6/raw/attempts_journal.jsonl` | 11,576 | `c67f75712432f703463f65f63e00abac835760f48e491a2c1ad153df66a4a4cc` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/sync/20261020T181308-0c554c7d-672ba6/raw/responses.jsonl` | 8,561 | `65ad8e3604ca30f48e5ffb8a7ec141b180b9808df55cc44ebd2860380de529d8` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/sync/20261020T181308-0c554c7d-672ba6/run_meta.json` | 2,637 | `cf7c711ccff2f88a2b36b996927dee12126e353d3b6a0562b6df957b0321bc02` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/sync/20261020T181308-0c554c7d-672ba6/spend_ledger.jsonl` | 1,230 | `d42cc07d663cc6fd6528acb6dabf617fd9dba1cfa9b3632fd74ef015f85fc42e` | 15.9.1 probe output (2 profiles, base variant, setup 2); held with the 15.9.1 run folders (Reviewer hold list: no run folders except pilot-v1) |
| `llm-loan-bias/runs/probe_s2/20261020T181308-684ca6-live/sync_config.yaml` | 1,025 | hash withheld (low entropy); recorded privately | probe config as run; held with the probe folder (Reviewer hold list) |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/batch_state.json` | 17,504,806 | `97dbb4a51fc78792a0e3c8b934cd5a2d457fdf11b463287a29b33f15e2a73f7b` | held until 15.8.5 labeling closes; contains variant/pair_type (request bodies) |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/calls.jsonl` | 13,741,974 | `3a8892970c812ab4f15d6e9ad7ad91b864efaf8d181b10eb062c6317d877c349` | held until 15.8.5 labeling closes; contains variant/pair_type |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/judge_batch_state.json` | 9,501,494 | `87f2552ae439ab70a8b957ab578637c5fef578c9b6dbc3bb598000d3ef4dbabb` | held until 15.8.5 labeling closes; contains variant/pair_type (request bodies) |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/judgments.jsonl` | 2,287,132 | `9b3606e682b10889604f8063e8789c776a16d4cd6a79dc1eb603fae57f29b2aa` | held until 15.8.5 labeling closes; joins to calls.jsonl (variant/pair_type) by call_id/custom_id |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/raw/batch_responses.jsonl` | 21,747,516 | `163e56031ae1e89be19d1296d609e366d743ed30f42b1b17c8789375241a6fd1` | held until 15.8.5 labeling closes; joins to calls.jsonl (variant/pair_type) by call_id/custom_id |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/retest_report.json` | 3,399 | `b282d36590bf769d3f4991d91b1a3d7abe92988b120a5babf5a8e2c7b298403c` | held with its run folder until 15.8.5 labeling closes (operational; no letters) |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/run_meta.json` | 5,552 | `4dc217355a72f4f3c77f0ff6fa0bd094f3ccc4eec018db1439f3cdd274798050` | held until 15.8.5 labeling closes; contains variant/pair_type (variant list, config) |
| `llm-loan-bias/runs/20261020T190210-ee9b9f61-a646ff-batch/spend_ledger.jsonl` | 1,513,486 | `67f083d3c18b0a3a5d63af078cc51f56f7010c4af827d8c653cb0a596e23e316` | held with its run folder until 15.8.5 labeling closes (operational; no letters) |
| `llm-loan-bias/runs/retest_config_20261020T190210-1b7618.yaml` | 1,259 | hash withheld (low entropy); recorded privately | re-test config as launched (copy of configs/minipilot_s2_retest.yaml); held with the re-test run folder |
| `llm-loan-bias/runs/s2_retest_live_20261020T190208.log` | 2,289 | `11e2f20ea0ad8ecd62d257119e7c963b47b2070133bfa7afc2d2dc4aa5cd8129` | re-test launch log (cost estimate + run summary; no keys); held with the re-test run folder |
| `llm-loan-bias/runs/cumulative_spend_ledger.jsonl` | 6,553,295 | `df20d4e228bdf49a5d06eed1941d0a5b476dc8147d3e2b20ac79d34ed25b0447` | cumulative spend ledger (operational; not tracked); held, Reviewer hold list. Grows with every paid run, so this hash is a snapshot after the re-test |

Total: 26 files, 73,085,652 bytes. Largest: the re-test `raw/batch_responses.jsonl` (21.7 MB, under GitHub's 50 MB
warning size). Raw Gemini `thought_signature` values appear only in held files (`raw/batch_responses.jsonl`,
`batch_state.json`); published code and records-side logging keep only their sha256 and length.

New docs held back for this update: none. (`docs/research/s2_retest_summary.md` is operational only and is published.)

## Labeling-design material (M1b)

| Path | Size | sha256 | Reason |
|---|---:|---|---|
| `research/memos/hedging_constructed_candidates.md` | 20,011 | `6b2bfed6e2767fcbecd0ddabf4cba237612b12e70435ae6b953d1fcf12d8dffb` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/memos/hedging_constructed_candidates.orig.md` | 16,475 | `d5325587095c0f330cfe4c605da392625cb87dfffdcb9f14daf000d2c8bc5a22` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/memos/hedging_constructed_candidates.v1c883b49.md` | 19,425 | `1c883b49617eab1d2650e7bdf6bc6d5152ac41501a3cb1b790ddbe5c446b9c47` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/memos/hedging_constructed_candidates.v95be9017.md` | 19,511 | `95be9017121e27f1dca6fbb3c7d2af6c84d984e60a076fc84da84271a6d894e6` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/memos/hedging_constructed_candidates.vb20bb8d8.md` | 19,547 | `b20bb8d8f9ef99ae6cc8882a0a4532d73145707e5973b46fe7517ddd81c029f8` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/review/constructed_key.md` | 9,433 | `9da3b91b3d3be4ad31b87120c900f6cae4035788126001614f02bfb6f53d503a` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/review/constructed_9b_check.md` | 4,887 | `63300f064181b4fe45e768d28453f625ebe2f8dd00beac58238de066ca90a0a7` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/review/c9b/all.txt` | 19,547 | `604dd1afe8c7240c0fbd09b8ae2aea8c2bfaba2b42b746ce0a93d029764de25f` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `research/review/c9b/constructed_item_ids.json` | 240 | hash withheld (low entropy); recorded privately | held until 15.8.5 labeling closes; M1b labeling-design material |
| `scratch/best48.json` | 19,566 | `da17e578a6d5d9db7d99e78c5d97e8494ec50dd9cdf3239160ec17f6945b3a0b` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `scratch/build_cands.py` | 17,031 | `b59ea168aecd6394cad686915854325628fdd0f8827eef669723f271ed365d17` | held until 15.8.5 labeling closes; M1b labeling-design material |
| `llm-loan-bias/runs/20261012T143812-ff54324d-4745d6-batch/rejudge_hedging_v3_state.json` | 1,079,079 | `b6ec45b27b8f902f65b4664bd7a182ecc09422a54c958a10ff9f1ae4cce9cdaf` | held until 15.8.5 labeling closes; M1b labeling-design material |

## Searched for and not found (Oct 20, 2026)

- **15.8.5 labeling workbooks:** none exist yet. They will be held too.
- **Seed-15846 M4 audit order:** not drawn yet.
- The research docs still published in `docs/research/` were grepped for labeler names, labeler-specific file names, labeling-design material, held item IDs, and seed 15846: none found.

Blind keys are never published (they live in a private store outside the repo; see `.gitignore`).
