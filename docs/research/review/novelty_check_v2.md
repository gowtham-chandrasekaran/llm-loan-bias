# Novelty Check v2: "Do LLM Loan Explanations Hide Biased Decisions?"
Reviewer (independent), Oct 12 2026, 7:30 PM ET. Inputs read: research_plan.md (§15–15.8.2), pilot memo (draft 15.9 at §"Recommended amendments"), paper.tex v0.14, novelty_check.md (v1). Read-only except this file.

## 0. Verdict
**Partially overlapping.** It is not duplicative, but the original framing ("H4: explanations differ by proxy on fixed decisions") is no longer novel as a *phenomenon*. Veldanda (2605.08671) has already shown fixed-decision explanation disparities in tone, hedging and sentiment, including in credit. What stays novel is the combination of: (a) a regulation-anchored outcome (choice of Reg B reason codes when a deterministic tool decides, M1a); (b) override of a deterministic scorer conditioned on the proxy (M2); (c) placebo-controlled, human-validated measurement on realistic HMDA mortgage profiles. Point (c) matters most, because Veldanda has no placebo and no human validation, and our pilot shows placebo pairs flip as often as proxy pairs. I found no paper that does M1a, M2 or Setup 3 with a demographic counterfactual.

## 1. Verification depth of sources
| Source | What I retrieved | Depth |
|---|---|---|
| Veldanda, *Explanation Fairness in LLMs*, arXiv 2605.08671 | arxiv.org/abs/2605.08671 (full HTML body) | **Full text** |
| Van den Bulck et al., *Who Gets the Better Story? Fairness in XAI Narratives for Credit Scoring*, PMLR v350 (ECAF'26) | PDF from mlresearch/v350 GitHub (bulck26a.pdf) | **Full text** (extended abstract, ~5 pp) |
| Bowen, Price, Stein & Yang (SSRN 4812158; AEA 2026) | lukestein.com PDF (127 KB text) | **Full text** (grep-level read of models and design) |
| Tripathy & Buckmann, arXiv 2605.15217 | arXiv API abstract | Abstract (v1 had the full abs page) |
| Geng et al., arXiv 2602.18895 | arXiv API abstract + search excerpts of HTML | Abstract + excerpts |
| Chen et al., arXiv 2505.05410 | arXiv API abstract | Abstract |
| Karvonen & Marks, arXiv 2506.10922 | arXiv API abstract | Abstract |
| Morla et al., AgentFairBench, arXiv 2606.16723 | arXiv API abstract (v1 had the abs page) | Abstract |
| Vohra & Ravikiran, arXiv 2609.09048 | arXiv API abstract | Abstract |
| Amiri-Margavi et al., arXiv 2602.02932 | search snippet (and cited in Veldanda) | Abstract |
| FiSCo, arXiv 2506.19028 | search snippet | Abstract |
| CEC "Do Fair Models Reason Fairly?", arXiv 2605.12701 | search snippet | Abstract |
| *Communicating Credit Risk with LLMs*, arXiv 2608.17715 | search snippet | Excerpt only |
| *Interpreting LLMs as Credit Risk Classifiers*, arXiv 2510.25701 | search snippet | Abstract |
| *Self-Blinding and Counterfactual Self-Simulation*, arXiv 2601.14553 | search synthesis only | Snippet only; verify before citing |
| *Social Group Bias in AI Finance*, arXiv 2506.17490 | search abstract | Abstract |
| Matton et al., *Walk the Talk?*, arXiv 2504.14150 | search abstract | Abstract |
| Lacuna "placebo-controlled counterfactual fairness audits" page | search result | It is a hypothesis page, not a paper. Do not cite as prior work. |

## 2. What the two flagged papers actually do (verified)

### Veldanda 2605.08671 (full text)
- **Models:** GPT-4.1, Claude Sonnet (claude-sonnet-4-6), Llama 3.3 70B, GPT-OSS 120B, Qwen3 32B. Temperature 0, 512 max tokens, a **single sample** per prompt.
- **Domain:** 4 domains (hiring, medical, credit, legal) with 20 templates each, adapted from BBQ, WinoBias and HolisticBias. The credit templates are short vignettes with **no HMDA realism and no reason codes**. 400 pairs total (80 templates × 5 models).
- **Decision fixed?** **Yes.** The prompt states the decision and the model only explains it. This is exactly our Setup-3 logic, except that the decision is stated by the prompt rather than produced by a real scoring tool.
- **Measures:** word count; elaboration (embedding clusters); VADER sentiment; a lexicon Hedging Density Score; FKGL, TTR and domain-term density; EFP (embedding distance to a contrastive explanation for the opposite decision). It uses **no reason-code or factor-selection measure** and no faithfulness check against real decision drivers.
- **Statistics:** one-sample Wilcoxon on **absolute** pair differences |m(A)−m(B)|. That test is significant by construction under any sampling noise, so "all p<10^-62" largely measures stochastic or template variability. Signed race differences were **not significant** (race: verbosity −2.2, sentiment +0.01, all n.s.). Only some gender and religion signed effects reached p<.05.
- **Tool/override:** none. **Placebo or noise floor:** none (no same-group name swaps, no repeated samples). **Human validation:** none; the paper lists it as future work.
- **Implication:** Veldanda claims the H4/M1b phenomenon, but its evidence is weak, and for race specifically the signed effects are null. That is a genuine opening for a stricter test.

### Van den Bulck et al., PMLR v350 (full text)
- **Models:** six LLMs (Claude Sonnet 4, DeepSeek-Chat, Gemini 3.1 Flash Lite, Grok-4-1-Fast, Mistral Large, GPT-4o) at T=0.
- **Domain:** German Credit. A random forest decides (protected attributes excluded), giving 34 denied instances. TreeSHAP top-3 features go into the prompt and the LLM writes a narrative, so the **decision is fixed by a model**, the same structure as our Setup 3. 204 narratives.
- **Design:** **observational, not counterfactual.** Group comparisons (sex, age) are across *different applicants*. There is no matched-pair swap, and the authors list injecting or removing protected attributes for the same instances as future work. Race could not be analysed (all 34 were foreign workers).
- **Measures:** feature coverage (which non-SHAP features are mentioned), rank agreement with SHAP (faithfulness), and protected-attribute mentions. Faithfulness is extracted by 3 LLMs with majority vote. There are **no tone or hedging measures and no Reg B codes.**
- **Tool/override:** none (the model decides; the LLM cannot override). **Placebo:** none. **Human validation:** a single annotator on a subset. No multiple-comparison correction; the authors call it exploratory.
- **Implication:** it is a partial precedent for M1a and M4 *in spirit* (which factors a fixed-decision letter cites, by group), but it is not causal. Our matched-pair design is a direct answer to its stated future work.

### Re-verification of the v1 closest works (no change in substance)
- **Bowen et al. (full text):** GPT-4 Turbo baseline plus Claude 3 Sonnet/Opus, Llama 3 8B/70B, and GPT-3.5/4 variants. Explicit race, plus **name and city** implicit signals, on HMDA-derived loans. Measures approval and rate. It does not study explanations, tools or placebos (it uses regression controls instead). **H1 is done.**
- **2605.15217:** open-weight models, name-matched mortgage pairs, **no output-level bias**, and latent bias that can be steered. Supports a null or weak H1 for aligned models. No explanations or tools.
- **2602.18895:** an LLM explains given SHAP or coefficient rankings from LR/XGB (LendingClub). It copies supplied rankings well and infers them poorly. **No demographic manipulation.** This is the Setup-3 precedent with no bias angle.
- **2505.05410:** the hint-verbalization metric (disclosure given a flip). **The method for H2 (M4) is done**; the domain is not.
- **2506.10922:** in hiring, realistic context induces bias that is invisible in the CoT. This is the closest prior result for **H2** (and partly H3) in a decision domain other than lending.
- **2606.16723 AgentFairBench:** name-coded matched sets in lending, hiring and triage. The "tool-augmented" scaffold measures tool-invocation disparity. Mostly nulls. It stores no rationales and has no deterministic scorer to override. It shares the noise-floor argument (2.4× arity inflation) with our placebo logic.
- **New, important: 2609.09048 (Vohra & Ravikiran):** a preregistered name-only audit (40,726 requests, lending included), with **planted-disparity positive controls** and identical-content controls. Its 36 contrasts are null, and order or instrument effects match demographic ones. **The proposed planted-bias positive control and the placebo framing are already done here for decisions** (not for explanations, reason codes or overrides). It must be cited, and our design should be positioned as its explanation-level counterpart.
- **Other relevant, abstract-level:** Amiri-Margavi 2602.02932 (counterfactual hedging and sentiment disparities in career advice; GPT-4 and Llama), FiSCo 2506.19028 (claim-level counterfactual long-form fairness with inter- vs intra-group comparison, conceptually close to our placebo baseline), 2601.14553 (LLM deference to and selective override of a blinded counterfactual self-simulation tool; the closest "override" precedent I found, though it does not involve a credit scorer), 2605.12701 (CEC: same outcome, different reasoning, on HMDA; classical ML, no LLM).

## 3. Overlap table
Legend: **D** = done, **P** = partial, **N** = not done. Columns: Bowen; 2605.15217 (TB); 2602.18895 (Geng); 2505.05410 (Chen); 2506.10922 (KM); AgentFairBench (AFB); Veldanda (V); Van den Bulck (VdB); 2609.09048 (VR).

| Row | Bowen | TB | Geng | Chen | KM | AFB | V | VdB | VR |
|---|---|---|---|---|---|---|---|---|---|
| **H1** flips from proxy | **D** name and city, mortgage | **D** (null output bias) | N | N (MC hints) | **D** (hiring) | **D** lending (mostly null) | N (decision fixed) | N | **D** lending, preregistered null |
| **H2** disclosure of proxy | N | N | N | **D** method (hints) | **P** 0/63 CoTs mention race (hiring) | N (no rationales) | N | **P** counts protected-attribute mentions (observational) | N |
| **H3** pretextual reasons | N | N | P (reasons ≠ model drivers, no proxy) | N | P (implicit) | N | N | P (faithfulness gaps by group, non-causal) | N |
| **H4** narrative differs by proxy (S2/S3) | N | N | N | N | N | N | **P→D** for phenomenon (fixed decision, credit included); no tool, no placebo | **P** fixed by RF, but between-applicant | N |
| **M1a** Reg B reason-code choice, tool decides | N | N | P (feature ranking, no proxy) | N | N | N | N | **P** feature coverage by sex/age, not counterfactual, not Reg B | N |
| **M1b** hedging/discouraging tone in denial letters | N | N | N | N | N | N | **D** (lexicon HDS, VADER; unvalidated, no placebo; race signed effect n.s.) | N | N |
| **M2** proxy-dependent override of deterministic scorer | N | N | N | N | N | P (tool-invocation disparity; tool never decides) | N | N | N |
| **M4** proxy mentions | N | N | N | D (method) | P | N | N | P | N |
| **Setup 1** AI decides | **D** | **D** | N | – | **D** | **D** | N | N | **D** |
| **Setup 2** AI + scoring tool | N | N | N | N | N | **P** (tool-augmented scaffold, info tool) | N | N | N |
| **Setup 3** tool decides, AI writes letter | N | N | **P** (no demographics) | N | N | N | **P** (decision stated in prompt) | **P** (RF + SHAP, observational) | N |
| (cross-cut) placebo / noise-floor control | N (regression controls) | N | N | N | N | **P** (test-retest, arity-matched) | N | N | **D** (identical-content + planted controls) |
| (cross-cut) human-validated judge | N | N | N | N | N | N | N | P (single annotator) | ? (abstract silent) |

**Net:** H1 and Setup 1 are replications. H2 and M4 apply an existing method to a new domain. H4 and M1b are now partly claimed by Veldanda, with weak evidence. M1a, M2, Setup 2 (deterministic scorer plus override) and Setup 3 with matched counterfactual proxies are **not done** by any retrieved paper.

## 4. Evaluation of the proposed pivot
1. **Reframe as a stricter test of Veldanda-style claims: endorse, with care.** It is the right framing because Veldanda's absolute-difference test cannot separate demographic signal from sampling noise, has no placebo, uses single samples, and has no human validation. Our placebo pairs, 3 repeats, κ-validated judge and signed paired tests are the exact fixes. Frame it as "explanation-fairness claims under a noise floor", not as a takedown. Also position it against Van den Bulck (we make their observational design counterfactual) and VR 2609.09048 (we extend instrument-aware auditing from decisions to explanations). **Key insight to state explicitly:** the pilot found placebo flips ≥ proxy flips for decisions. If the same holds for letters, a null result with a validated noise floor and a positive control is itself a publishable correction to the literature.
2. **Make M2 co-primary: endorse.** It is the most novel quantity, with no prior work, and it maps cleanly onto fair-lending practice (high- and low-side override monitoring is standard compliance; see the Asurity practitioner note, web only). Caveats: the pilot override rates are vendor-driven (OpenAI 18%, Google 0%) and the Anthropic arm is incomplete. M2 must be **proxy-conditional**, estimated against placebo-conditional override rates, with vendor as a stratum and not pooled. Because the Holm family is fixed at 8 tests, this needs a formal amendment *before main-run data* (it changes the priority, not the family). Document it as 15.9-x.
3. **Planted-bias positive control: endorse, but it is not novel by itself** (VR 2609.09048 already does it for decisions). It is essential for us: without it, a null on M1a, M1b and M2 is uninterpretable. Implementation: a system prompt instructing a subtle proxy-conditional tone or override behavior at known strengths (e.g., 5, 10 and 20 pp), run through the identical blinded judge pipeline, reporting recovered vs planted effect (a detection-power curve). Plant it in the *letter* (M1a/M1b) and in the *override* (M2), not only in the decision.
4. **Current models: endorse for credibility, but only as a pre-registered additional arm.** Veldanda uses GPT-4.1 and Sonnet 4.6. Using older or mini models invites "already fixed" objections. Do not swap models mid-preregistration. Add a frontier subset under the $200 cap, and keep the pilot models as the confirmatory arm.

## 5. What would make it publishable
**ICAIF (finance-AI; most natural fit):**
- Lead with the deployment-realistic pipeline: HMDA-grounded profiles, a deterministic scorer, Reg B reason codes, and CFPB Circular 2022-03/2023-03 as the legal yardstick for "reasons must reflect factors actually scored". M1a (reason-code fidelity and disparity against the tool's true drivers) and M2 (override disparity) as headline results.
- A compliance-oriented result is valuable even if it is null: "LLM-written adverse-action letters cite reason codes that diverge from the scorer's top factors at rate X, and this divergence does / does not depend on the proxy beyond the placebo floor." Add a measure of reason-code *fidelity* (cited codes vs the tool's top-k contributing factors), not only divergence between proxy variants. This fills Geng's gap and adds the demographic dimension.
- Release the harness and the paired dataset.

**FAccT (higher bar on measurement validity and normative framing):**
- The methodological contribution must be explicit: placebo-calibrated, positive-control-validated, human-validated (κ ≥ 0.7 on M1b/M4 with ≥100 items) explanation-fairness auditing, plus a re-analysis showing how Veldanda-style |Δ| tests behave under our placebo pairs (i.e., apply their metric to our placebo pairs and show the false-positive rate). That is a strong, checkable claim.
- Report signed effects and directions, with vendor heterogeneity as a finding (the pilot already shows it).
- Discuss the harms: discouragement (Reg B §1002.4(b)), the right to specific reasons, and the distinction between ZIP and name proxies (ZIP is redlining-relevant).

**Must-fix before either venue:**
- Cite and position against Veldanda, Van den Bulck, 2609.09048, AgentFairBench, Amiri-Margavi and FiSCo. paper.tex v0.14 currently has no related-work text covering them (the regulatory paragraph is a TODO).
- Fix the control arm (pilot D3): control_dup is byte-identical, so it underestimates chance. Use placebo pairs as the primary chance baseline for every metric, including M1/M2.
- Resolve vendor-pooling (D1) and the Anthropic incompleteness, and fix the Google truncation, before any confirmatory claim.
- Read in full before citing (abstract-only here): 2609.09048, 2602.02932, 2506.19028, 2601.14553, 2608.17715.

## 6. Bottom line
Partially overlapping. Novel core: proxy-conditional **Reg B reason-code selection and fidelity with a deterministic decider (M1a, Setup 3)** and **proxy-conditional override of a scorer (M2, Setup 2)**, measured against a **placebo floor with planted positive controls and human validation**. H1, H2/M4 and M1b are replication or stricter-test contributions. The pivot is sound and improves the paper's odds.
