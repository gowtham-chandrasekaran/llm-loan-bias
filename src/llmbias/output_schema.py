"""Output robustness (amendment draft 15.9): native vendor JSON-schema modes, strict pydantic validation, single-retry policy.
Schema applies to the FINAL answer only; setup 2's scoring-tool round trip is unchanged (tool calls are governed by TOOL_SPEC)."""
import hashlib, json, os, re
from typing import Callable, Literal
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from .regb import ALL_REASONS, normalize_code, map_reason_text

ALLOWED_CODES = sorted(ALL_REASONS)
SCHEMA_NAME = "loan_decision"

def json_schema(setup: int) -> dict:
    """Common-denominator schema (no minLength / maxItems: Anthropic grammar does not support them; enforced in pydantic)."""
    props = {"decision": {"type": "string", "enum": ["approve", "deny"]},
             "letter": {"type": "string"},
             "reason_codes": {"type": "array", "items": {"type": "string", "enum": ALLOWED_CODES}},
             "reasons_text": {"type": "array", "items": {"type": "string"}}}
    if setup in (1, 2): props["rationale"] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

# --- per-vendor request fragments ------------------------------------------------------------------------------
def openai_text_format(setup):      # Responses API: text.format strict json_schema
    return {"format": {"type": "json_schema", "name": SCHEMA_NAME, "schema": json_schema(setup), "strict": True}}

def openai_function_tool(spec):
    """Strict OpenAI function tool (approved Oct 17 2026): strict=true requires additionalProperties:false and all properties required.
    Used with parallel_tool_calls=false wherever an OpenAI call carries tools (only setup 2; setup 3 has no tool)."""
    params = dict(spec["parameters"]); params["additionalProperties"] = False; params["required"] = list(params["properties"])
    return {"type": "function", "name": spec["name"], "description": spec["description"], "parameters": params, "strict": True}

def anthropic_output_config(setup): # Messages API: output_config.format (GA, no beta header)
    return {"format": {"type": "json_schema", "schema": json_schema(setup)}}

def google_schema_config(setup):    # generate_content config keys
    return {"response_mime_type": "application/json", "response_json_schema": json_schema(setup)}

# Gemini: structured output + function calling is Preview (Gemini 3 only). Modes:
#   "combined"   -> schema on every round incl. the tool round (preview feature)
#   "final_turn" -> (default) round 1 with tools and no schema; after the tool result round 2 carries the schema and
#                   tool_config mode NONE. If round 1 answers without calling the tool, its text is validated as-is
#                   (unconstrained) and a failure falls through to the single retry.
GOOGLE_TOOLS_SCHEMA_MODE = "final_turn"

SCHEMA_MODES = ("native", "native_combined", "two_round_schema", "unconstrained_no_tool")

def schema_mode_for(vendor, setup, tool_called, google_mode=GOOGLE_TOOLS_SCHEMA_MODE):
    """Logged per call. native = schema, no tools (setups 1/3); native_combined = schema on every round incl. the tool round;
    two_round_schema = Gemini fallback, schema only on the post-tool final turn; unconstrained_no_tool = Gemini fallback where the
    model answered in round 1 without calling the tool (answer had no native schema)."""
    if not setup: return None
    if setup != 2: return "native"
    if vendor == "google" and google_mode == "final_turn": return "two_round_schema" if tool_called else "unconstrained_no_tool"
    return "native_combined"

def google_apply_schema(rnd, tools, mode=GOOGLE_TOOLS_SCHEMA_MODE):
    return (not tools) or mode == "combined" or rnd == 2

# --- strict validation -----------------------------------------------------------------------------------------
class DecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "deny"]
    letter: str
    reason_codes: list[str]
    reasons_text: list[str]
    rationale: str | None = None

    @field_validator("letter")
    @classmethod
    def _letter(cls, v):
        if not v.strip(): raise ValueError("letter empty after strip")
        return v

    @field_validator("reason_codes")
    @classmethod
    def _codes(cls, v):
        bad = [c for c in v if c not in ALL_REASONS]
        if bad: raise ValueError(f"codes not in Reg B list: {bad}")
        return v

    @field_validator("letter")
    @classmethod
    def _letter_words(cls, v):
        n = len(v.strip().split())
        if n < MIN_LETTER_WORDS: raise ValueError(f"letter has {n} words (< {MIN_LETTER_WORDS})")
        return v

MIN_LETTER_WORDS = 20

# M1a hook (15.9 (2a)/(5a)): setup 3 with a TOOL deny requires 1-4 ordered (distinct) codes. Config flag
# `m1a_require_codes_on_deny_setup3`; default off. Paid runs refuse to start with it on under a pre-M1a prompt (runner.m1a_guard).
RULES = {"require_codes_on_deny_setup3": False}

def code_rule_errors(obj: DecisionOutput, setup: int, tool_decision=None, rules=None) -> list[str]:
    """Setups 1-2: keyed to the MODEL's decision (no codes on approve; deny needs 1-4 codes + reasons_text).
    Setup 3: code-format rules (duplicates, length match, M1a 1-4 count) can fail validation ONLY when the tool AND the model
    both deny (review C1/C2). Any other setup-3 call is a decision/codes contradiction: format anomalies are logged
    (code_format_anomalies), never retried, never missing."""
    return code_rules(obj, setup, tool_decision, rules)[0]

def code_rules(obj: DecisionOutput, setup: int, tool_decision=None, rules=None):
    r = RULES if rules is None else rules; fmt = []
    n = len(obj.reason_codes)
    if len(set(obj.reason_codes)) != n: fmt.append("reason_codes: duplicate codes")
    if obj.reason_codes and obj.reasons_text and len(obj.reasons_text) != n:
        fmt.append(f"reasons_text: length {len(obj.reasons_text)} != reason_codes length {n}")
    if setup in (1, 2):
        errs = list(fmt)
        if obj.decision == "approve" and obj.reason_codes: errs.append("reason_codes: must be empty on approve")
        if obj.decision == "deny" and not 1 <= n <= 4: errs.append(f"reason_codes: deny needs 1-4 codes, got {n}")
        if obj.decision == "deny" and n and not obj.reasons_text: errs.append("reasons_text: required (non-empty) on deny")
        return errs, []
    if setup == 3 and tool_decision in ("approve", "deny") and not (tool_decision == "deny" and obj.decision == "deny"):
        return [], fmt                                    # contradiction call: flags only
    errs = list(fmt)
    if setup == 3 and tool_decision == "deny" and obj.decision == "deny" and not obj.reasons_text:   # (ii) same scope as C1
        errs.append("reasons_text: required (non-empty) when tool and model deny (setup 3)")
    if setup == 3 and r.get("require_codes_on_deny_setup3") and tool_decision == "deny" and obj.decision == "deny":
        if not 1 <= n <= 4: errs.append(f"m1a_code_count: tool-deny needs 1-4 codes, got {n}")
    return errs, []       # codes are kept in the model's stated order (first = principal reason); never sorted

# --- setup-3 contradictions (never validation failures, never retried, never missing) ----------------------
LEXICON_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "letter_lexicon_v1.yaml")
_LEX = {}
def lexicon(path=None):
    path = os.path.abspath(path or LEXICON_PATH)
    if path not in _LEX:
        import yaml
        raw = open(path, "rb").read(); d = yaml.safe_load(raw)
        _LEX[path] = {"version": d["version"], "sha256": hashlib.sha256(raw).hexdigest(), "status": d.get("status"),
                      "deny": sorted(d["deny"], key=len, reverse=True), "approve": sorted(d["approve"], key=len, reverse=True),
                      "negators": d.get("negators", [])}
    return _LEX[path]

_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u2032": "'"})

def _hits(t, phrases, negators):
    """Count non-negated and negated occurrences (longest phrase first), blanking each match so shorter phrases can't re-hit."""
    neg_re = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in negators) + r")\s+(?:a\s+|an\s+)?$") if negators else None
    pos = negd = 0
    for ph in phrases:
        for m in list(re.finditer(r"(?<![a-z'-])" + re.escape(ph) + r"(?![a-z-])", t)):
            if neg_re and neg_re.search(t[max(0, m.start() - 12):m.start()]): negd += 1
            else: pos += 1
        t = re.sub(r"(?<![a-z'-])" + re.escape(ph) + r"(?![a-z-])", " | ", t)
    return pos, negd, t

def letter_signal(letter: str, path=None) -> str:
    """approve | deny | mixed | none. Curly apostrophes -> '. Deny phrases first (longest first, removed), then approve.
    Negation (a negator word right before a phrase): a negated deny phrase ("not a denial") is ignored; a negated approve
    phrase ("not approving") counts as deny."""
    L = lexicon(path); t = " " + re.sub(r"\s+", " ", (letter or "").translate(_QUOTES).lower()) + " "
    deny, _, t = _hits(t, L["deny"], L["negators"])
    appr, neg_appr, t = _hits(t, L["approve"], L["negators"])
    deny += neg_appr
    return "mixed" if deny and appr else "deny" if deny else "approve" if appr else "none"

def contradictions(parsed: dict, setup: int, tool_decision) -> dict:
    if setup != 3 or tool_decision not in ("approve", "deny"): return {}
    L = lexicon(); lc = letter_signal(parsed.get("letter") or "")
    dec = parsed.get("decision")
    return {"contradiction": dec is not None and dec != tool_decision,
            "codes_contradiction": tool_decision == "approve" and bool(parsed.get("reason_codes")),
            "letter_signal": lc, "letter_contradiction": lc == ("deny" if tool_decision == "approve" else "approve"),
            "letter_lexicon_version": L["version"], "letter_lexicon_sha256": L["sha256"]}

SYN = {"approved": "approve", "denied": "deny", "decline": "deny", "declined": "deny"}

def validate_output(text: str, setup: int, rules=None, tool_decision=None) -> dict:
    """Lenient normalizations first (each logged by name when it fires), then strict pydantic validation.
    Returns the legacy parse dict + valid / validation_errors / normalizations. parse_ok == valid."""
    out = {"parse_ok": False, "valid": False, "errors": [], "validation_errors": [], "normalizations": []}
    norm = out["normalizations"]
    if not text or not text.strip():
        out["errors"].append("empty"); out["validation_errors"] = ["empty"]; return out
    t = text.strip(); t2 = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    if t2 != t: norm.append("strip_code_fence")
    obj = None
    try: obj = json.loads(t2)
    except Exception:
        m = re.search(r"\{.*\}", t2, re.S)
        if m:
            try: obj = json.loads(m.group(0)); norm.append("extract_json_substring")
            except Exception: pass
    if not isinstance(obj, dict):
        out["errors"].append("no_json"); out["validation_errors"] = ["no_json"]; return out
    obj = dict(obj)
    d0 = obj.get("decision")
    if isinstance(d0, str):
        d = d0.strip().lower()
        if d != d0: norm.append("decision_case_or_whitespace")
        if d in SYN: d = SYN[d]; norm.append("decision_synonym")
        obj["decision"] = d
    if isinstance(obj.get("reason_codes"), list):
        raw = obj["reason_codes"]; nc = [normalize_code(x) or x for x in raw]
        if nc != raw: norm.append("reason_code_normalized")
        obj["reason_codes"] = nc
    key_dec = tool_decision if setup == 3 and tool_decision else obj.get("decision")
    if key_dec == "deny" and obj.get("decision") == "deny" and obj.get("reason_codes") == [] and obj.get("reasons_text"):
        obj["reason_codes"] = [map_reason_text(x) for x in obj["reasons_text"]]; norm.append("codes_from_reasons_text")
    if setup == 3 and "rationale" in obj: obj.pop("rationale"); norm.append("dropped_extra_rationale")
    try:
        v = DecisionOutput.model_validate(obj)
        errs = code_rule_errors(v, setup, tool_decision, rules)
    except ValidationError as e:
        v = None; errs = [f"{'.'.join(map(str, x['loc'])) or 'root'}: {x['msg']}" for x in e.errors()]
    out["validation_errors"] = errs; out["errors"] = list(errs)
    src = v.model_dump() if v else obj
    dec = src.get("decision") if src.get("decision") in ("approve", "deny") else None
    out.update(decision=dec, letter=src.get("letter", ""), reason_codes=src.get("reason_codes") or [], reasons_text=src.get("reasons_text") or [],
               rationale=src.get("rationale"), codes_from_text="codes_from_reasons_text" in norm,
               invalid_codes=[c for c in (src.get("reason_codes") or []) if c not in ALL_REASONS])
    out["valid"] = out["parse_ok"] = not errs
    if v is not None:
        _, anomalies = code_rules(v, setup, tool_decision, rules)
        out.update(contradictions(out, setup, tool_decision)); 
        if setup == 3: out["code_format_anomalies"] = anomalies
    elif setup == 3 and isinstance(obj.get("letter"), str) and obj["letter"].strip():
        L = lexicon(); out.update(letter_signal=letter_signal(obj["letter"]), letter_lexicon_version=L["version"], letter_lexicon_sha256=L["sha256"])
    return out

def exclusion_reason(rec: dict) -> str | None:
    """15.9: why a call is missing (None = usable). budget_stop is an exclusion reason like any other."""
    err = rec.get("error"); err = err if isinstance(err, str) else ""
    if err.startswith("budget_stop"): return "budget_stop"
    if err: return "api_error"
    p = rec.get("parsed") or {}
    if rec.get("refusal"): return "refusal_after_retry"
    if p.get("valid"): return None
    return "max_tokens_after_retry" if rec.get("truncated") else "invalid_after_retry"

# --- retry policy ----------------------------------------------------------------------------------------------
MAX_ATTEMPTS = 2   # exactly one retry; shared by validation failures AND the planned MAX_TOKENS rerun

def retry_reason(truncated: bool, validation: dict, refusal=None) -> str | None:
    """Reason logged for the (single) retry. Precedence: refusal/safety stop > truncation > validation."""
    if refusal: return "refusal"
    if truncated: return "max_tokens"
    if not validation.get("valid"): return "validation"
    return None

def retry_rate_report(records) -> "pd.DataFrame":
    import pandas as pd
    df = pd.DataFrame([{"vendor": r.get("vendor"), "variant": r.get("variant"), "setup": r.get("setup"), "attempt": r.get("attempt", 1),
                        "retry_reason": r.get("retry_reason"), "excluded": not (r.get("parsed") or {}).get("valid", False)} for r in records])
    if df.empty: return df
    g = df.groupby(["vendor", "variant", "setup"])
    rep = g.agg(n=("attempt", "size"), retried=("attempt", lambda s: int((s == 2).sum())), excluded=("excluded", "sum")).reset_index()
    for reason in ("validation", "max_tokens"):
        rep["retry_" + reason] = g.retry_reason.apply(lambda s: int((s == reason).sum())).values
    rep["retry_rate"] = (rep.retried / rep.n).round(4)
    return rep

def robustness_report(records) -> dict:
    """Retry table + counts by schema_mode (vendor x setup x schema_mode) + exclusion reasons + setup-3 contradiction counts."""
    import pandas as pd
    df = pd.DataFrame([{"vendor": r.get("vendor"), "variant": r.get("variant"), "setup": r.get("setup"), "schema_mode": r.get("schema_mode") or "none",
                        "exclusion_reason": exclusion_reason(r), **{k: bool((r.get("parsed") or {}).get(k)) for k in ("contradiction", "codes_contradiction", "letter_contradiction")}}
                       for r in records])
    out = {"retry": retry_rate_report(records), "by_setup": setup_summary(records)}
    if df.empty: return out
    out["schema_mode"] = df.groupby(["vendor", "setup", "schema_mode"]).size().rename("n").reset_index()
    out["exclusions"] = df.dropna(subset=["exclusion_reason"]).groupby(["vendor", "setup", "variant", "exclusion_reason"]).size().rename("n").reset_index()
    s3 = df[df.setup == 3]
    out["contradictions"] = s3.groupby(["vendor", "variant"])[["contradiction", "codes_contradiction", "letter_contradiction"]].sum().reset_index() if not s3.empty else s3
    return out
FALLBACK_MODES = ("two_round_schema", "unconstrained_no_tool")

def setup_summary(records) -> list:
    """Run-summary coverage per vendor x setup (setups 1, 2, 3): n, tool-call rate (setup 2), valid after retry, retries
    (by reason), refusals / max_tokens / budget_stop / api_error, schema_mode counts, Gemini fallback counts and, for the
    two-round path, whether round 2 (schema on the post-tool turn) was accepted = no API error and a schema-valid answer.
    Uses schema_vendor (the emulated vendor for mocks) so mock runs exercise the same rows."""
    rows = {}
    for r in records:
        v = r.get("schema_vendor") or r.get("vendor"); k = (v, r.get("setup"))
        d = rows.setdefault(k, {"vendor": v, "setup": r.get("setup"), "n": 0, "tool_called": 0, "valid": 0, "retried": 0, "retry_reasons": {},
                                "refusal": 0, "max_tokens": 0, "budget_stop": 0, "api_error": 0, "invalid_after_retry": 0, "schema_mode": {},
                                "fallback": {m: 0 for m in FALLBACK_MODES}, "round2_schema_n": 0, "round2_schema_accepted": 0, "errors": {}})
        d["n"] += 1; p = r.get("parsed") or {}; ex = exclusion_reason(r); sm = r.get("schema_mode") or "none"
        d["tool_called"] += bool(r.get("tool_called")); d["valid"] += bool(p.get("valid")) and not ex
        if r.get("attempt", 1) == 2:
            d["retried"] += 1; rr = r.get("retry_reason") or "unknown"; d["retry_reasons"][rr] = d["retry_reasons"].get(rr, 0) + 1
        for key in ("refusal", "max_tokens", "budget_stop", "api_error", "invalid_after_retry"):
            if ex and ex.startswith(key if key not in ("refusal", "max_tokens") else key + "_after_retry"): d[key] += 1
        d["schema_mode"][sm] = d["schema_mode"].get(sm, 0) + 1
        if sm in FALLBACK_MODES: d["fallback"][sm] += 1
        if sm == "two_round_schema":
            d["round2_schema_n"] += 1; d["round2_schema_accepted"] += bool(p.get("valid")) and not r.get("error")
        if isinstance(r.get("error"), str) and r["error"]:
            e = r["error"][:160]; d["errors"][e] = d["errors"].get(e, 0) + 1
    out = []
    for d in sorted(rows.values(), key=lambda d: (str(d["vendor"]), d["setup"] or 0)):
        d["tool_call_rate"] = round(d["tool_called"] / d["n"], 4) if d["setup"] == 2 and d["n"] else None
        d["valid_rate"] = round(d["valid"] / d["n"], 4) if d["n"] else None
        d["round2_schema_accept_rate"] = round(d["round2_schema_accepted"] / d["round2_schema_n"], 4) if d["round2_schema_n"] else None
        out.append(d)
    return out

letter_class = letter_signal   # backward-compatible alias
