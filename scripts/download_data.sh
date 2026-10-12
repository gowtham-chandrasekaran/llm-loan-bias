#!/usr/bin/env bash
# Re-download all raw public inputs and verify checksums (see data/PROVENANCE.md).
set -euo pipefail
cd "$(dirname "$0")/../data/raw"
for s in DE MD PA; do curl -sSL --compressed -o hmda_2024_${s}_actions1_3.csv "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?states=${s}&years=2024&actions_taken=1,3"; done
curl -sSL -o zcta_tract_rel_2020.txt "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_tract20_natl.txt"
curl -sSL -o census_2010_surnames.zip "https://www2.census.gov/topics/genealogy/2010surnames/names.zip" && unzip -o -q census_2010_surnames.zip
curl -sSL -o fhfa_hpi_at_tract.csv "https://www.fhfa.gov/hpi/download/annual/hpi_at_tract.csv"
curl -sSL -o crabtree_dataverse_files.zip "https://zenodo.org/records/7741926/files/dataverse_files.zip?download=1" && mkdir -p crabtree && unzip -o -q crabtree_dataverse_files.zip -d crabtree
sha256sum -c ../CHECKSUMS.sha256
