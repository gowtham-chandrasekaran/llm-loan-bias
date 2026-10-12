"""Reg B (12 CFR 1002, Appendix C, Form C-1) adverse-action reasons and HMDA denial_reason codes.

Sources (retrieved 2026-10-12):
- Form C-1 checklist: https://www.consumerfinance.gov/rules-policy/regulations/1002/c/
- HMDA denial_reason-1..4 codes: https://ffiec.cfpb.gov/documentation/publications/loan-level-datasets/lar-data-fields
"""
import re

REGB_REASONS = {
    "C01": "Credit application incomplete",
    "C02": "Insufficient number of credit references provided",
    "C03": "Unacceptable type of credit references provided",
    "C04": "Unable to verify credit references",
    "C05": "Temporary or irregular employment",
    "C06": "Unable to verify employment",
    "C07": "Length of employment",
    "C08": "Income insufficient for amount of credit requested",
    "C09": "Excessive obligations in relation to income",
    "C10": "Unable to verify income",
    "C11": "Length of residence",
    "C12": "Temporary residence",
    "C13": "Unable to verify residence",
    "C14": "No credit file",
    "C15": "Limited credit experience",
    "C16": "Poor credit performance with us",
    "C17": "Delinquent past or present credit obligations with others",
    "C18": "Collection action or judgment",
    "C19": "Garnishment or attachment",
    "C20": "Foreclosure or repossession",
    "C21": "Bankruptcy",
    "C22": "Number of recent inquiries on credit bureau report",
    "C23": "Value or type of collateral not sufficient",
    "C24": "Other",
}
# Appendix C para 3 allows creditors to add common reasons; we add one (flagged as an addition, not Form C-1 text).
REGB_ADDED = {"X01": "Insufficient funds for down payment, closing costs, or reserves (added per App. C para 3)",
              "X02": "Credit score below our minimum requirement (added per App. C para 3)"}
ALL_REASONS = {**REGB_REASONS, **REGB_ADDED}

HMDA_DENIAL = {
    "1": "Debt-to-income ratio", "2": "Employment history", "3": "Credit history", "4": "Collateral",
    "5": "Insufficient cash (downpayment, closing costs)", "6": "Unverifiable information",
    "7": "Credit application incomplete", "8": "Mortgage insurance denied", "9": "Other", "10": "Not applicable",
}
# Coarse crosswalk Reg B code -> HMDA denial reason (author-defined mapping; documented in docs/design.md)
REGB_TO_HMDA = {
    "C01": "7", "C02": "3", "C03": "3", "C04": "6", "C05": "2", "C06": "6", "C07": "2", "C08": "1",
    "C09": "1", "C10": "6", "C11": "9", "C12": "9", "C13": "6", "C14": "3", "C15": "3", "C16": "3",
    "C17": "3", "C18": "3", "C19": "3", "C20": "3", "C21": "3", "C22": "3", "C23": "4", "C24": "9", "X01": "5", "X02": "3",
}

# Keyword fallback mapper for free-text reasons (used only when the model doesn't return a code).
_KW = [
    ("C09", r"debt[- ]to[- ]income|\bdti\b|obligations|debt (load|burden|level)"),
    ("C08", r"income (is )?insufficient|insufficient income|income .*not sufficient"),
    ("C07", r"length of employment|employment (tenure|history)|time (on|at) (the )?job"),
    ("C05", r"irregular|temporary employment"),
    ("C17", r"delinquen|late payment|past due"),
    ("C15", r"limited credit|thin (credit )?file|credit experience"),
    ("C23", r"collateral|loan[- ]to[- ]value|\bltv\b|appraisal|property value"),
    ("X01", r"reserves|savings|down ?payment|closing costs|cash"),
    ("C22", r"inquir"),
    ("C14", r"credit score|credit history|credit profile|creditworthiness"),
]

def map_reason_text(text: str) -> str:
    t = (text or "").lower()
    for code, pat in _KW:
        if re.search(pat, t):
            return "X02" if code == "C14" and "score" in t else ("C16" if code == "C14" else code)
    return "C24"

def normalize_code(c) -> str | None:
    if c is None:
        return None
    c = str(c).strip().upper()
    return c if c in ALL_REASONS else None
