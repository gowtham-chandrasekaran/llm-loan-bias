import json, os, pytest
import pandas as pd
from llmbias.scoring import score_application
from llmbias.parsing import parse_output
from llmbias.render import render, order_seed
from llmbias.blind import redact, detect_proxy_spans
from llmbias.analysis import holm, wilson, mcnemar, jsd, permutation_paired, HOLM_FAMILY
from llmbias.labels import cohen_kappa
from llmbias.budget import BudgetGuard, BudgetExceeded, call_cost
from llmbias.clients import with_retries, TransientError
from llmbias.build_data import pair_diff_check, P

APP = dict(credit_score=680, dti_pct=44.5, ltv_pct=90.0, reserves_months=3.0, employment_years=4, annual_income=90000,
           job_title="electrician", monthly_debt_payments=3337, loan_amount=300000, property_value=335000, loan_term_months=360)

def test_scoring_deterministic_and_monotone():
    a = score_application(APP); assert a == score_application(APP)
    better = score_application({**APP, "credit_score": 740}); assert better.score > a.score
    assert all(c in ("X02", "C09", "C23", "X01", "C07") for c in a.reason_codes)

def test_parse_variants():
    assert parse_output('```json\n{"decision":"Denied","reason_codes":["c09"],"letter":"x"}\n```')["decision"] == "deny"
    p = parse_output('blah {"decision":"approve","letter":"ok"} trailing'); assert p["parse_ok"]
    assert not parse_output("no json")["parse_ok"]
    assert parse_output('{"decision":"maybe"}')["errors"] == ["bad_decision"]
    p = parse_output('{"decision":"deny","reasons_text":["debt-to-income too high"]}'); assert p["reason_codes"] == ["C09"]

def test_render_order_same_across_variants_and_logged():
    s = order_seed(1, "P001", 0)
    a = render({**APP, "applicant_name": "Mary Meyer", "property_zip": "19803", "proxy_state": "10"}, s)
    b = render({**APP, "applicant_name": "Lakisha Jackson", "property_zip": "19803", "proxy_state": "10"}, s)
    strip = lambda t: [l for l in t.splitlines() if not l.startswith("Applicant:")]
    assert strip(a) == strip(b)
    orders = {tuple(l for l in render(APP, order_seed(1, "P001", r)).splitlines() if l.startswith("-")) for r in range(5)}
    assert len(orders) > 1

def test_blind_and_spans():
    t = "Dear Lakisha Jackson,\nWe considered your neighborhood in ZIP 19701, Delaware. Ms. Jackson, your DTI is high."
    r = redact(t, "Lakisha Jackson", "19701")
    assert "Lakisha" not in r and "Jackson" not in r and "19701" not in r and "Delaware" not in r and "[NAME]" in r
    spans = detect_proxy_spans(t, "Lakisha Jackson", "19701")
    assert not any(s["span"].startswith("Dear") for s in spans)
    assert any("location_kw" in s["triggers"] for s in spans)

def test_stats_helpers():
    lo, hi = wilson(0, 25); assert lo == 0 and 0.13 < hi < 0.14
    assert mcnemar([1, 1, 1, 0], [0, 0, 0, 0])[:2] == (3, 0)
    h = holm({"M1a_name": 0.001, "M3_zip": 0.04}); assert h["M1a_name"]["reject"] and not h["M3_zip"]["reject"]
    assert set(h) == set(HOLM_FAMILY) and len(HOLM_FAMILY) == 8
    assert jsd({"C09": 5}, {"C09": 5}) < 1e-9 and jsd({"C09": 5}, {"C17": 5}) > 0.99
    obs, p = permutation_paired([0] * 30, [1] * 30, lambda a, b: sum(b) / len(b) - sum(a) / len(a), n_perm=500); assert p < 0.01
    assert cohen_kappa(["t", "f", "t", "f"], ["t", "f", "t", "f"]) == 1.0

def test_budget_hard_stop(tmp_path):
    prices = {"mock": {"mock-1": {"input": 1.0, "output": 1.0}}}
    g = BudgetGuard(0.01, {}, prices, str(tmp_path / "l.jsonl"))
    g.check("mock", "mock-1", 1000, 1000)
    g.record("mock", "mock-1", 5000, 3000, "a")
    with pytest.raises(BudgetExceeded): g.check("mock", "mock-1", 2000, 2000)
    assert abs(BudgetGuard(1, {}, prices, str(tmp_path / "l.jsonl")).spent - 0.008) < 1e-9  # ledger persisted

def test_retries():
    n = {"i": 0}
    def f():
        n["i"] += 1
        if n["i"] < 3: raise TransientError("rate limit")
        return "ok"
    assert with_retries(f, sleep=lambda s: None) == "ok" and n["i"] == 3

def test_pair_diff_detects_violation():
    v = pd.read_csv(P("data", "processed", "variants.csv"), dtype={"property_zip": str})
    v = v[v.profile_id.isin(["P000", "P001"])].copy()
    assert pair_diff_check(v).empty
    v.loc[(v.profile_id == "P000") & (v.variant == "name_b"), "credit_score"] += 1
    bad = pair_diff_check(v); assert len(bad) >= 1 and "credit_score" in bad.iloc[0].unexpected_diffs
