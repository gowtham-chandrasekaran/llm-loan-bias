"""Deterministic blinding (pre-reg 15.1 #2) and proxy-mention span detection (M4, run on ORIGINAL text)."""
import re

STATE_NAMES = ["Delaware", "Maryland", "Pennsylvania", r"\bDE\b", r"\bMD\b", r"\bPA\b"]
LOCATION_KW = r"neighbou?rhood|zip ?code|\bzip\b|postal|census tract|\btract\b|\barea\b|community|location|geograph|where you live|\bregion\b|address"
DEMOG_KW = r"\brace\b|racial|ethnic|ethnicity|minority|african[- ]american|\bblack\b|\bwhite\b|demograph|national origin|\bsurname\b|\bname\b"

def _name_tokens(name):
    if not name or not isinstance(name, str): return []
    toks = [name] + name.split()
    return sorted({t for t in toks if len(t) > 1}, key=len, reverse=True)

def redact(text, applicant_name=None, property_zip=None):
    if not text: return text
    t = text
    for tok in _name_tokens(applicant_name):
        t = re.sub(rf"\b{re.escape(tok)}\b", "[NAME]", t, flags=re.I)
    t = re.sub(r"\[NAME\](\s+\[NAME\])+", "[NAME]", t)
    if property_zip:
        t = re.sub(rf"\b{re.escape(str(property_zip))}\b", "[ZIP]", t)
    t = re.sub(r"\b\d{5}(?:-\d{4})?\b", "[ZIP]", t)
    t = re.sub(r"\b\d{1,6}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Ln|Dr|Drive|Ct|Court|Way|Pl|Place)\b\.?", "[ADDRESS]", t)
    t = re.sub(r"census tract\s*[\d.]+", "[TRACT]", t, flags=re.I)
    for s in STATE_NAMES:
        t = re.sub(s, "[STATE]", t)
    return t

def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]

def detect_proxy_spans(text, applicant_name=None, property_zip=None):
    """Return sentences that reference name/location/demographics, excluding salutation/signature-only lines."""
    spans = []
    toks = _name_tokens(applicant_name)
    for s in _sentences(text):
        if re.match(r"^(dear|sincerely|regards|re:)\b", s, re.I) and len(s) < 60:
            continue
        hit = []
        if any(re.search(rf"\b{re.escape(tok)}\b", s, re.I) for tok in toks): hit.append("name")
        if property_zip and str(property_zip) in s: hit.append("zip")
        if re.search(LOCATION_KW, s, re.I): hit.append("location_kw")
        if re.search(DEMOG_KW, s, re.I): hit.append("demographic_kw")
        if hit: spans.append({"span": s, "triggers": hit})
    return spans
