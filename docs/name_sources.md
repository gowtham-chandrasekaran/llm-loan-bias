# Name-source candidates vs Reviewer conditions (2026-10-12)
Conditions: (1) first names scored on their own with a recognition measure comparable to Crabtree's forced-choice share; (2) pairs from one source; (3) perceived class measured.

| Source | (1) first-name-alone, comparable recognition | (2) single-source pairs (10/group at threshold) | (3) perceived class | Status |
|---|---|---|---|---|
| Crabtree et al. 2023, doi:10.7910/DVN/LP4EAR | No: full first+last names. Forced-choice share (the reference measure) | No: BM 1, WF 1 distinct first names at >= 0.70 | Yes: perceived income and education (ordinal) | active |
| Elder & Hayes 2023, doi:10.7910/DVN/47CZDX (CC0) | Yes, first names alone. But the measure is a 1-5 Likert rating of each race separately, not a forced-choice share. Under rule 15.6 it is used with its own thresholds, so 0.70 does not apply | Yes under 15.6: WF 134, WM 102, BF 14, BM 5 | Yes: upper/middle/working class ratings, but for 336 of 1,000 names only | **integrated (15.6)** |
| Vaseiezadeh, Campbell, Gaddis & Onwuachi-Willig 2025 "Beyond the Name", OSF https://osf.io/xfmnh/ (wba.csv, sha256 8d29a857...) | Yes: forced-choice 7-category race item, first names alone, about 89 raters per name. The most comparable to Crabtree | Nearly: at >= 0.70, WF 10, WM 10, BF 9, BM 10 | Partly: perceived income (3 bands) and education, no "class" item | candidate. No license stated on OSF; the paper is not yet located |
| Gaddis 2017, Sociological Science 4:469 | Yes: forced-choice, first name alone and with surnames | Unknown: per-name rates only in figures; no name-level data file on OSF (sddv6 is empty) | No perceived class. Mother's education (objective) instead | rejected (no data) |
| Rosenman, Olivella & Imai 2023, doi:10.7910/DVN/SGKW0K | No: self-reported voter-file P(race given name), not perception | n/a | No | rejected |

Code: `src/llmbias/name_sources.py` (registry plus loaders; candidates are refused unless approved). `name_source` is carried through variants, call records and the GLMMs.
