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

SALUTATION_ONLY_RE = re.compile(r"^(Dear\s+)?(Mr\.|Ms\.|Mrs\.)?\s*\[NAME\],?$")   # memo m4_label_quality s.5 / 15.8.3 (1)
PLACEHOLDER_RE = re.compile(r"\[(NAME|ZIP|STATE|CITY|ADDRESS|NEIGHBORHOOD|TRACT)\]")

def is_salutation_only(span_redacted):
    """True for a span that is only a greeting with the name placeholder (e.g. '[NAME],', 'Dear Ms. [NAME],')."""
    return bool(SALUTATION_ONLY_RE.match((span_redacted or "").strip()))

def normalize_span(text):
    """Dedup key: lowercase, collapse whitespace, strip surrounding punctuation/quotes."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    return t.strip(" \t\"'.,;:!?-")

def has_substantive_placeholder(span_redacted):
    """Offline B2 proxy: a placeholder occurs in a span that is not salutation-only."""
    return (not is_salutation_only(span_redacted)) and bool(PLACEHOLDER_RE.search(span_redacted or ""))

def filter_spans(spans, key="span_redacted", seen=None):
    """15.8.3 (1): drop salutation-only spans, then deduplicate on normalized text (pass a shared `seen` set to dedup across items).
    Returns (kept, n_salutation_dropped, n_duplicate_dropped)."""
    seen = set() if seen is None else seen; kept, ns, nd = [], 0, 0
    for sp in spans:
        t = sp[key] if isinstance(sp, dict) else sp
        if is_salutation_only(t): ns += 1; continue
        k = normalize_span(t)
        if k in seen: nd += 1; continue
        seen.add(k); kept.append(sp)
    return kept, ns, nd

def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]

def detect_proxy_spans(text, applicant_name=None, property_zip=None, dedup=True):
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
    if not dedup: return spans
    # 15.8.3 (1): judge salutation-only status on the redacted form, then dedup on normalized text within this text
    for sp in spans: sp["span_redacted"] = redact(sp["span"], applicant_name, property_zip)
    kept, _, _ = filter_spans(spans)
    return kept
