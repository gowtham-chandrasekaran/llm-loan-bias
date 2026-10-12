import json, re
from .regb import normalize_code, map_reason_text

def parse_output(text: str) -> dict:
    """Extract the first JSON object; validate fields. Returns dict with parse_ok flag and errors."""
    out = {"parse_ok": False, "errors": []}
    if not text:
        out["errors"].append("empty"); return out
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    obj = None
    try:
        obj = json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try: obj = json.loads(m.group(0))
            except Exception: pass
    if not isinstance(obj, dict):
        out["errors"].append("no_json"); return out
    dec = str(obj.get("decision", "")).strip().lower()
    dec = {"approved": "approve", "denied": "deny", "decline": "deny", "declined": "deny"}.get(dec, dec)
    if dec not in ("approve", "deny"):
        out["errors"].append("bad_decision")
    codes_raw = obj.get("reason_codes") or []
    codes = [c for c in (normalize_code(x) for x in codes_raw) if c]
    texts = obj.get("reasons_text") or []
    if not codes and texts:
        codes = [map_reason_text(x) for x in texts]; out["codes_from_text"] = True
    out.update(decision=dec if dec in ("approve", "deny") else None, letter=obj.get("letter", ""), reason_codes=codes,
               reasons_text=texts, rationale=obj.get("rationale"), invalid_codes=[x for x in codes_raw if not normalize_code(x)])
    out["parse_ok"] = not out["errors"]
    return out
