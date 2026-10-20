"""Descriptive, packet-only metric: reason codes a model gave that the factor points shown in its own prompt do not support.

Rule (documented in docs/amendment_15_9_output_robustness.md, "Code support"):
- Factor -> code map is the scorecard table in scoring.COMPONENTS: credit_score -> X02, dti_pct -> C09, ltv_pct -> C23,
  reserves_months -> X01, employment_years -> C07 (code descriptions in regb.py).
- A factor-mapped code is SUPPORTED when that factor scored below its maximum, i.e. the scorecard counts a shortfall
  (scoring.score_application only gives codes with gap > 0). It is UNSUPPORTED when the factor is at full points
  (e.g. X01 with reserves 10.0 / 10, which means reserves >= 6 months).
- Packet-only: points come from the model's own prompt text, never from hidden state.
  setup 3 (M1a v2): the "- <factor>: p / max" lines exactly as shown (1 dp); full = p >= max.
  setups 1-2: the applicant values shown in the prompt, converted with the scorecard functions and thresholds
  (credit full >= 760, DTI full <= 36%, LTV full <= 80%, reserves full >= 6 months, tenure full >= 5 years).
  Falls back to the logged tool_components only when the prompt is not in the record (flagged source="record").
- Codes that map to no scorecard factor (other Reg B codes) are counted as not_factor_mapped, never as unsupported.
- Descriptive only: no inference and no effect estimate. Counted over valid model-deny outputs (codes are empty on approve)."""
import re
from .scoring import COMPONENTS

FACTOR_CODE = {f: code for f, _, _, code in COMPONENTS}
CODE_FACTOR = {code: f for f, code in FACTOR_CODE.items()}
FACTOR_MAX = {f: float(mx) for f, _, mx, _ in COMPONENTS}
FACTOR_FN = {f: fn for f, fn, _, _ in COMPONENTS}
_LABELS = {"Credit score": "credit_score", "Debt-to-income ratio": "dti_pct", "Loan-to-value ratio": "ltv_pct",
           "Cash reserves": "reserves_months", "Employment history": "employment_years"}
_RAW = {"credit_score": r"Credit score \(FICO\): (\d+(?:\.\d+)?)", "dti_pct": r"Debt-to-income ratio: ([\d.]+)%",
        "ltv_pct": r"Combined loan-to-value: ([\d.]+)%", "reserves_months": r"Liquid reserves \(months of payments\): ([\d.]+)",
        "employment_years": r"Years with current employer: (\d+(?:\.\d+)?)"}

def shown_points(rec):
    """(points by factor, source) as the model saw them."""
    p = rec.get("prompt") or ""
    if rec.get("setup") == 3 and p:
        pts = {_LABELS[m.group(1)]: float(m.group(2)) for m in re.finditer(r"^- (" + "|".join(map(re.escape, _LABELS)) + r"): ([\d.]+) / \d+", p, re.M)}
        if len(pts) == len(FACTOR_CODE): return pts, "prompt_points"
    if p:
        raw = {f: re.search(rx, p) for f, rx in _RAW.items()}
        if all(raw.values()): return {f: FACTOR_FN[f](float(m.group(1))) for f, m in raw.items()}, "prompt_values"
    tc = rec.get("tool_components")
    return ({f: float(v) for f, v in tc.items()}, "record") if tc else (None, None)

def code_support(rec):
    """Per call: {supported, unsupported, not_factor_mapped, unsupported_codes, source} for the model's reason codes."""
    p = rec.get("parsed") or {}; codes = p.get("reason_codes") or []
    pts, src = shown_points(rec)
    out = {"supported": [], "unsupported": [], "not_factor_mapped": [], "source": src}
    for c in codes:
        f = CODE_FACTOR.get(c)
        if f is None: out["not_factor_mapped"].append(c); continue
        if pts is None: continue
        (out["unsupported"] if pts[f] >= FACTOR_MAX[f] else out["supported"]).append(c)
    return out

def code_support_report(records):
    """Per vendor x setup over valid model-deny outputs: deny calls, codes given, supported / unsupported / not_factor_mapped
    counts, calls with >= 1 unsupported code, and unsupported counts by code. Descriptive only."""
    rows = {}
    for r in records:
        p = r.get("parsed") or {}
        if not p.get("valid") or p.get("decision") != "deny" or r.get("error"): continue
        v = r.get("schema_vendor") or r.get("vendor"); k = (v, r.get("setup"))
        d = rows.setdefault(k, {"vendor": v, "setup": r.get("setup"), "deny_calls": 0, "codes": 0, "supported": 0, "unsupported": 0,
                                "not_factor_mapped": 0, "calls_with_unsupported": 0, "unsupported_by_code": {}, "sources": {}})
        cs = code_support(r); d["deny_calls"] += 1
        d["codes"] += len(cs["supported"]) + len(cs["unsupported"]) + len(cs["not_factor_mapped"])
        for key in ("supported", "unsupported", "not_factor_mapped"): d[key] += len(cs[key])
        d["calls_with_unsupported"] += bool(cs["unsupported"])
        for c in cs["unsupported"]: d["unsupported_by_code"][c] = d["unsupported_by_code"].get(c, 0) + 1
        d["sources"][cs["source"]] = d["sources"].get(cs["source"], 0) + 1
    return [rows[k] for k in sorted(rows, key=lambda k: (str(k[0]), k[1] or 0))]
