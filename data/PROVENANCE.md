# Data provenance

All raw files live in `data/raw/` (git-ignored; FHFA file is ~90 MB). Re-create with `scripts/download_data.sh`, which ends with `sha256sum -c data/CHECKSUMS.sha256`.
Downloaded 2026-10-12 (America/New_York, EDT) by the Developer agent on the shared box.

| File | Source URL | Downloaded (EDT) | Data rows | SHA-256 |
|---|---|---|---|---|
| hmda_2024_DE_actions1_3.csv | https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?states=DE&years=2024&actions_taken=1,3 (301-redirects to files.ffiec.cfpb.gov/data-browser/datasets/2024/filtered-queries/...) | 12:58 | 32,906 | 8eae415ee42e4382ece76fb0f02be4bfff4d650e923e33b039283d69ad83b240 |
| hmda_2024_MD_actions1_3.csv | https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?states=MD&years=2024&actions_taken=1,3 | 13:08 | 157,721 | 10c81a59d2956d4b6500ab33c66870cd2828215834cf204138f30b04a350b91f |
| hmda_2024_PA_actions1_3.csv | https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?states=PA&years=2024&actions_taken=1,3 | 13:08 | 328,267 | fbe2cf6cebcc43aee5cd8801406b935280ebefc73422db192b61af9b0c575d9f |
| zcta_tract_rel_2020.txt | https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_tract20_natl.txt (Census 2020 ZCTA–tract relationship) | 12:58 | 171,480 | 6a25d8c3fff4cf612c4d2dccc2c0cd6cb5bc99b807ff3d5d107a2e9b9d68dde0 |
| fhfa_hpi_at_tract.csv | https://www.fhfa.gov/hpi/download/annual/hpi_at_tract.csv (FHFA annual tract HPI, experimental) | 12:59 | 2,179,042 | 7675da97c942f6ab2949aa58a733675a681ca69cb28cbb192cf84ef5ee1502cf |
| crabtree_dataverse_files.zip | https://zenodo.org/records/7741926 (Crabtree & Kim 2022, doi:10.5281/zenodo.7741926; md5 7bded5139d736c9571539ebd9ae7887d matches Zenodo) | 12:59 | 600 names / 44,170 ratings | 18f6c3457843ee07893563189426c01025cba1dc7f6dccd892fa59cf5c5589f0 |
| census_2010_surnames.zip → Names_2010Census.csv | https://www2.census.gov/topics/genealogy/2010surnames/names.zip | 12:58 | 162,254 | 117c41cb4668727b7627b2845b6df3f83eb2a22a1813f42c0ff4bdcab86de135 |

HMDA year: 2024 Snapshot/public data via FFIEC Data Browser (filtered query: actions_taken 1 = originated, 3 = denied). The Data Browser serves the latest published dataset, so a later re-download could differ if CFPB republishes; the checksum detects that.

## Pre-data design change (2026-10-12, before any model run)
- Scope was DE only (32,906 rows). DE yielded only 3 matched and 5 placebo tract pairs, so on Parent/Reviewer instruction MD and PA 2024 were added with identical filters. Result at data-gate-v1: 85 matched / 283 placebo pairs.
- data-gate-v2 (2026-10-12, amendment 15.3 + Reviewer review): pairs with MSA code 99999 (non-metro) dropped; placebo minority-share gap capped at 10 pts. Result: **84 matched pairs (PA 42, MD 39, DE 3) and 256 placebo pairs (PA 211, MD 41, DE 4)**, all within one state and one real MSA. Each profile now draws its ZIP pairs from its own HMDA state.

## Citations for proxy sources
- Names: Crabtree, C., Kim, J. Y., Gaddis, S. M., Holbein, J. B., Guage, C., & Marx, W. W. (2023). Validated names for experimental studies on race and ethnicity. *Scientific Data* 10 (2023). https://doi.org/10.1038/s41597-023-01947-0. Data: https://doi.org/10.7910/DVN/LP4EAR and Zenodo mirror above.
- Tract demographics and income: HMDA LAR tract fields (tract_minority_population_percent, tract_to_msa_income_percentage), which come from FFIEC census files.
- House-price trend: FHFA tract-level annual HPI (methodology: Bogin, Doerner & Larson, FHFA Working Paper 16-01; citation not yet re-verified). The index is experimental; 2019→2024 growth is used.
- ZIP: Census 2020 ZCTA (a ZIP approximation). Each tract is assigned to the ZCTA with the largest land overlap.
- Reg B reasons: 12 CFR 1002 App. C Form C-1 https://www.consumerfinance.gov/rules-policy/regulations/1002/c/ ; HMDA denial_reason codes https://ffiec.cfpb.gov/documentation/publications/loan-level-datasets/lar-data-fields (both retrieved 2026-10-12).

## data-gate-v3 additions (2026-10-12, amendments 15.5/15.6)
- Elder & Hayes 2023 name ratings: copied from /workspace/research/eh (originally Harvard Dataverse doi:10.7910/DVN/47CZDX, CC0) into data/raw/eh/.
  nameratings.tsv (comma-separated, 1038 names) sha256 59c5e598e27acc8bff95c4b5355e33f1749f0ffa896b9181a4d7c3c5307cda66;
  traits.tsv sha256 f753a9fa269729c173d48db35fd853df5d1f5072e03f4e3ce6545d9d31c3164f.
- SSA baby names via CRAN babynames 1.0.1 (https://cloud.r-project.org/src/contrib/babynames_1.0.1.tar.gz, retrieved 2026-10-12, sha256 70d8841a8dcd6e05ca9bd4935404352661c418342aa3f40bf8926366a96997bd; SSA 1880-2017). ssa.gov returned 403 (Access Denied) to our egress. Aggregated to data/raw/ssa_name_decade.csv for era matching.
- Candidate (not integrated): Vaseiezadeh et al. wba.csv, https://osf.io/download/7yn9d/, sha256 8d29a85788c47a57045bb358d9791e8ecf8d0f63dad2d7f6f43bf35de30b9d5c.
