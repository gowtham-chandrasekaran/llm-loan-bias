import json, os, re, stat
import numpy as np, pandas as pd, pytest
import llmbias.labels as LB
import llmbias.labels_v21 as V
from llmbias.blind import redact, redact_places, find_places, reredact, filter_spans_random, template_brackets, B2_TOKENS, BRACKET_RE
from llmbias.prompts import judge_hedging, judge_mention

AUDIT = ["Towson", "Bel Air", "Philadelphia", "Hampstead", "Hunt Valley", "Frederick County", "Lancaster", "Smyrna", "New Castle"]

@pytest.mark.parametrize("place", AUDIT)
def test_audit_places_redacted(place):
    for t in (f"the purchase of your primary residence in {place}, [STATE] (ZIP [ZIP])", f"the property located in {place}, MD 21204"):
        out = redact(t) if "MD" in t else redact_places(t)
        assert place not in out and "[CITY]" in out, out

def test_audit_items():
    # 7952166B (Lancaster), BFCDA4A7 (Philadelphia), D3C70065 (New Castle): zip_b leaks; 83910DB4: Hunt Valley/Towson
    cases = {"Lancaster": "to purchase the property located in Lancaster, [STATE] (ZIP [ZIP]) has been approved.",
             "Philadelphia": "your proposed primary residence in Philadelphia, [STATE] (ZIP [ZIP]) has been reviewed",
             "New Castle": "the purchase of your primary residence in New Castle, [STATE] (ZIP [ZIP]) has been approved.",
             "Hunt Valley/Towson": "the purchase of the property located in Hunt Valley/Towson area (ZIP [ZIP]) has been approved."}
    for k, t in cases.items():
        out = reredact(t); assert not find_places(out) and "[CITY]" in out
        for part in k.split("/"): assert part not in out
    assert reredact(cases["Hunt Valley/Towson"]).count("[CITY]") == 2

def test_agency_and_common_words_untouched():
    assert redact("Federal Trade Commission, Washington, DC 20580.") == "Federal Trade Commission, Washington, DC 20580."
    assert "Allen, TX" in redact("Experian, P.O. Box 2002, Allen, TX 75013")
    for t in ("Reading your file, we found", "Mr. Taylor reviewed it", "Liberty of choice", "Progress was made"): assert redact_places(t) == t

def test_closed_list_tokens():
    t = redact("Census tract 1234.01 in Towson, MD 21204", "A B", "21204")
    assert set(re.findall(r"\[[A-Z]+\]", t)) <= set(B2_TOKENS) and "[TRACT]" not in t
    assert reredact("in [TRACT], Bel Air") == "in [NEIGHBORHOOD], [CITY]"
    assert template_brackets("[Bank Name] at [ZIP] [City, State]") == ["[Bank Name]", "[City, State]"]

def test_dedup_random_seeded_ignores_judge():
    sp = [{"t": "Same text.", "judge": j, "id": i} for i, j in enumerate([0, 1, 0, 0, 0, 0])] + [{"t": "[NAME],", "judge": 0, "id": 9}]
    picks = {filter_spans_random(sp, key="t", seed=s)[0][0]["id"] for s in range(40)}
    assert len(picks) > 1                                       # random, not judge-positive or first copy
    a, b = filter_spans_random(sp, key="t", seed=15820), filter_spans_random(sp, key="t", seed=15820)
    assert a == b and a[1] == 1 and a[2] == 5

def test_tfidf_clusters():
    t = ["The property located in ZIP code [ZIP] was approved.", "The property located in ZIP code [ZIP] was approved!", "ECOA notice text here."]
    c = V.clusters(t); assert c[0] == c[1] != c[2]

def test_salutation_normalized():
    for l in ("Dear Ms. [NAME],\n\nBody.", "[NAME],\nBody.", "Dear Valued Customer,\nBody.", "Body only."):
        assert V.normalize_salutation(l).split("\n", 1)[0] == "Dear Applicant,"

def test_prompts_v12():
    _, h = judge_hedging("x")
    for k in ("This decision is final", "ECOA", "co-signer", "foreseeable future", "naming a condition"): assert k in h
    _, m = judge_mention("y")
    for k in ("[NEIGHBORHOOD]", "[Bank Name]", "Washington, DC", "B4a", "B2c"): assert k in m

def test_load_constructed_strips_notes(tmp_path):
    p = tmp_path / "c.md"
    p.write_text("# Title\nintro\n\n## C01\n\nDear Applicant,\n\nBody one.\n> Feature: futility\n<!-- note -->\nNote: hidden\n\n## C02\n\nDear Applicant,\nBody two.\n")
    c = V.load_constructed(str(p)); assert [x[0] for x in c] == ["C01", "C02"]
    assert "Feature" not in c[0][1] and "note" not in c[0][1].lower() and "Body one." in c[0][1]

def _fake_pool():
    rows = []
    for g in ("base", "name", "zip"):
        for i in range(120):
            t = f"Dear Ms. [NAME],\n\nLetter {g} {i} about income {i*7}." + (" Unfortunately we cannot." if i % 10 == 0 else "") + (" Property in Towson." if i == 3 else "")
            rows.append(dict(internal_id=f"h{g}{i:03d}", task="hedging", variant=g, call_id=f"c{g}{i}", text=t, judge=int(i < 7)))
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima"]
        for i in range(120):
            w = f"{words[i % 12]} {words[(i // 12) % 12]} {i}"
            t = ["[NAME],", f"Your home in [ZIP] qualifies under plan {w}.", f"Income {w} reason applies today.", f"Values in your neighborhood {w} fell.",
                 f"Contact [credit bureau name] about {w}.", f"Under the Equal Credit Opportunity Act {w} we do not discriminate.", f"Located in Bel Air, [STATE], case {w}."][i % 7]
            rows.append(dict(internal_id=f"h{g}{i:03d}#s0", task="proxy_mention", variant=g, call_id=f"c{g}{i}", text=t, judge=int(i == 1)))
    return pd.DataFrame(rows)

def _rubric(tmp_path):
    p = tmp_path / "rubric.md"; p.write_text("# Rubric v1.2 test\nB2 closed list.\n"); import hashlib
    return str(p), hashlib.sha256(p.read_bytes()).hexdigest()

def _cands(tmp_path):
    p = tmp_path / "cands.md"; p.write_text("".join(f"## C{i:02d}\n\nDear Applicant,\n\nConstructed letter {i}. We would not encourage you to reapply.\n> Feature: (a)\n\n" for i in range(1, 21)))
    return str(p)

def test_export_v21(tmp_path, monkeypatch):
    monkeypatch.setattr(LB, "item_pool", lambda run_dir: _fake_pool())
    rp, rs = _rubric(tmp_path); out = tmp_path / "lab"
    with pytest.raises(SystemExit): V.export(str(tmp_path / "runX"), outdir=str(out), rubric_path=rp, rubric_sha="0" * 64, candidates=_cands(tmp_path))
    res = V.export(str(tmp_path / "runX"), outdir=str(out), rubric_path=rp, rubric_sha=rs, candidates=_cands(tmp_path))
    for p in (res["key"], res["selection"]): assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and p.startswith(str(tmp_path / "private"))
    a, b = (pd.read_csv(out / f"labeler_{x}.csv", dtype=str, keep_default_na=False) for x in "AB")
    assert sorted(a.text) == sorted(b.text) and list(a.text) != list(b.text)
    assert (a.task == "hedging").sum() == 120 and (a.task == "proxy_mention").sum() <= 100
    key = json.load(open(res["key"])); m = key["labelers"]["A"]["map"]
    assert key["labelers"]["A"]["order_seed"] == 15821 and key["labelers"]["B"]["order_seed"] == 15822 and key["draw_seed"] == 15820
    assert key["rubric_sha256"] == rs and len(key["candidates_sha256"]) == 64
    assert sum(v["constructed"] for v in m.values()) == 20 and all("placeholder_free" in v for v in m.values())
    meta = json.load(open(out / "LABELING_METADATA.json")); assert meta["inputs_sha256"]["rubric"] == rs
    allowed_brackets = {i for i, v in m.items() if v["stratum"] == "hard:template_bracket"}
    for r in a.itertuples():
        assert not find_places(r.text), r.text                                   # no gazetteer place survives
        stray = [x for x in BRACKET_RE.findall(r.text) if x not in B2_TOKENS]
        assert not stray or r.item_id in allowed_brackets, (r.item_id, stray)
        if r.task == "hedging": assert r.text.split("\n", 1)[0] == "Dear Applicant,"
        assert "Feature" not in r.text and not re.search(r"\bC\d{2}\b", r.text)
    boiler = sum(v["stratum"] == "placeholder_boilerplate" for v in m.values()); assert boiler <= 30
    assert sum(v["stratum"].startswith("m4:") for v in m.values()) == 0
    log = json.load(open(res["selection"])); assert log["draw_seed"] == 15820 and "residual_place_replacements" in log
