"""Tiny live checks (2026-10-12, authorized: one thinking-off call + one tool call per vendor). Prints no secrets.
Uses the project's adapters, so a pass verifies the real code path. Writes results to docs/live_checks.json."""
import json, sys, time
sys.path.insert(0, "src")
from llmbias.clients import make_client
from llmbias.scoring import TOOL_SPEC, score_application

MODELS = [("openai", "gpt-5.4-mini", "none"), ("anthropic", "claude-sonnet-5", "none"), ("google", "gemini-3.8-flash", "none")]
TOOL_PROMPT = ("Use the score_application tool for this applicant: credit_score 700, dti_pct 42, ltv_pct 90, reserves_months 3, "
               "employment_years 4. Then reply ONLY JSON {\"decision\": \"approve\"|\"deny\", \"score\": <number>}.")
def tool_fn(a):
    r = score_application(a); return {"score": r.score, "decision": r.decision, "reason_codes": r.reason_codes}

out = []
for which in sys.argv[1:] or ["thinking", "tool"]:
    for v, m, eff in MODELS:
        c = make_client(v, m, eff, max_output_tokens=200)
        t0 = time.time()
        try:
            if which == "thinking":
                r = c.complete("Reply with one word.", "Say OK.")
            else:
                r = c.complete("You are an underwriter.", TOOL_PROMPT, TOOL_SPEC, tool_fn)
            rec = dict(check=which, vendor=v, model=m, effort=eff, ok=True, text=r.text[:200], model_version=r.model_version,
                       input_tokens=r.input_tokens, output_tokens=r.output_tokens, reasoning_tokens=r.reasoning_tokens,
                       reasoning_present=bool(r.reasoning), tool_calls=r.tool_calls)
        except Exception as e:
            rec = dict(check=which, vendor=v, model=m, effort=eff, ok=False, error=f"{type(e).__name__}: {str(e)[:400]}")
        rec["latency_s"] = round(time.time() - t0, 2); out.append(rec); print(json.dumps(rec, default=str))
json.dump(out, open(f"docs/live_checks_{'_'.join(sys.argv[1:]) or 'all'}.json", "w"), indent=2, default=str)
