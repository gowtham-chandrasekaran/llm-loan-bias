import os
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
    # other-state (agency / bureau) ZIPs such as "Washington, DC 20580" are not applicant data (rubric v1.2 B2a): left as-is
    t = re.sub(r"\b\d{5}(?:-\d{4})?\b", lambda m: m.group() if _is_foreign_zip(t, m) else "[ZIP]", t)
    t = re.sub(r"\b\d{1,6}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Ln|Dr|Drive|Ct|Court|Way|Pl|Place)\b\.?", "[ADDRESS]", t)
    t = re.sub(r"census tract\s*[\d.]+", "[NEIGHBORHOOD]", t, flags=re.I)   # v1.2 B2 closed list: tract -> [NEIGHBORHOOD]
    for s in STATE_NAMES:
        t = re.sub(s, "[STATE]", t)
    return redact_places(t)

# ---- 15.8.4 (2): real place names (DE/MD/PA towns, cities, CDPs, counties) -> [CITY]
GAZETTEER_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed", "gazetteer_de_md_pa.csv")
_GAZ = None

def gazetteer():
    """Compiled patterns. Source: Census 2020 Gazetteer place + county files for DE/MD/PA (LSAD suffixes stripped).
    Names that are also common words or person names (column common_word) and bare county names are redacted only
    in a geographic context; any name followed by another state's code (", TX", ", DC") is an agency/bureau address (B2a)."""
    global _GAZ
    if _GAZ is None:
        import csv
        rows = list(csv.DictReader(open(os.path.abspath(GAZETTEER_CSV))))
        counties = {r["name"].replace(" County", "") for r in rows if r["kind"] == "county" and r["name"].endswith(" County")}
        places = {r["name"] for r in rows if r["kind"] in ("place", "place_supplement")}   # supplement: USPS localities without a Census place (audit: Hunt Valley)
        ctx = ({r["name"] for r in rows if r["common_word"] == "True"} | counties)
        always = {n for n in places if n not in ctx and len(n) > 2}
        ctx = {n for n in ctx if len(n) > 2} - always
        alt = lambda ns: "|".join(re.escape(n) for n in sorted(ns, key=len, reverse=True))
        _GAZ = {"county": re.compile(rf"\b(?:{alt(counties | places)})\s+County\b"),
                "always": re.compile(rf"\b(?:{alt(always)})\b"),
                "ctx": re.compile(rf"\b(?:{alt(ctx)})\b")}
    return _GAZ

_FOREIGN = re.compile(r",?\s*(?:[A-Z]{2}\b|D\.\s?C\.)")
_GEO_AFTER = re.compile(r",?\s*(?:\(?\[STATE\]|\[ZIP\]|\(ZIP)|\s+(?:area|city|borough|township|region)\b|/")
_GEO_BEFORE = re.compile(r"(?:located in|residence in|property in|located at|home in|house in|living in|near|in the|area of|/)\s*$", re.I)

def _place_matches(t):
    g = gazetteer(); out = []
    for m in g["county"].finditer(t): out.append((m.start(), m.end(), m.group()))
    taken = lambda a, b: any(a < e and b > s for s, e, _ in out)
    for kind in ("always", "ctx"):
        for m in g[kind].finditer(t):
            if taken(m.start(), m.end()): continue
            after, before = t[m.end():m.end() + 30], t[max(0, m.start() - 30):m.start()]
            if _FOREIGN.match(after): continue                     # other state's code -> agency/bureau (B2a)
            if kind == "ctx" and not (_GEO_AFTER.match(after) or _GEO_BEFORE.search(before)): continue
            out.append((m.start(), m.end(), m.group()))
    return sorted(out)

def find_places(text):
    """Real DE/MD/PA place names that redact_places would replace."""
    return [x for _, _, x in _place_matches(text or "")]

def redact_places(t):
    if not t: return t
    for a, b, _ in reversed(_place_matches(t)): t = t[:a] + "[CITY]" + t[b:]
    return t

SALUTATION_ONLY_RE = re.compile(r"^(Dear\s+)?(Mr\.|Ms\.|Mrs\.)?\s*\[NAME\],?$")   # memo m4_label_quality s.5 / 15.8.3 (1)
B2_TOKENS = ("[NAME]", "[ZIP]", "[STATE]", "[CITY]", "[NEIGHBORHOOD]", "[ADDRESS]")   # rubric v1.2 B2 closed list
PLACEHOLDER_RE = re.compile(r"\[(NAME|ZIP|STATE|CITY|ADDRESS|NEIGHBORHOOD)\]")
BRACKET_RE = re.compile(r"\[[^\[\]\n]{1,60}\]")
TARGET_STATES = ("DE", "MD", "PA")

def _is_foreign_zip(t, m):
    pre = t[max(0, m.start() - 6):m.start()]
    mm = re.search(r"\b([A-Z]{2}|D\.\s?C\.)\s*$", pre)
    return bool(mm) and mm.group(1) not in TARGET_STATES

def template_brackets(text):
    """Bracketed text that is not a B2 study token (rubric v1.2 B2b: template field, label 0)."""
    return [b for b in BRACKET_RE.findall(text or "") if b not in B2_TOKENS]

def normalize_tokens(text):
    """Map legacy study tokens onto the v1.2 closed list ([TRACT] -> [NEIGHBORHOOD]); bureau ZIPs already redacted
    after another state's code (", DC [ZIP]") are dropped, since they are not applicant data (B2a)."""
    t = (text or "").replace("[TRACT]", "[NEIGHBORHOOD]")
    return re.sub(r"(,?\s*\b(?!DE\b|MD\b|PA\b)(?:[A-Z]{2}|D\.\s?C\.))\s*\[ZIP\]", r"\1", t)

def reredact(text):
    """Apply the 15.8.4 redaction fixes to already-redacted pilot text (places + closed-list tokens)."""
    return redact_places(normalize_tokens(text))

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

def filter_spans_random(spans, key="span_redacted", seed=15820):
    """15.8.4 (3) as revised: drop salutation-only spans; among copies with the same normalized text keep one AT RANDOM
    (seeded), ignoring judge labels. Returns (kept, n_salutation_dropped, n_duplicate_dropped)."""
    import random as _r
    rng = _r.Random(seed); groups = {}; ns = 0
    for sp in spans:
        t = sp[key] if isinstance(sp, dict) else sp
        if is_salutation_only(t): ns += 1; continue
        groups.setdefault(normalize_span(t), []).append(sp)
    kept = [g[rng.randrange(len(g))] for g in groups.values()]
    return kept, ns, sum(len(g) - 1 for g in groups.values())

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
