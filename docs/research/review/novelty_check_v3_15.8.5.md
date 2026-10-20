# Novelty Check v3 (amendment 15.8.5): "Do LLM Loan Explanations Hide Biased Decisions?"
Date: 2026-10-17 (ET). Scope: only NEW work since v1/v2 (about Aug–Oct 2026), checked against v1/v2 citation lists.

## Verdict: NEW PARTIAL OVERLAP (minor). v2's "partially overlapping" stands. Nothing changes the amendment.

## Searches run
- Web: LLM lending/mortgage fairness with paired names/ZIP/HMDA; LLM adverse-action / Reg B reason codes / explanation faithfulness; LLM tool/score override bias; SSRN generative-AI adverse action; FAccT/arXiv adverse-action letter disparities.
- X (search_posts_all, since 2026-09-01): two queries on LLM + lending/credit/adverse action + bias. No relevant posts came back.
- Results already in v1/v2 and not repeated here: Bowen et al.; 2605.15217; 2606.16723 (AgentFairBench); 2609.09048; 2608.17715; 2602.18895; 2510.25701; 2609.37223 (CredWise). CFPB Circulars 2022-03 and 2023-03 are the existing legal standard. I found no new CFPB or Fed publication.

## New papers found
1. **The Stated Reason: Counterfactual Testing of Self-Explanation Faithfulness in Automated Credit Adjudication.** Zenodo preprint, 2026-09-10. https://doi.org/10.5281/zenodo.22689598 . The author was not shown in the search result; check this on the record.
   - Method: 50 synthetic credit applications, gpt-oss-20b/120b. Each model names the field that decided the case, then each field is neutralized to the cohort median. Neutralizing the named field flips the decision in only 26.5% and 17.4% of cases (faithfulness index 0.34–0.39).
   - Overlap: partial with the faithfulness framing behind M1a and pretext. It has no demographic or proxy pairing, no Reg B codes, no tool setup and no frontier API models. Cite it as concurrent evidence that LLM credit self-explanations are unfaithful.
2. **Accurate Ensembles, Fragile Narratives: Multi-Scale Stacking and a Fidelity Audit of LLM-Generated Explanations for Credit Risk.** G. R. Pratama, K.-K. Tseng (HIT Shenzhen). arXiv 2608.08126 (Aug 2026). https://arxiv.org/abs/2608.08126
   - Method: SHAP/LIME drivers go to phi-2, which writes a rationale. A membership/direction/coverage audit finds sign inversions and an omitted top driver in one worked case. The paper does not estimate a rate.
   - Overlap: weak to partial with Setup 3 / M1a (do LLM-written reasons match the scorer's drivers?). It is a single case with no protected attributes, no paired proxies and no Reg B codes. It supports adding a reason-code fidelity measure, as v2 already recommended.
3. **Social Group Bias in AI Finance.** T. R. Cook (FRB Kansas City), S. Kazinnik (Stanford). SSRN 5287153, first posted Jun 2025, **revised 13 Sep 2026**. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5287153
   - Method: four open-weight LLMs in mortgage underwriting. Irrelevant demographic information shifts interest rates and decision margins even when the final decision does not change.
   - Overlap: partial with H1/M3 (paired demographic cues in mortgage decisions). It uses open-weight models only and has no explanations, reason codes, tool override or letters. This is not new work, but the revision is new, and v1/v2 do not cite it. Add it to related work.

Not counted (not close): OverAct arXiv 2610.01508 (tool over-authorization, privacy, not fairness); SSRN 7202460 (Gerber, governance framework, no LLMs); SSRN 6742919 (Jain, XGBoost proxy audit, no LLMs).

## Overlap with the novel core
None of the new items studies proxy-conditional Reg B reason-code selection or fidelity with a deterministic decider (M1a, Setup 3), proxy-dependent tool override (M2), proxy mentions (M4), or name/ZIP pairing with frontier API models. The core named in v2 is intact.

## Implications for amendment 15.8.5
- Moving M1b to exploratory: unaffected. No new work makes hedging tone confirmatory-critical.
- 6-test Holm family: unaffected.
- M4 automated proxy-span detector with an AI precision audit: unaffected. No new competing measurement method.
- Optional, outside the amendment: cite items 1–3 in related work. Items 1–2 reinforce v2's advice to report reason-code *fidelity* against the scorer's top-k drivers. If fidelity is not already a pre-registered measure, consider it in a later amendment. It is not required for this one.

## Limitations
Search index coverage is partial. X search returned noise only. I did not read full text for item 1 or item 3, so their summaries come from abstracts. I read item 2 in full on arXiv HTML. FAccT/NeurIPS 2026 proceedings were not searchable by venue.
