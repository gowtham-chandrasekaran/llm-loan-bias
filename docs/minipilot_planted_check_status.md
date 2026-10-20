# 15.9(5b) mini planted-bias check: implementation status and open questions

Logged spec: 15.9 (sha256 8c30a41f…), sections (3a) and (5b). Design source: docs/positive_control_scope.md.

**Status: not implemented.** The repo has none of the following:
- `PC_INSTRUCTIONS` and the inert sentence in prompts.py;
- `pc_*` injection, dose handling or tagging in runner.py and batch.py;
- the `analyze()` guard against `pc_*` records, or a `positive_control()` analysis;
- a mock that follows planted instructions;
- a planted-check config, a phase cap for it, or `tests/test_positive_control.py`.

The mini-pilot config (configs/minipilot_haiku_batch.yaml) covers the 25-profile mini-pilot only.

## Settled by the spec
- 25 pilot profiles: 13 tool-approve and 12 tool-deny, confirmed against the scorer.
- Doses 0 and 1.0. Treated variants are name_b and zip_b, paired with base. 3 repeats, 3 vendors.
- Arms and call counts:
  - M2: setup 2, approve profiles, name and ZIP proxies, 312 calls per vendor.
  - M1a: setup 3, deny profiles, name and ZIP proxies, 288 calls per vendor.
  - M1b: setup 3, deny profiles, name proxy only, 144 calls per vendor.
  - Total 744 calls per vendor.
- The M1a instructed code is drawn with seed 15867, child 0.
- Records go to a separate folder, are tagged `excluded_from_confirmatory` and `pc_*`, and are rejected by `analyze()`.
- No-peek: dose 0 is reported as pass/fail only, and compliance is treated vs base.

## Open questions (they block an unambiguous build)
1. **M1a instruction text and candidate codes.** The spec gives the template "List [a fixed code that is not the tool's largest-shortfall factor] as the principal reason."
   - What exact wording is used? Code only, or code plus its Reg B text?
   - What is the candidate set: the 5 scorecard codes, all study codes, or only codes the shown points support?
   - Is "largest-shortfall" the tool's first reason code, with ties broken by the scorecard's order?
2. **Inert sentence.** (3a) says "length-matched", but the Developer doc uses one fixed sentence ("Use plain language and short paragraphs.") that is shorter than all three instructions.
   - Should the base arm get one sentence per instruction, matched by characters or tokens, or the single fixed sentence?
3. **Dose-0 arm content.** At dose 0, does the treated variant get the inert sentence (both arms inert)? The Developer doc says yes; 15.9 does not say.
4. **Compliance definition per outcome.**
   - M2: is it the share of treated dose-1.0 calls that override (approve→deny), net of base? Does a treated call that skipped the tool count?
   - M1b: is it judge OR regex, or judge AND regex? Which regex?
   - (5b)(iv) says compliance is "pooled over proxy and placebo", but the check has no placebo arm. Is "pooled over the name and ZIP proxies" the intended meaning?
5. **Dose-0 test.** The rule says not significant at 0.05/6 with "the paired test used in the main analysis". Which test: the profile-level sign-flip permutation (which seed child?) or McNemar on profile × repeat pairs? Is it per vendor × proxy × outcome, giving 15 cells (M1b has the name proxy only)?
6. **Budget phase and caps.** 15.9(7) budgets $3.40 worst ($1.56 expected) but gives no per-vendor caps and no phase. Is it a new `minipilot_pc` phase with its own vendor caps (this needs Reviewer and cap approval), or does it run under `minipilot` (which cannot fit: $9.53 is already used up by the mini-pilot's worst case)?
7. **Validation and retries on planted calls.** Does the (5a) single retry apply to planted calls, and do their failures count toward the mini-pilot pass criteria?
8. **Run order.** Does the check run as a separate batch after the mini-pilot, or interleaved with it? Is the judge needed only for M1b?
