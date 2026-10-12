"""Deterministic scoring tool (no AI). Points-based scorecard; returns score, decision, top reason codes.
The scorecard is author-defined (not a real lender's). Weights are fixed and documented here."""
from dataclasses import dataclass

THRESHOLD = 60.0  # approve if score >= THRESHOLD; see calibrate_threshold() and docs/design.md

def _credit_pts(cs):  # 0..40
    return max(0.0, min(40.0, (cs - 580) / (760 - 580) * 40))
def _dti_pts(dti):  # 0..25 ; <=36 full, >=50 zero
    return max(0.0, min(25.0, (50 - dti) / (50 - 36) * 25))
def _ltv_pts(ltv):  # 0..15 ; <=80 full, >=97 zero
    return max(0.0, min(15.0, (97 - ltv) / (97 - 80) * 15))
def _reserve_pts(m):  # 0..10 ; months of PITI reserves, >=6 full
    return max(0.0, min(10.0, m / 6 * 10))
def _tenure_pts(y):  # 0..10 ; >=5 years full
    return max(0.0, min(10.0, y / 5 * 10))

COMPONENTS = [  # (field, fn, max, Reg B code)
    ("credit_score", _credit_pts, 40, "X02"),
    ("dti_pct", _dti_pts, 25, "C09"),
    ("ltv_pct", _ltv_pts, 15, "C23"),
    ("reserves_months", _reserve_pts, 10, "X01"),
    ("employment_years", _tenure_pts, 10, "C07"),
]

@dataclass
class ScoreResult:
    score: float
    decision: str
    reason_codes: list
    components: dict

def score_application(app: dict, threshold: float = THRESHOLD, k: int = 2) -> ScoreResult:
    comps, shortfall = {}, []
    for f, fn, mx, code in COMPONENTS:
        p = fn(float(app[f])); comps[f] = round(p, 3)
        shortfall.append((mx - p, code))
    s = round(sum(comps.values()), 3)
    dec = "approve" if s >= threshold else "deny"
    shortfall.sort(key=lambda x: (-x[0], x[1]))
    codes = [c for gap, c in shortfall if gap > 0][:k]
    return ScoreResult(s, dec, codes, comps)

TOOL_SPEC = {
    "name": "score_application",
    "description": "Bank's deterministic underwriting scorecard. Returns score (0-100), decision (approve if score >= threshold), and up to 2 principal adverse-action reason codes (Reg B).",
    "parameters": {"type": "object", "properties": {
        "credit_score": {"type": "number"}, "dti_pct": {"type": "number"}, "ltv_pct": {"type": "number"},
        "reserves_months": {"type": "number"}, "employment_years": {"type": "number"}},
        "required": ["credit_score", "dti_pct", "ltv_pct", "reserves_months", "employment_years"]},
}
