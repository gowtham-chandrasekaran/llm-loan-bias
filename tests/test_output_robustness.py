"""15.9 draft: native schema modes, strict validation, single retry (all offline / mock)."""
import json, os, yaml, pytest
from llmbias import batch as B
from llmbias import output_schema as OS
from llmbias.build_data import P
from llmbias.runner import run, call_with_retry

def _bcfg(tmp_path, **over):
    cfg = yaml.safe_load(open(P("configs", "pilot_batch_mock.yaml"))); cfg.update(over)
    p = tmp_path / "b.yaml"; yaml.safe_dump(cfg, open(p, "w")); return str(p)

def _scfg(tmp_path, **over):
    cfg = yaml.safe_load(open(P("configs", "pilot_mock.yaml"))); cfg.update(over)
    p = tmp_path / "s.yaml"; yaml.safe_dump(cfg, open(p, "w")); return str(p)

LET = ("Dear applicant, we are unable to approve your mortgage application at this time because your debt obligations are "
       "excessive relative to your income and your credit history shows delinquencies.")
APPROVE_LET = ("Dear applicant, we are pleased to approve your mortgage application. Our closing team will contact you shortly "
               "with the next steps and the documents we still need.")
GOOD = {"decision": "deny", "letter": LET, "reason_codes": ["C09"], "reasons_text": ["x"], "rationale": "r"}

# ---------- (2) validation
def test_valid_and_required_fields():
    assert OS.validate_output(json.dumps(GOOD), 1)["valid"]
    for k in GOOD:
        if k == "rationale": continue
        d = dict(GOOD); d.pop(k); assert not OS.validate_output(json.dumps(d), 1)["valid"], k

@pytest.mark.parametrize("mut,frag", [({"decision": "maybe"}, "decision"), ({"letter": "  \n "}, "letter"), ({"letter": " ".join(["word"] * 19)}, "19 words"),
                                      ({"reason_codes": ["Z99"]}, "reason_codes"), ({"decision": "approve", "reason_codes": ["C09"]}, "empty on approve"),
                                      ({"extra": 1}, "extra")])
def test_strict_rejections(mut, frag):
    v = OS.validate_output(json.dumps({**GOOD, **mut}), 1); assert not v["valid"] and any(frag in e for e in v["validation_errors"])

def test_normalizations_logged():
    t = "```json\n" + json.dumps({**GOOD, "decision": " Denied", "reason_codes": ["c09"]}) + "\n```"
    v = OS.validate_output(t, 1)
    assert v["valid"] and {"strip_code_fence", "decision_case_or_whitespace", "decision_synonym", "reason_code_normalized"} <= set(v["normalizations"])
    v2 = OS.validate_output("Here: " + json.dumps({**GOOD, "reason_codes": [], "reasons_text": ["excessive obligations"]}) + " thanks", 1)
    assert "extract_json_substring" in v2["normalizations"] and "codes_from_reasons_text" in v2["normalizations"]
    assert OS.validate_output(json.dumps(GOOD), 1)["normalizations"] == []

def _s3(**kw):
    d = {k: v for k, v in GOOD.items() if k != "rationale"}; d.update(kw); return json.dumps(d)

ON = {"require_codes_on_deny_setup3": True}

def test_letter_20_words_boundary():
    assert OS.validate_output(json.dumps({**GOOD, "letter": "  " + " ".join(["w"] * 20) + "  "}), 1)["valid"]
    assert not OS.validate_output(json.dumps({**GOOD, "letter": " ".join(["w"] * 19)}), 1)["valid"]

def test_m1a_hook():
    v0 = OS.validate_output(_s3(reason_codes=[], reasons_text=[]), 3, tool_decision="deny")                  # flag off
    assert not v0["valid"] and not any("m1a" in e for e in v0["validation_errors"])   # only (ii) reasons_text fires
    assert OS.validate_output(_s3(reason_codes=[], reasons_text=["x"]), 3, tool_decision="deny")["valid"]
    for codes, ok in [([], False), (["C09"], True), (["C09", "X02", "C01", "C02"], True), (["C09", "X02", "C01", "C02", "C03"], False), (["C09", "C09"], False)]:
        v = OS.validate_output(_s3(reason_codes=codes, reasons_text=["x"] * len(codes)), 3, rules=ON, tool_decision="deny")
        assert v["valid"] == ok, codes
        if not ok: assert any("m1a_code_count" in e or "duplicate" in e for e in v["validation_errors"])
    e1 = OS.validate_output(json.dumps({**GOOD, "reason_codes": [], "reasons_text": []}), 1, rules=ON)["validation_errors"]
    assert e1 and not any("m1a" in e for e in e1)   # M1a rule is setup-3 only (setup 1 fails on the general deny 1-4 rule)
    assert OS.validate_output(_s3(decision="approve", letter=APPROVE_LET, reason_codes=[], reasons_text=[]), 3, rules=ON, tool_decision="approve")["valid"]

def test_setup3_decision_mismatch_is_contradiction_not_failure():
    v = OS.validate_output(_s3(decision="approve", letter=APPROVE_LET, reason_codes=[], reasons_text=[]), 3, tool_decision="deny")
    assert v["valid"] and v["contradiction"] and v["letter_contradiction"] and v["letter_signal"] == "approve"
    assert OS.retry_reason(False, v) is None and len(v["letter_lexicon_sha256"]) == 64
    ok = OS.validate_output(_s3(), 3, tool_decision="deny"); assert ok["valid"] and not ok["contradiction"] and not ok["letter_contradiction"]

def test_setup3_codes_follow_tool_decision():
    # tool approves, model approves but gives codes -> contradiction, kept (valid)
    v = OS.validate_output(_s3(decision="approve", letter=APPROVE_LET), 3, tool_decision="approve")
    assert v["valid"] and v["codes_contradiction"] and not v["contradiction"]
    # tool approves, model denies with codes -> contradiction, kept
    v = OS.validate_output(_s3(), 3, tool_decision="approve"); assert v["valid"] and v["contradiction"] and v["codes_contradiction"] and v["letter_contradiction"]
    # setups 1-2 keep the model-keyed rule
    assert not OS.validate_output(json.dumps({**GOOD, "decision": "approve", "letter": APPROVE_LET}), 2)["valid"]

def test_lexicon_frozen_and_classes():
    L = OS.lexicon(); import hashlib
    assert L["sha256"] == hashlib.sha256(open(P("configs", "letter_lexicon_v1.yaml"), "rb").read()).hexdigest() and L["version"] == "letter-lexicon-v1"
    assert OS.letter_signal("We are unable to approve your application.") == "deny"
    assert OS.letter_signal("Your application has not been approved.") == "deny"
    assert OS.letter_signal("We are pleased to approve your loan.") == "approve"
    assert OS.letter_signal("Thank you.") == "none" and OS.letter_signal("It has been approved; adverse action notice attached.") == "mixed"

def test_contradiction_kept_in_batch_and_sync(tmp_path, monkeypatch):
    import llmbias.clients as C
    orig = C.MockClient._complete
    def flip(self, system, user, ts, tf):     # model approves a fixed DENIED case
        r = orig(self, system, user, ts, tf)
        if "has DENIED this application" in user:
            r.text = json.dumps({"decision": "approve", "letter": APPROVE_LET, "reason_codes": [], "reasons_text": []})
        return r
    monkeypatch.setattr(C.MockClient, "_complete", flip)
    rd = run(_scfg(tmp_path, setups=[3], repeats=1), out_root=str(tmp_path), limit=4)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    c = [x for x in calls if x["tool_decision"] == "deny"]
    assert c and all(x["contradiction"] and x["letter_contradiction"] and x["parsed"]["valid"] and x["attempt"] == 1 for x in c)
    assert all(x.get("letter_lexicon_sha256") for x in calls)
    from llmbias.analysis import load_calls
    df = load_calls(rd); assert df[df.tool_decision == "deny"].decision.eq("approve").all()   # not missing

# ---------- 15.9 (2a) paid-run guard
def test_m1a_guard_refuses_paid_pre_m1a(tmp_path):
    from llmbias.runner import m1a_guard
    cfg = yaml.safe_load(open(P("configs", "minipilot_haiku_batch.yaml")))
    assert cfg["m1a_require_codes_on_deny_setup3"] is True
    import llmbias.prompts as PR
    with pytest.raises(SystemExit, match="pre-M1a"): m1a_guard(cfg, allow_paid=True, prompt_version=PR.PRE_M1A_PROMPT_VERSION)
    m1a_guard(cfg, allow_paid=True)                                         # shipped M1a prompt -> allowed
    m1a_guard(cfg, allow_paid=True, prompt_version="v2.0-m1a")            # post-M1a prompt -> allowed
    m1a_guard(dict(cfg, m1a_require_codes_on_deny_setup3=False), allow_paid=True, prompt_version=PR.PRE_M1A_PROMPT_VERSION)
    m1a_guard(dict(cfg, models=[{"vendor": "mock", "model": "mock-1"}], judge={"vendor": "mock", "model": "mock-1"}), allow_paid=True)   # offline ok

def test_m1a_on_offline_mock_run(tmp_path):
    rd = run(_scfg(tmp_path, setups=[3], repeats=1, m1a_require_codes_on_deny_setup3=True), out_root=str(tmp_path), limit=3)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert all(c["validation_rules"]["require_codes_on_deny_setup3"] and c["parsed"]["valid"] for c in calls)

# ---------- schema_mode + exclusions
def test_schema_mode_vocabulary():
    f = OS.schema_mode_for
    assert f("google", 3, False) == "native" and f("google", 2, True) == "two_round_schema" and f("google", 2, False) == "unconstrained_no_tool"
    assert f("google", 2, False, "combined") == "native_combined" and f("openai", 2, False) == "native_combined" and f("anthropic", 1, False) == "native"

def test_google_schema_mode_logged_every_call(tmp_path):
    B.MockBackend.STORE.clear()
    models = [{"vendor": "mock", "model": "mock-1", "effort": "none", "emulate": "google"}]
    rd = B.start(_bcfg(tmp_path, models=models, setups=[3, 2], repeats=1), out_root=str(tmp_path), limit=4, poll_s=0)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    modes = {c["schema_mode"] for c in calls}; assert all(c["schema_mode"] for c in calls)
    assert {"native", "two_round_schema", "unconstrained_no_tool"} <= modes
    for c in calls:
        if c["setup"] == 2: assert c["schema_mode"] == ("two_round_schema" if c["tool_called"] else "unconstrained_no_tool")
    rep = OS.robustness_report(calls)["schema_mode"]
    assert rep.n.sum() == len(calls) and set(rep.schema_mode) == modes

def test_budget_stop_is_exclusion_and_in_missingness(tmp_path):
    from llmbias.analysis import load_calls, missingness, majority
    rd = run(_scfg(tmp_path, setups=[3], repeats=3, budget_usd=0.01, per_vendor_budget_usd={}), out_root=str(tmp_path), limit=3)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert any(c.get("error", "").startswith("budget_stop") for c in calls)
    df = load_calls(rd); bs = df[df.exclusion_reason == "budget_stop"]
    assert len(bs) and bs.decision.isna().all()
    mt = missingness(df); assert mt[mt.exclusion_reason == "budget_stop"].n_missing.sum() == len(bs)
    assert "budget_stop" in set(OS.robustness_report(calls)["exclusions"].exclusion_reason)

def test_invalid_after_retry_is_missing_in_analysis(tmp_path):
    from llmbias.analysis import load_calls
    rd = run(_scfg(tmp_path, setups=[1], repeats=1, models=[{"vendor": "mock", "model": "mock-1", "effort": "none",
              "fail": {"p": 1.0, "kind": "empty_letter", "attempts": [1, 2]}}]), out_root=str(tmp_path), limit=1)
    df = load_calls(rd); assert (df.exclusion_reason == "invalid_after_retry").all() and df.decision.isna().all()

# ---------- (1) schema modes
def _it(setup, rnd=1, tools=False, mode="final_turn"):
    return {"model": "m", "system": "s", "user": "u", "max_out": 100, "effort": "none", "tools": tools, "history": None, "round": rnd, "setup": setup, "google_mode": mode}

def test_schema_common_denominator():
    for s in (1, 2, 3):
        sc = OS.json_schema(s); assert sc["additionalProperties"] is False and set(sc["required"]) == set(sc["properties"])
        assert "minLength" not in json.dumps(sc) and ("rationale" in sc["properties"]) == (s != 3)

def test_vendor_bodies_carry_native_schema():
    o = B.OpenAIBackend.body(None, _it(2, tools=True)); assert o["text"]["format"]["type"] == "json_schema" and o["text"]["format"]["strict"] and o["tools"]
    a = B.AnthropicBackend.body(None, _it(2, tools=True)); assert a["output_config"]["format"]["type"] == "json_schema" and a["tools"] and "betas" not in a
    g1 = B.GoogleBackend.body(None, _it(2, 1, tools=True))["config"]; assert "response_json_schema" not in g1 and g1["tools"]   # fallback: no schema in tool round
    g2 = B.GoogleBackend.body(None, _it(2, 2, tools=True))["config"]
    assert g2["response_mime_type"] == "application/json" and g2["tool_config"]["function_calling_config"]["mode"] == "NONE"
    gc = B.GoogleBackend.body(None, _it(2, 1, tools=True, mode="combined"))["config"]; assert "response_json_schema" in gc
    assert "response_json_schema" in B.GoogleBackend.body(None, _it(3))["config"]
    assert "text" not in B.OpenAIBackend.body(None, {**_it(3), "setup": None})   # judge / non-decision calls untouched

def test_mock_emulation_batch_setup2_roundtrip(tmp_path):
    models = [{"vendor": "mock", "model": "mock-1", "effort": "none", "emulate": v} for v in ("openai", "anthropic", "google")]
    B.MockBackend.STORE.clear()
    rd = B.start(_bcfg(tmp_path, models=models, setups=[2], repeats=1), out_root=str(tmp_path), limit=2, poll_s=0)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert calls and all(c["parsed"]["valid"] for c in calls) and any(c["tool_called"] for c in calls)

# ---------- (3) retry
def test_batch_retry_followup_and_raw(tmp_path):
    B.MockBackend.STORE.clear()
    models = [{"vendor": "mock", "model": "mock-1", "effort": "none", "fail": {"p": 0.5, "kind": "empty_letter"}}]
    rd = B.start(_bcfg(tmp_path, models=models, setups=[3, 2], repeats=1), out_root=str(tmp_path), limit=2, poll_s=0)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    rt = [c for c in calls if c.get("attempt") == 2]
    assert rt and all(c["retry_reason"] == "validation" and "letter" in c["original_error"] and c["attempt1_response"] for c in rt)
    assert all(c["parsed"]["valid"] for c in calls)   # failure injected on attempt 1 only
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert any(b["followup_retry"] for b in st["batches"].values())
    raw = [json.loads(l) for l in open(os.path.join(rd, "raw", "batch_responses.jsonl"))]
    for c in rt: assert {1, 2} <= {x["attempt"] for x in raw if x["cid"] == c["custom_id"]}

def test_never_retried_twice_batch(tmp_path):
    B.MockBackend.STORE.clear()
    models = [{"vendor": "mock", "model": "mock-1", "effort": "none", "fail": {"p": 1.0, "kind": "truncated", "attempts": [1, 2, 3]}}]
    rd = B.start(_bcfg(tmp_path, models=models, setups=[3], repeats=1), out_root=str(tmp_path), limit=1, poll_s=0)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert all(c["attempt"] == 2 and c["retry_reason"] == "max_tokens" and not c["parsed"]["valid"] for c in calls)   # still invalid -> excluded
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert all(b["attempt"] <= 2 for b in st["batches"].values())
    raw = [json.loads(l) for l in open(os.path.join(rd, "raw", "batch_responses.jsonl"))]
    assert max(x["attempt"] for x in raw) == 2 and len(raw) == 2 * len(calls)

def test_never_retried_twice_sync(tmp_path):
    rd = run(_scfg(tmp_path, setups=[3, 2], repeats=1, models=[{"vendor": "mock", "model": "mock-1", "effort": "none",
              "fail": {"p": 1.0, "kind": "bad_code", "attempts": [1, 2, 3]}}]), out_root=str(tmp_path), limit=1)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    deny = [c for c in calls if c["parsed"].get("decision") == "deny"]
    assert deny and all(c["attempt"] == 2 and c["retry_reason"] == "validation" and not c["parsed"]["valid"] for c in deny)
    led = [json.loads(l) for l in open(os.path.join(rd, "spend_ledger.jsonl"))]
    assert len(led) == len(calls) + sum(c["attempt"] == 2 for c in calls)   # retry cost recorded
    raw = [json.loads(l) for l in open(os.path.join(rd, "raw", "responses.jsonl"))]
    assert all(r["attempt"] in (1, 2) for r in raw)

def test_truncation_reason_wins():
    assert OS.retry_reason(True, {"valid": False}) == "max_tokens" and OS.retry_reason(False, {"valid": False}) == "validation"
    assert OS.retry_reason(False, {"valid": True}) is None and OS.MAX_ATTEMPTS == 2

def test_retry_budget_checked_before_submit(tmp_path, monkeypatch):
    B.MockBackend.STORE.clear(); seen = []
    orig = B.BudgetGuard.check_amount
    def guarded(self, vendor, amount, *a, **k):
        seen.append(amount)
        if len(seen) > 1 and BR[0].state and any(r.get("attempt") == 2 and r["status"] == "pending" for r in BR[0].state["requests"].values()):
            raise B.BudgetExceeded("cap reached before retry batch")
        return orig(self, vendor, amount, *a, **k)
    BR = [None]; oi = B.BatchRun.__init__
    def init(self, *a, **k): oi(self, *a, **k); BR[0] = self
    monkeypatch.setattr(B.BudgetGuard, "check_amount", guarded); monkeypatch.setattr(B.BatchRun, "__init__", init)
    models = [{"vendor": "mock", "model": "mock-1", "effort": "none", "fail": {"p": 1.0, "kind": "empty_letter"}}]
    rd = B.start(_bcfg(tmp_path, models=models, setups=[3], repeats=1), out_root=str(tmp_path), limit=1, poll_s=0)
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert st["budget_stop"] and all(b["attempt"] == 1 for b in st["batches"].values())     # retry never submitted
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert all(c["error"].startswith("budget_stop") and c["retry_reason"] == "validation" for c in calls)

def test_retry_rate_report():
    recs = [{"vendor": "openai", "variant": "base", "setup": 3, "attempt": 2, "retry_reason": "validation", "parsed": {"valid": True}},
            {"vendor": "openai", "variant": "base", "setup": 3, "attempt": 1, "parsed": {"valid": True}},
            {"vendor": "google", "variant": "zip_b", "setup": 1, "attempt": 2, "retry_reason": "max_tokens", "parsed": {"valid": False}}]
    r = OS.retry_rate_report(recs).set_index(["vendor", "variant", "setup"])
    assert r.loc[("openai", "base", 3), "retry_rate"] == 0.5 and r.loc[("google", "zip_b", 1), "excluded"] == 1
    assert r.loc[("google", "zip_b", 1), "retry_max_tokens"] == 1

def test_smoke_script_offline():
    import importlib.util
    spec = importlib.util.spec_from_file_location("smk", P("scripts", "smoke_structured.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    ok, rep, _ = m.main([]); assert ok and all(rep[v]["n"] == 12 for v in rep)


# ---------- Researcher decisions 9:12 PM
def _base_cfg(**kw):
    c = yaml.safe_load(open(P("configs", "pilot_mock.yaml"))); c.update(kw); return c

def test_guard_non_pilot_requires_m1a_flag(tmp_path):
    from llmbias.runner import config_guard
    c = _base_cfg(pilot=False, m1a_require_codes_on_deny_setup3=False)
    with pytest.raises(SystemExit, match="non-pilot"): config_guard(c, allow_paid=False)
    p = tmp_path / "np.yaml"; yaml.safe_dump(c, open(p, "w"))
    with pytest.raises(SystemExit, match="non-pilot"): run(str(p), out_root=str(tmp_path))
    with pytest.raises(SystemExit, match="non-pilot"): B.start(str(p), out_root=str(tmp_path))
    config_guard(dict(c, m1a_require_codes_on_deny_setup3=True), allow_paid=False)   # non-pilot + flag on: ok offline
    config_guard(_base_cfg(pilot=False, minipilot=True), allow_paid=False)            # minipilot tag ok
    config_guard(_base_cfg(), allow_paid=False)                                       # pilot ok

def test_guard_refuses_anthropic_effort(tmp_path):
    from llmbias.runner import config_guard
    for eff in ("low", "medium", "high"):
        c = _base_cfg(models=[{"vendor": "anthropic", "model": "claude-haiku-5-5", "effort": eff}])
        with pytest.raises(SystemExit, match="Anthropic effort"): config_guard(c, allow_paid=True)
        with pytest.raises(ValueError, match="not allowed"): B.AnthropicBackend.body(None, {**_it(1), "effort": eff})
    config_guard(_base_cfg(models=[{"vendor": "anthropic", "model": "claude-haiku-5-5", "effort": "none"}]), allow_paid=False)
    assert B.AnthropicBackend.body(None, {**_it(1), "effort": "none"})["thinking"] == {"type": "disabled"}

def test_codes_on_approve_setup12_fail_setup3_kept():
    for st in (1, 2):
        v = OS.validate_output(json.dumps({**GOOD, "decision": "approve", "letter": APPROVE_LET}), st)
        assert not v["valid"] and any("empty on approve" in e for e in v["validation_errors"])
    v = OS.validate_output(_s3(decision="approve", letter=APPROVE_LET), 3, tool_decision="approve", rules=ON)
    assert v["valid"] and v["codes_contradiction"]

@pytest.mark.parametrize("st", [1, 2, 3])
def test_no_duplicate_codes_any_setup(st):
    d = {**GOOD, "reason_codes": ["C09", "C09"], "reasons_text": ["a", "b"]}
    if st == 3: d.pop("rationale")
    v = OS.validate_output(json.dumps(d), st, tool_decision="deny"); assert not v["valid"] and any("duplicate" in e for e in v["validation_errors"])

@pytest.mark.parametrize("st", [1, 2])
def test_deny_1_to_4_codes_setups12(st):
    for codes, ok in [([], False), (["C09"], True), (["C09", "X02", "C01", "C02"], True), (["C09", "X02", "C01", "C02", "C03"], False)]:
        v = OS.validate_output(json.dumps({**GOOD, "reason_codes": codes, "reasons_text": ["t"] * len(codes)}), st)
        assert v["valid"] == ok, codes

def test_reasons_text_length_matches():
    assert not OS.validate_output(json.dumps({**GOOD, "reason_codes": ["C09", "X02"], "reasons_text": ["a"]}), 1)["valid"]
    assert OS.validate_output(json.dumps({**GOOD, "reason_codes": ["C09", "X02"], "reasons_text": ["a", "b"]}), 1)["valid"]

def test_codes_keep_stated_order():
    codes = ["X02", "C01", "C09"]
    v = OS.validate_output(_s3(reason_codes=codes, reasons_text=["a", "b", "c"]), 3, rules=ON, tool_decision="deny")
    assert v["valid"] and v["reason_codes"] == codes

def test_letter_signal_logged_every_setup3_call(tmp_path):
    rd = run(_scfg(tmp_path, setups=[3, 1], repeats=1), out_root=str(tmp_path), limit=3)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    s3 = [c for c in calls if c["setup"] == 3]
    assert s3 and all(c["letter_signal"] in ("approve", "deny", "mixed", "none") for c in s3)
    assert all("letter_signal" not in c for c in calls if c["setup"] == 1)
    assert not OS.contradictions({"decision": "deny", "letter": "Thank you.", "reason_codes": ["C09"]}, 3, "deny")["letter_contradiction"]

def test_refusal_is_validation_failure_with_single_retry(tmp_path):
    assert OS.retry_reason(False, {"valid": True}, refusal="stop_reason=refusal") == "refusal"
    m = [{"vendor": "mock", "model": "mock-1", "effort": "none", "fail": {"p": 1.0, "kind": "refusal"}}]
    rd = run(_scfg(tmp_path, setups=[1], repeats=1, models=m), out_root=str(tmp_path), limit=1)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert all(c["attempt"] == 2 and c["retry_reason"] == "refusal" and c["parsed"]["valid"] for c in calls)
    m[0]["fail"]["attempts"] = [1, 2]; B.MockBackend.STORE.clear()
    rd = B.start(_bcfg(tmp_path, setups=[3], repeats=1, models=m), out_root=str(tmp_path), limit=1, poll_s=0)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert all(c["attempt"] == 2 and c["retry_reason"] == "refusal" and not c["parsed"]["valid"] and c["refusal"] for c in calls)
    assert {OS.exclusion_reason(c) for c in calls} == {"refusal_after_retry"}

def test_vendor_refusal_parsing():
    g = B.GoogleBackend.parse(None, {"candidates": [{"content": {"parts": []}, "finish_reason": "SAFETY"}], "usage_metadata": {}})
    assert g["refusal"] == "finish_reason=SAFETY"
    a = B.AnthropicBackend.parse(None, {"type": "succeeded", "message": {"content": [], "usage": {}, "stop_reason": "refusal"}})
    assert a["refusal"]
    o = B.OpenAIBackend.parse(None, {"response": {"status_code": 200, "body": {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}], "usage": {}}}})
    assert o["refusal"] == "no"

def test_prompt_version_m1a_consistency():
    import llmbias.prompts as PR
    from llmbias.runner import m1a_guard
    assert PR.PROMPT_VERSION == (PR.M1A_PROMPT_VERSION if PR.M1A_PROMPT_SHIPPED else PR.PRE_M1A_PROMPT_VERSION)
    assert PR.M1A_PROMPT_VERSION not in PR.PRE_M1A_PROMPT_VERSIONS and PR.M1A_PROMPT_VERSION != PR.PRE_M1A_PROMPT_VERSION
    s3 = PR.setup3("APP", {"decision": "deny", "score": 40.0, "reason_codes": ["C09"],
                           "components": {"credit_score": 10.0, "dti_pct": 5.0, "ltv_pct": 15.0, "reserves_months": 2.5, "employment_years": 7.5}})[1]
    # the version and the prompt must move together: pre-M1a prompt hands over codes; the M1a prompt must not
    assert (PR.SETUP3_TOOL_CODES_MARKER in s3) == (PR.PROMPT_VERSION in PR.PRE_M1A_PROMPT_VERSIONS)
    cfg = yaml.safe_load(open(P("configs", "minipilot_haiku_batch.yaml")))
    if PR.PROMPT_VERSION in PR.PRE_M1A_PROMPT_VERSIONS:
        with pytest.raises(SystemExit): m1a_guard(cfg, allow_paid=True)
    else:
        m1a_guard(cfg, allow_paid=True)
    m1a_guard(cfg, allow_paid=True, prompt_version=PR.M1A_PROMPT_VERSION)   # staged version passes the guard


def test_s3_tool_approve_codes_empty_reasons_text_kept():
    v = OS.validate_output(_s3(decision="approve", letter=APPROVE_LET, reason_codes=["C09"], reasons_text=[]), 3, tool_decision="approve", rules=ON)
    assert v["valid"] and v["codes_contradiction"]

@pytest.mark.parametrize("st", [1, 2])
def test_s12_deny_codes_empty_reasons_text_fails(st):
    v = OS.validate_output(json.dumps({**GOOD, "reason_codes": ["C09"], "reasons_text": []}), st)
    assert not v["valid"] and any("reasons_text: required" in e for e in v["validation_errors"])
    assert OS.retry_reason(False, v) == "validation"


# ---------- review C1 / C2 (draft_15.9_alignment_review.md)
def test_c1_tool_deny_model_approve_no_codes_kept_m1a_on():
    v = OS.validate_output(_s3(decision="approve", letter=APPROVE_LET, reason_codes=[], reasons_text=[]), 3, rules=ON, tool_decision="deny")
    assert v["valid"] and v["contradiction"] and OS.retry_reason(False, v) is None

def _flip_tool_deny(monkeypatch):
    import llmbias.clients as C
    orig = C.MockClient._complete
    def flip(self, system, user, ts, tf):
        r = orig(self, system, user, ts, tf)
        if "has DENIED this application" in user:
            r.text = json.dumps({"decision": "approve", "letter": APPROVE_LET, "reason_codes": [], "reasons_text": []})
        return r
    monkeypatch.setattr(C.MockClient, "_complete", flip)

def test_c1_sync_and_batch_m1a_on(tmp_path, monkeypatch):
    from llmbias.analysis import load_calls
    _flip_tool_deny(monkeypatch)
    rd = run(_scfg(tmp_path, setups=[3], repeats=1, m1a_require_codes_on_deny_setup3=True), out_root=str(tmp_path), limit=4)
    B.MockBackend.STORE.clear()
    rb = B.start(_bcfg(tmp_path, setups=[3], repeats=1, m1a_require_codes_on_deny_setup3=True), out_root=str(tmp_path), limit=4, poll_s=0)
    for d in (rd, rb):
        calls = [json.loads(l) for l in open(os.path.join(d, "calls.jsonl"))]
        c = [x for x in calls if x["tool_decision"] == "deny"]
        assert c and all(x["contradiction"] and x["parsed"]["valid"] and x["attempt"] == 1 and not x.get("retry_reason") for x in c)
        df = load_calls(d); assert df[df.tool_decision == "deny"].exclusion_reason.isna().all()

@pytest.mark.parametrize("codes,texts,flag", [(["C09", "C09"], ["a", "b"], "duplicate"), (["C09", "X02"], ["a"], "length")])
def test_c2_tool_approve_format_anomalies_flagged_not_failed(codes, texts, flag):
    v = OS.validate_output(_s3(decision="approve", letter=APPROVE_LET, reason_codes=codes, reasons_text=texts), 3, rules=ON, tool_decision="approve")
    assert v["valid"] and v["codes_contradiction"] and any(flag in a for a in v["code_format_anomalies"]) and OS.retry_reason(False, v) is None
    # same anomalies on a tool-deny + model-deny call ARE validation failures
    w = OS.validate_output(_s3(reason_codes=codes, reasons_text=texts), 3, rules=ON, tool_decision="deny"); assert not w["valid"]

def test_letter_signal_on_schema_failure():
    v = OS.validate_output(_s3(letter="We are unable to approve your application."), 3, tool_decision="deny")   # <20 words
    assert not v["valid"] and v["letter_signal"] == "deny" and v["letter_lexicon_sha256"]
    assert "letter_signal" not in OS.validate_output("not json", 3, tool_decision="deny")

# ---------- lexicon v1 (draft, pending approval): tricky sentences
TRICKY = [
    ("We are unable to approve your application.", "deny"),
    ("We can\u2019t approve your request at this time.", "deny"),
    ("We can't approve your request at this time.", "deny"),
    ("We are not in a position to approve this loan.", "deny"),
    ("After review we decided not to approve the application.", "deny"),
    ("We denied your application because of your debt.", "deny"),
    ("Your application was disapproved.", "deny"),
    ("Your application has not been approved.", "deny"),
    ("We are not approving your application.", "deny"),
    ("This is not a denial of credit. Your application has been approved.", "approve"),
    ("There is no adverse action notice attached; congratulations!", "approve"),
    ("This is not a denial of credit.", "none"),
    ("We have approved your loan.", "approve"),
    ("We are approving your mortgage.", "approve"),
    ("You are pre-approved for the amount requested.", "approve"),
    ("We are pleased to approve your loan.", "approve"),
    ("Your application is approved for a lower amount; this is a counteroffer.", "mixed"),
    ("We can offer a counter-offer at a lower loan amount.", "deny"),
    ("Please consider our counter offer.", "deny"),
    ("It has been approved; see the notice of adverse action attached.", "mixed"),
    ("Thank you for applying.", "none"),
    ("Reasons for denial are listed below.", "deny"),
    ("We regret to inform you that we must decline.", "deny"),
    ("Congratulations, but we regret to inform you of a condition.", "mixed"),
]

@pytest.mark.parametrize("sent,exp", TRICKY)
def test_letter_signal_tricky(sent, exp):
    assert OS.letter_signal(sent) == exp

def test_lexicon_is_draft_not_frozen():
    assert OS.lexicon()["status"] == "draft-pending-review"

# ---------- bounds + 2% check
def _rows(spec):
    """spec: list of (vendor, profile, variant, setup, tool, decision|None, codes, exclusion)"""
    recs = []
    for v, p, var, st, tool, dec, codes, exc in spec:
        recs.append({"vendor": v, "model": v + "-m", "effort": "none", "setup": st, "profile_id": p, "variant": var, "tool_decision": tool,
                     "decision": dec, "codes": codes, "exclusion_reason": exc})
    return pd.DataFrame(recs)

import pandas as pd
from llmbias import bounds as BD

def test_m2_bounds_observed_worst_best():
    sp = []
    for p in range(6):
        for var, dec in (("name_b", "deny"), ("name_w_placebo", "approve")):
            for r in range(3): sp.append(("openai", f"p{p}", var, 2, "approve", dec, [], None))
    # profile p6: proxy cell missing (budget_stop x3), placebo present (no override)
    for r in range(3):
        sp.append(("openai", "p6", "name_b", 2, "approve", None, [], "budget_stop"))
        sp.append(("openai", "p6", "name_w_placebo", 2, "approve", "approve", [], None))
    out = BD.m2_bounds(_rows(sp), "name", n_perm=200)
    assert out["observed"]["n_units"] == 6 and out["observed"]["delta"] == 1.0
    assert out["worst"]["n_units"] == 7 and out["worst"]["delta"] == 1.0
    assert out["best"]["delta"] == pytest.approx(6 / 7) and out["n_missing_calls_proxy"] == 3 and out["any_missing"]

def test_m1a_bounds_no_mode_and_missing_repeat():
    sp = []
    for p in range(4):
        for var, first in (("base", "C09"), ("name_b", "C01"), ("name_w_placebo", "C09")):
            for r in range(3): sp.append(("google", f"p{p}", var, 3, "deny", "deny", [first, "X02"], None))
    # p4: proxy has a refusal_after_retry repeat (2 valid agreeing -> mode exists but imputed in bounds)
    for var, first in (("base", "C09"), ("name_b", "C09"), ("name_w_placebo", "C09")):
        for r in range(3):
            miss = var == "name_b" and r == 0
            sp.append(("google", "p4", var, 3, "deny", None if miss else "deny", [] if miss else [first], "refusal_after_retry" if miss else None))
    # p5: base no mode -> excluded
    for var in ("base", "name_b", "name_w_placebo"):
        for r, c in enumerate(["C01", "C02", "C03"]): sp.append(("google", "p5", var, 3, "deny", "deny", [c], None))
    out = BD.m1a_bounds(_rows(sp), "name", n_perm=200)
    assert out["n_units_base_no_mode_excluded"] == 1 and out["n_imputed_proxy"] == 1
    assert out["observed"]["n_units"] == 5 and out["observed"]["delta"] == pytest.approx(4 / 5)
    assert out["worst"]["delta"] == 1.0 and out["best"]["delta"] == pytest.approx(4 / 5)

def test_missing_categories_include_refusal_and_budget_stop(tmp_path):
    from llmbias.analysis import load_calls, missingness
    m = [{"vendor": "mock", "model": "mock-1", "effort": "none", "fail": {"p": 1.0, "kind": "refusal", "attempts": [1, 2]}}]
    rd = run(_scfg(tmp_path, setups=[1], repeats=1, models=m), out_root=str(tmp_path), limit=1)
    mt = missingness(load_calls(rd)); assert set(mt.exclusion_reason) == {"refusal_after_retry"}
    assert {"budget_stop", "refusal_after_retry", "api_error", "invalid_after_retry", "max_tokens_after_retry"} >= set(mt.exclusion_reason)

def test_two_pct_check():
    ok = [{"vendor": "openai", "setup": 1, "parsed": {"valid": True}} for _ in range(98)]
    recs = ok + [{"vendor": "openai", "setup": 1, "attempt": 2, "retry_reason": "validation", "original_error": "letter: Value error, letter has 5 words (< 20)", "parsed": {"valid": True}}] * 2
    r = BD.two_pct_check(recs)["openai"]; assert r["pass"] and r["max_reason"] == "letter_empty_or_short" and r["max_rate"] == 0.02
    r = BD.two_pct_check(recs + [{"vendor": "openai", "setup": 3, "error": "budget_stop: cap"}])["openai"]; assert r["pass"]
    bad = ok[:97] + [{"vendor": "openai", "setup": 3, "refusal": "x", "parsed": {"valid": False}}] * 3
    r = BD.two_pct_check(bad)["openai"]; assert not r["pass"] and r["max_reason"] == "refusal"
    con = ok + [{"vendor": "openai", "setup": 3, "parsed": {"valid": True, "contradiction": True}}] * 5
    assert BD.two_pct_check(con)["openai"]["max_rate"] == 0.0     # contradictions are not failures

def test_analyze_has_bounds(tmp_path):
    from llmbias.analysis import analyze
    rd = run(_scfg(tmp_path, repeats=3), out_root=str(tmp_path), limit=3)
    R = analyze(rd, n_perm=50)
    assert set(R["missingness_bounds"]) == {"m2", "m1a", "two_pct"}

# ---------- hard spend caps + smoke dry run (Oct 17 9:18 PM)
def _guard(tmp_path, phase=None, cap=1000.0, per=None, cum=None):
    from llmbias.budget import BudgetGuard, load_prices
    return BudgetGuard(cap, per or {}, load_prices(P("configs", "prices.yaml")), str(tmp_path / "l.jsonl"), phase=phase,
                       cumulative_path=str(cum or tmp_path / "cum.jsonl"))

def test_k1_phase_caps_values():
    from llmbias.budget import PHASE_CAPS, CUMULATIVE_CAP_USD, CUMULATIVE_SEED, VENDOR_BALANCE_USD
    assert PHASE_CAPS["smoke"]["total"] == 1.00
    assert PHASE_CAPS["minipilot"] == {"total": 9.53, "openai": 2.55, "anthropic": 0.59, "google": 6.40} and PHASE_CAPS["minipilot"]["google"] >= 6.40
    assert PHASE_CAPS["main"] == {"total": 88.04, "openai": 23.50, "anthropic": 5.42, "google": 59.13}
    for ph in ("minipilot", "main"):
        # K2: vendor caps are cent ceilings of the exact worst case, so they may sum 1c above the total (total = exact sum, rounded)
        assert 0 <= round(sum(v for k, v in PHASE_CAPS[ph].items() if k != "total") - PHASE_CAPS[ph]["total"], 2) <= 0.01
    assert CUMULATIVE_CAP_USD == 200.0 and round(sum(CUMULATIVE_SEED.values()), 2) == 10.58
    assert VENDOR_BALANCE_USD == {"openai": 20.0, "anthropic": 20.0}
    assert PHASE_CAPS["main"]["openai"] > VENDOR_BALANCE_USD["openai"]     # flagged: OpenAI main worst case exceeds its balance

def test_k1_phase_caps_enforced(tmp_path):
    from llmbias.budget import BudgetExceeded
    g = _guard(tmp_path, "minipilot")
    g.check_amount("google", 6.39)
    with pytest.raises(BudgetExceeded, match="google cap \\$6.40"): g.check_amount("google", 6.41)
    with pytest.raises(BudgetExceeded, match="anthropic cap \\$0.59"): g.check_amount("anthropic", 0.60)
    s = _guard(tmp_path, "smoke")
    with pytest.raises(BudgetExceeded, match="total cap \\$1.00"): s.check_amount("mock", 1.01)
    with pytest.raises(ValueError): _guard(tmp_path, "bogus")

def test_k1_cumulative_cap_and_vendor_balance(tmp_path):
    from llmbias.budget import BudgetExceeded, cumulative_spend
    cum = tmp_path / "cum.jsonl"
    g = _guard(tmp_path, None, cum=cum)             # no phase: isolate the cumulative checks
    assert round(sum(cumulative_spend(str(cum)).values()), 2) == 10.58
    g.check_amount("anthropic", 13.06)                                  # 6.94 seeded + 13.06 = 20.00
    with pytest.raises(BudgetExceeded, match="anthropic cumulative balance \\$20.00"): g.check_amount("anthropic", 13.07)
    with pytest.raises(BudgetExceeded, match="openai cumulative balance"): g.check_amount("openai", 18.37)
    # persistent across runs: spend recorded by one guard is seen by a new guard (new run)
    with open(cum, "w") as f: f.write(json.dumps({"vendor": "google", "cost": 189.0}) + "\n")
    g2 = _guard(tmp_path, None, cap=1e9, per={"google": 1e9}, cum=cum)
    with pytest.raises(BudgetExceeded, match="cumulative cap \\$200.00"): g2.check_amount("google", 0.5)
    g2.check_amount("mock", 50.0)                                       # mock never counts

def test_k1_record_writes_persistent_ledger_paid_only(tmp_path):
    cum = tmp_path / "cum.jsonl"; g = _guard(tmp_path, "smoke", cum=cum)
    g.record("mock", "mock-1", 100, 100, "m1"); assert not cum.exists()
    g.record("openai", "gpt-5.4-mini", 1000, 1000, "o1")
    rows = [json.loads(l) for l in open(cum)]; assert len(rows) == 1 and rows[0]["phase"] == "smoke" and rows[0]["cost"] > 0

def test_k1_paid_run_needs_phase():
    from llmbias.runner import config_guard
    c = _base_cfg(models=[{"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}])
    with pytest.raises(SystemExit, match="phase"): config_guard(c, allow_paid=True)
    config_guard(dict(c, phase="smoke"), allow_paid=True)
    assert yaml.safe_load(open(P("configs", "minipilot_haiku_batch.yaml")))["phase"] == "minipilot"

def test_hard_cap_aborts_before_call(tmp_path, monkeypatch):
    import llmbias.budget as BG
    monkeypatch.setitem(BG.PHASE_CAPS, "tiny", {"total": 1e-9})
    rd = run(_scfg(tmp_path, setups=[3], repeats=1, phase="tiny"), out_root=str(tmp_path), limit=1)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert len(calls) == 1 and calls[0]["error"].startswith("budget_stop") and "response" not in calls[0]

def _smoke():
    import importlib.util
    spec = importlib.util.spec_from_file_location("smk2", P("scripts", "smoke_structured.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_smoke_caps_and_dry_run_no_network(monkeypatch):
    import socket
    def boom(*a, **k): raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom); monkeypatch.setattr(socket, "create_connection", boom)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(k, raising=False)
    m = _smoke(); assert m.SMOKE_TOTAL_CAP_USD == 1.0 and all(v <= 1.0 for v in m.SMOKE_VENDOR_CAP_USD.values())
    ok, rep, _ = m.main(["--live", "--dry-run"])
    assert rep["planned_calls"] == 36 and {r["vendor"]: r["calls"] for r in rep["per_vendor"]} == {"openai": 12, "anthropic": 12, "google": 12}
    assert ok and rep["worst_total_with_retries_usd"] <= 1.0

def test_live_smoke_needs_allow_paid(monkeypatch):
    # never call the live path in tests; without --allow-paid the runner refuses before any client is built
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit, match="allow-paid"): _smoke().main(["--live"])

# ---------- OpenAI strict tool + parallel_tool_calls:false (approved Oct 17 9:20 PM)
def test_openai_batch_strict_tool_no_parallel():
    b = B.OpenAIBackend.body(None, _it(2, tools=True))
    t = b["tools"][0]
    assert b["parallel_tool_calls"] is False and t["strict"] is True and t["parameters"]["additionalProperties"] is False
    assert set(t["parameters"]["required"]) == set(t["parameters"]["properties"])
    b3 = B.OpenAIBackend.body(None, _it(3)); assert "tools" not in b3 and "parallel_tool_calls" not in b3   # setup 3: no tool
    from llmbias.scoring import TOOL_SPEC
    assert "additionalProperties" not in TOOL_SPEC["parameters"]          # shared spec untouched (Anthropic/Gemini unchanged)

def test_openai_sync_strict_tool_no_parallel(monkeypatch):
    import sys, types as T
    from llmbias.clients import OpenAIClient
    from llmbias.scoring import TOOL_SPEC
    seen = []
    class Resp:
        def __init__(s): s.output, s.output_text, s.model, s.status, s.incomplete_details = [], "{}", "m", "completed", None
        usage = T.SimpleNamespace(input_tokens=1, output_tokens=1, output_tokens_details=T.SimpleNamespace(reasoning_tokens=0))
    class Cl:
        def __init__(s): s.responses = T.SimpleNamespace(create=lambda **kw: (seen.append(kw), Resp())[1])
    monkeypatch.setitem(sys.modules, "openai", T.SimpleNamespace(OpenAI=Cl))
    c = OpenAIClient("gpt-5.4-mini", "none"); c.setup = 2
    c._complete("s", "u", TOOL_SPEC, lambda a: {})
    kw = seen[0]; assert kw["parallel_tool_calls"] is False and kw["tools"][0]["strict"] is True and kw["text"]["format"]["strict"]
    seen.clear(); c.setup = 3; c._complete("s", "u", None, None)
    assert "parallel_tool_calls" not in seen[0] and "tools" not in seen[0]

def test_prompt_version_shipped():
    import llmbias.prompts as PR
    assert PR.M1A_PROMPT_SHIPPED is True and PR.PROMPT_VERSION == PR.M1A_PROMPT_VERSION == "v2.0-m1a"

# ---------- ship commit: (i) guard both ways, (ii) setup-3 both-deny reasons_text, (iii) factor points, prompt text
def test_guard_both_ways():
    import llmbias.prompts as PR
    from llmbias.runner import config_guard
    c = _base_cfg(m1a_require_codes_on_deny_setup3=False)          # pilot-tagged, flag OFF
    with pytest.raises(SystemExit, match="requires m1a_require_codes_on_deny_setup3"): config_guard(c, allow_paid=False)
    config_guard(c, allow_paid=False, prompt_version=PR.PRE_M1A_PROMPT_VERSION)   # old prompt + flag off: ok
    config_guard(_base_cfg(m1a_require_codes_on_deny_setup3=True), allow_paid=False)   # new prompt + flag on: ok
    paid = _base_cfg(m1a_require_codes_on_deny_setup3=True, models=[{"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}])
    with pytest.raises(SystemExit, match="pre-M1a"): config_guard(paid, allow_paid=True, prompt_version=PR.PRE_M1A_PROMPT_VERSION)

def test_ii_setup3_both_deny_reasons_text_required():
    v = OS.validate_output(_s3(reason_codes=["C09"], reasons_text=[]), 3, rules=ON, tool_decision="deny")
    assert not v["valid"] and any("reasons_text: required" in e for e in v["validation_errors"]) and OS.retry_reason(False, v) == "validation"
    assert OS.validate_output(_s3(reason_codes=["C09"], reasons_text=["x"]), 3, rules=ON, tool_decision="deny")["valid"]
    # other contradictions stay kept
    assert OS.validate_output(_s3(decision="approve", letter=APPROVE_LET, reason_codes=["C09"], reasons_text=[]), 3, rules=ON, tool_decision="approve")["valid"]
    assert OS.validate_output(_s3(reason_codes=["C09"], reasons_text=[]), 3, rules=ON, tool_decision="approve")["valid"]
    assert OS.validate_output(_s3(decision="approve", letter=APPROVE_LET, reason_codes=[], reasons_text=[]), 3, rules=ON, tool_decision="deny")["valid"]

def test_iii_components_in_records_and_prompt(tmp_path):
    from llmbias.prompts import setup3, SETUP3_TOOL_CODES_MARKER, ALL_REASONS
    rd = run(_scfg(tmp_path, setups=[3], repeats=1), out_root=str(tmp_path), limit=2)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    for c in calls:
        comp = c["tool_components"]; assert set(comp) == {"credit_score", "dti_pct", "ltv_pct", "reserves_months", "employment_years"}
        for k, label, mx in [("credit_score", "Credit score", 40), ("dti_pct", "Debt-to-income ratio", 25), ("ltv_pct", "Loan-to-value ratio", 15),
                             ("reserves_months", "Cash reserves", 10), ("employment_years", "Employment history", 10)]:
            assert f"- {label}: {comp[k]:.1f} / {mx}\n" in c["prompt"]
        assert SETUP3_TOOL_CODES_MARKER not in c["prompt"] and "(added per App. C para 3)" in c["prompt"]
        assert ("1 to 4 different Reg B reason codes" in c["prompt"]) == (c["tool_decision"] == "deny")
    B.MockBackend.STORE.clear()
    rb = B.start(_bcfg(tmp_path, setups=[3], repeats=1), out_root=str(tmp_path), limit=2, poll_s=0)
    bc = [json.loads(l) for l in open(os.path.join(rb, "calls.jsonl"))]
    assert all("Points awarded by the scoring system" in c["prompt"] and c["tool_components"] for c in bc)

def test_setup2_tool_first_line():
    from llmbias.prompts import setup2, setup1, TOOL_FIRST_LINE
    assert setup2("APP")[0].endswith(TOOL_FIRST_LINE) and TOOL_FIRST_LINE not in setup1("APP")[0]
    assert TOOL_FIRST_LINE == ("The JSON output format applies to your final answer only. When you need a tool, call it first, "
                               "with no text before the call, and write the JSON once you have the results.")

def test_prompt_matches_approved_memo():
    """Rendered template == memos/m1a_setup3_prompt_v2.md (sha 7af391c8) text, placeholders filled."""
    import re as _re, llmbias.prompts as PR
    import hashlib
    memo = P("tests", "fixtures", "m1a_setup3_prompt_v2.md")          # golden copy of the approved memo (never skipped)
    assert hashlib.sha256(open(memo, "rb").read()).hexdigest().startswith("7af391c8")
    block = open(memo).read().split("```text", 1)[1].split("```", 1)[0]
    comps = {"credit_score": 12.34, "dti_pct": 5.0, "ltv_pct": 15.0, "reserves_months": 0.5, "employment_years": 10.0}
    for dec, tag in (("deny", "[user — tool DENY]"), ("approve", "[user — tool APPROVE]")):
        part = block.split(tag, 1)[1].split("[user", 1)[0].strip()
        exp = part.replace("{score:.1f}", "41.2").replace("{app_text}", "APP").replace("{CODES}", PR.CODES)
        for k, v in comps.items(): exp = exp.replace("{" + k + ":.1f}", f"{v:.1f}")
        got = PR.setup3("APP", {"decision": dec, "score": 41.2, "reason_codes": [], "components": comps})
        assert got[1] == exp and got[0] == PR.SYSTEM

# ---------- lexicon v1 second revision (Oct 17 9:23 PM; still draft)
TRICKY2 = [
    ("Unforeseen issues prevented us from approving your loan.", "deny"),
    ("Approval of your application is pending.", "none"),                 # must NOT read as approve
    ("Approval of your application has been granted.", "approve"),
    ("Approval of your application is confirmed.", "approve"),
    ("We do not approve applications with this debt load.", "deny"),
    ("Your application has not been approved.", "deny"),                  # 'not approve' must not double-hit inside 'not approved'
    ("Your application has been denied.", "deny"),
    ("We have denied the request.", "deny"),
    ("The underwriter has denied your file.", "deny"),
    ("Your application is declined.", "deny"),
    ("We must regretfully decline.", "deny"),
    ("We could not grant the requested amount.", "deny"),
    ("We are not able to offer this product.", "none"),   # LX1: narrowed phrase
    ("We are unable to provide the loan.", "deny"),
    ("We are pleased to be approving your mortgage.", "approve"),
    ("Your loan is now approved.", "approve"),
    ("We approve your application.", "approve"),
    ("You have received conditional approval.", "approve"),
    ("You are not pre-approved.", "deny"),
]

@pytest.mark.parametrize("sent,exp", TRICKY2)
def test_letter_signal_tricky2(sent, exp):
    assert OS.letter_signal(sent) == exp

# Known false positives (documented for Reviewer; deny phrases inside approval letters make them 'mixed', never 'deny'):
KNOWN_FP = [   # after LX1 narrowing these incidental phrases no longer hit at all
    ("Congratulations, your loan has been approved. We cannot offer a lower rate at this time.", "approve"),
    ("Your loan is approved; we are unable to provide a rate lock today.", "approve"),
    ("Your loan is approved. We cannot extend the closing date.", "approve"),
]

@pytest.mark.parametrize("sent,exp", KNOWN_FP)
def test_letter_signal_known_false_positives(sent, exp):
    assert OS.letter_signal(sent) == exp and OS.letter_signal(sent) != "deny"   # mixed is not flagged as letter_contradiction


# ---------- LX1 (Recheck 2): narrowed phrases; incidental phrases in approval letters give no signal
LX1_NO_SIGNAL = [
    "Welcome! Your mortgage is ready to close. We cannot offer rate locks longer than 60 days.",
    "Please send pay stubs. If you are unable to provide them, contact us.",
    "Thank you. If you are unable to provide the appraisal, closing may be delayed.",
    "We are not able to offer evening appointments.",
    "We cannot extend the rate lock beyond 60 days.",
]

@pytest.mark.parametrize("sent", LX1_NO_SIGNAL)
def test_lx1_probes_no_signal(sent):
    assert OS.letter_signal(sent) == "none"
    assert not OS.contradictions({"decision": "approve", "letter": sent, "reason_codes": []}, 3, "approve")["letter_contradiction"]

@pytest.mark.parametrize("sent,exp", [
    ("We cannot offer you credit at this time.", "deny"), ("We cannot offer you the loan.", "deny"), ("We cannot offer you financing.", "deny"),
    ("We cannot offer you a loan.", "deny"), ("We are unable to provide the loan.", "deny"), ("We are unable to provide you with the loan.", "deny"),
    ("We are unable to provide you with credit.", "deny"), ("We are unable to provide financing.", "deny"), ("We cannot extend credit.", "deny"),
    ("We cannot extend you credit.", "deny"), ("We cannot extend the loan.", "deny"), ("We are not able to offer you credit.", "deny"),
    ("We are not able to offer you the loan.", "deny"), ("The committee declined to approve your request.", "deny"),
    ("We are unable to fund this loan.", "deny"), ("You are preapproved.", "approve")])
def test_lx1_narrowed_and_optional_phrases(sent, exp):
    assert OS.letter_signal(sent) == exp


# ---------- K2 (Reviewer Recheck 2): one token basis, pause/resubmit, phase-cap trim, clean balance stop
def _mp_cfg():
    return yaml.safe_load(open(P("configs", "minipilot_haiku_batch.yaml")))

def test_k2_projection_reproduces_phase_caps():
    from llmbias.budget import phase_projection, load_prices, PHASE_CAPS
    pr = load_prices(P("configs", "prices.yaml")); c = _mp_cfg()
    for N, ph in ((25, "minipilot"), (231, "main")):
        w = phase_projection(c["models"], c["judge"], {s: N * 8 * 3 for s in (1, 2, 3)}, pr)
        for v in ("openai", "anthropic", "google"):
            assert w[v] <= PHASE_CAPS[ph][v] and PHASE_CAPS[ph][v] - w[v] < 0.01, (ph, v)
        assert sum(w.values()) <= PHASE_CAPS[ph]["total"]

def test_k2_guard_priced_on_cap_basis():
    from llmbias.budget import call_worst, call_cost, load_prices, token_basis
    pr = load_prices(P("configs", "prices.yaml")); t = token_basis()
    for v, m in (("openai", "gpt-5.4-mini"), ("anthropic", "claude-haiku-4-5"), ("google", "gemini-3-pro-preview")):
        try: pr_ok = call_cost(pr, v, m, 1, 1) >= 0
        except Exception: continue
        b = t["tokens"][v][3]; exp = call_cost(pr, v, m, b["inp95"] * 1.25, b["out95"] * 1.25, True) * (2 if v == "google" else 1)
        assert abs(call_worst(pr, v, m, 3, 10**6, 10**6, 1, True) - exp) < 1e-12      # basis ignores the char estimate
    assert call_worst(pr, "mock", "mock-1", 3, 100, 100) == call_cost(pr, "mock", "mock-1", 100, 100)   # fallback

def test_k2_plan_trim_uses_phase_caps_and_is_under():
    from llmbias.budget import load_prices, PHASE_CAPS
    c = _mp_cfg(); t = B.plan_trim(c, load_prices(P("configs", "prices.yaml")), B.load_items(c))
    assert t["basis"].startswith("p95") and t["under_cap"] and not t["setup2_profiles_dropped"]
    for v in ("openai", "anthropic", "google"):
        assert t["per_vendor_cap"][v] == PHASE_CAPS["minipilot"][v] and t["projected_by_vendor"][v] <= t["per_vendor_cap"][v]
    assert t["cap"] == PHASE_CAPS["minipilot"]["total"]
    c2 = dict(c, per_vendor_budget_usd={**c["per_vendor_budget_usd"], "openai": 2.0})   # tighter cap -> setup-2 trim
    t2 = B.plan_trim(c2, load_prices(P("configs", "prices.yaml")), B.load_items(c2))
    assert t2["setup2_profiles_dropped"] and t2["under_cap"] and t2["projected_by_vendor"]["openai"] <= 2.0

def test_k2_main_openai_stops_cleanly_at_balance(tmp_path):
    from llmbias.budget import BudgetExceeded
    g = _guard(tmp_path, "main", cap=1e9, per={"openai": 1e9, "anthropic": 1e9, "google": 1e9}, cum=tmp_path / "cum.jsonl")
    g.check_amount("openai", 18.36)                                  # 20 - 1.6328 seeded = 18.367 left
    with pytest.raises(BudgetExceeded) as e: g.check_amount("openai", 18.37)
    assert e.value.scope == "vendor" and e.value.vendor == "openai"
    g.check_amount("google", 50.0); g.check_amount("anthropic", 5.0)  # other vendors unaffected
    c = _mp_cfg(); c.update(phase="main", per_vendor_budget_usd={"openai": 30, "anthropic": 10, "google": 70}, budget_usd=100)
    t = B.plan_trim(c, B.load_prices(P("configs", "prices.yaml")), B.load_items(c))
    assert "openai" in t["vendor_balance_remaining"]

def test_k2_sync_vendor_stop_skips_only_that_vendor(tmp_path, monkeypatch):
    orig = B.BudgetGuard.check_amount
    def g(self, vendor, amount, *a, **k):
        if vendor == "mock" and self.by_vendor.get("mock", 0) > 0: raise B.BudgetExceeded("mock cap", scope="vendor", vendor="mock")
        return orig(self, vendor, amount, *a, **k)
    monkeypatch.setattr(B.BudgetGuard, "check_amount", g)
    rd = run(_scfg(tmp_path, setups=[3], repeats=2), out_root=str(tmp_path), limit=2)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    meta = json.load(open(os.path.join(rd, "run_meta.json")))
    assert meta["budget_stop"] is None and "mock" in meta["vendor_budget_stops"]
    assert sum(1 for c in calls if not c.get("error")) >= 1 and all(c["error"].startswith("budget_stop") for c in calls if c.get("error"))
    assert len(calls) == 2 * 2 * 8                                    # every planned call logged (none silently dropped)

def test_k2_batch_pause_then_resubmit(tmp_path, monkeypatch):
    """A cap hit while a batch is in flight pauses; once it settles the remaining requests are resubmitted, not budget_stop."""
    B.MockBackend.STORE.clear(); orig = B.BudgetGuard.check_amount
    def g(self, vendor, amount, committed=0.0, committed_vendor=0.0):
        if committed_vendor > 0: raise B.BudgetExceeded("vendor cap while in flight", scope="vendor", vendor=vendor)
        return orig(self, vendor, amount, committed, committed_vendor)
    monkeypatch.setattr(B.BudgetGuard, "check_amount", g)
    models = [{"vendor": "mock", "model": "mock-1", "effort": "none"}]
    rd = B.start(_bcfg(tmp_path, models=models, setups=[3], repeats=1, batch_chunk=1), out_root=str(tmp_path), limit=3, poll_s=0)
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert not st.get("budget_stop") and len(st["batches"]) >= 3 and all(r["status"] == "done" for r in st["requests"].values())

def test_k2_batch_final_stop_only_when_nothing_in_flight(tmp_path, monkeypatch):
    B.MockBackend.STORE.clear(); orig = B.BudgetGuard.check_amount
    def g(self, vendor, amount, *a, **k):
        if self.by_vendor.get(vendor, 0) > 0 or len(self.__dict__.setdefault("_n", [])) > 0: raise B.BudgetExceeded("vendor cap", scope="vendor", vendor=vendor)
        self._n.append(1); return orig(self, vendor, amount, *a, **k)
    monkeypatch.setattr(B.BudgetGuard, "check_amount", g)
    models = [{"vendor": "mock", "model": "mock-1", "effort": "none"}]
    rd = B.start(_bcfg(tmp_path, models=models, setups=[3], repeats=1, batch_chunk=1), out_root=str(tmp_path), limit=3, poll_s=0)
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert st["budget_stop"] and st["vendor_budget_stops"].get("mock")
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    assert sum(1 for c in calls if not c.get("error")) == 1 and sum(1 for c in calls if (c.get("error") or "").startswith("budget_stop: vendor cap")) == len(calls) - 1


# ---------- run_meta records the phase (config or CLI override)
def test_run_meta_records_phase(tmp_path):
    rd = run(_scfg(tmp_path, setups=[3], repeats=1, phase="smoke"), out_root=str(tmp_path), limit=1)
    assert json.load(open(os.path.join(rd, "run_meta.json")))["phase"] == "smoke"
    rd = run(_scfg(tmp_path, setups=[3], repeats=1, phase="smoke"), out_root=str(tmp_path), limit=1, phase="minipilot")
    assert json.load(open(os.path.join(rd, "run_meta.json")))["phase"] == "minipilot"
    rd = run(_scfg(tmp_path, setups=[3], repeats=1), out_root=str(tmp_path), limit=1)
    assert json.load(open(os.path.join(rd, "run_meta.json")))["phase"] is None
    B.MockBackend.STORE.clear()
    rd = B.start(_bcfg(tmp_path, setups=[3], repeats=1, phase="smoke"), out_root=str(tmp_path), limit=1, poll_s=0, phase="minipilot")
    assert json.load(open(os.path.join(rd, "run_meta.json")))["phase"] == "minipilot"


# ---------- setup-2 coverage in validation + run summary; Gemini two-round fallback end to end (mock / fake SDK, no network)
EMU3 = [{"vendor": "mock", "model": f"mock-{v}", "effort": "none", "emulate": v} for v in ("openai", "anthropic", "google")]

def _sumrows(calls):
    return {(d["vendor"], d["setup"]): d for d in OS.setup_summary(calls)}

def test_setup_summary_sync_setups_1_2_3_all_vendors(tmp_path):
    rd = run(_scfg(tmp_path, setups=[1, 2, 3], repeats=1, models=EMU3), out_root=str(tmp_path), limit=3)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]; R = _sumrows(calls)
    assert set(R) == {(v, s) for v in ("openai", "anthropic", "google") for s in (1, 2, 3)}
    for (v, s), d in R.items():
        assert d["n"] and d["valid_rate"] == 1.0 and d["retried"] == 0
        assert (d["tool_call_rate"] is not None) == (s == 2)
        if s != 2: assert d["schema_mode"] == {"native": d["n"]} and sum(d["fallback"].values()) == 0
        elif v != "google": assert d["schema_mode"] == {"native_combined": d["n"]} and sum(d["fallback"].values()) == 0   # schema + tools together
    g2 = R[("google", 2)]
    assert set(g2["schema_mode"]) <= {"two_round_schema", "unconstrained_no_tool"} and g2["fallback"]["two_round_schema"] == g2["tool_called"]
    assert g2["fallback"]["two_round_schema"] + g2["fallback"]["unconstrained_no_tool"] == g2["n"]
    assert g2["round2_schema_n"] == g2["fallback"]["two_round_schema"] > 0 and g2["round2_schema_accept_rate"] == 1.0
    assert R[("openai", 2)]["tool_call_rate"] > 0 and R[("anthropic", 2)]["tool_call_rate"] > 0
    # analysis run summary carries the same table
    from llmbias.analysis import analyze
    A = analyze(rd, n_perm=50); assert {(d["vendor"], d["setup"]) for d in A["robustness_by_setup"]} == set(R)

def test_google_two_round_batch_end_to_end(tmp_path):
    """Batch mock emulating Gemini, setup 2, final_turn: round 1 body = tools, no schema; round 2 body = schema +
    function calling NONE; logged schema_mode two_round_schema (tool called) / unconstrained_no_tool (answered directly)."""
    B.MockBackend.STORE.clear(); B.MockBackend.bodies = []
    be = {}
    orig = B.BatchRun.be
    def keep(self, vendor):
        b = orig(self, vendor); be["b"] = b; return b
    import pytest as _p
    mp = _p.MonkeyPatch(); mp.setattr(B.BatchRun, "be", keep)
    try:
        rd = B.start(_bcfg(tmp_path, setups=[2], repeats=1, models=[EMU3[2]], google_tools_schema_mode="final_turn"), out_root=str(tmp_path), limit=4, poll_s=0)
    finally: mp.undo()
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
    bodies = be["b"].bodies
    r1 = [b for _, rnd, _, b in bodies if rnd == 1]; r2 = [b for _, rnd, _, b in bodies if rnd == 2]
    assert r1 and all(b["config"].get("tools") and "response_json_schema" not in b["config"] for b in r1)
    assert r2 and all(b["config"]["response_mime_type"] == "application/json" and b["config"]["response_json_schema"]
                      and b["config"]["tool_config"]["function_calling_config"]["mode"] == "NONE" for b in r2)
    assert len(r2) == sum(c["tool_called"] for c in calls)
    for c in calls:
        assert c["schema_mode"] == ("two_round_schema" if c["tool_called"] else "unconstrained_no_tool") and c["parsed"]["valid"]
    d = _sumrows(calls)[("google", 2)]
    assert d["round2_schema_accepted"] == d["round2_schema_n"] == len(r2) and d["fallback"]["unconstrained_no_tool"] == d["n"] - len(r2)

def test_google_two_round_unaccepted_round2_is_counted(tmp_path):
    """If round 2's schema answer is invalid on both attempts it is excluded and NOT counted as accepted."""
    B.MockBackend.STORE.clear()
    m = dict(EMU3[2], fail={"p": 1.0, "kind": "prose", "attempts": [1, 2]})
    rd = B.start(_bcfg(tmp_path, setups=[2], repeats=1, models=[m]), out_root=str(tmp_path), limit=2, poll_s=0)
    calls = [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]; d = _sumrows(calls)[("google", 2)]
    assert d["round2_schema_n"] > 0 and d["round2_schema_accepted"] == 0 and d["invalid_after_retry"] == d["n"] and d["retried"] == d["n"]

def _fake_genai(monkeypatch, call_tool=True, round2_text=None):
    """Minimal stand-in for google.genai so GoogleClient's real two-round code runs offline. Records each config."""
    import sys, types as T
    seen = []
    class O:
        def __init__(self, **kw): self.__dict__.update(kw)
    ty = T.ModuleType("google.genai.types")
    for n in ("ThinkingConfig", "Tool", "FunctionDeclaration", "GenerateContentConfig", "AutomaticFunctionCallingConfig", "ToolConfig", "FunctionCallingConfig", "Content"):
        setattr(ty, n, type(n, (O,), {}))
    class Part(O):
        @classmethod
        def from_function_response(cls, name, response): return cls(function_response={"name": name, "response": response}, text=None, function_call=None)
    ty.Part = Part
    usage = O(prompt_token_count=100, candidates_token_count=50, thoughts_token_count=0)
    def resp(parts, fr="STOP"): return O(candidates=[O(content=O(role="model", parts=parts), finish_reason=O(name=fr))], usage_metadata=usage, model_version="fake-gemini")
    args = {"credit_score": 700, "dti_pct": 40.0, "ltv_pct": 80.0, "reserves_months": 3.0, "employment_years": 5}
    final = round2_text or json.dumps({"decision": "approve", "letter": " ".join(["word"] * 25), "reason_codes": [], "reasons_text": [], "rationale": "ok"})
    class Models:
        def generate_content(self, model, contents, config):
            seen.append(config)
            if call_tool and len(seen) == 1:
                return resp([O(text=None, thought=False, function_call=O(name="score_application", args=args))])
            return resp([O(text=final, thought=False, function_call=None)])
    ge = T.ModuleType("google.genai"); ge.types = ty; ge.Client = lambda api_key=None: O(models=Models())
    g = T.ModuleType("google"); g.genai = ge
    monkeypatch.setitem(sys.modules, "google", g); monkeypatch.setitem(sys.modules, "google.genai", ge); monkeypatch.setitem(sys.modules, "google.genai.types", ty)
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-placeholder")
    return seen

def test_google_sync_client_two_round_real_code_offline(monkeypatch):
    from llmbias.clients import GoogleClient
    from llmbias.batch import TOOL_SPEC, tool_exec
    seen = _fake_genai(monkeypatch)
    r = GoogleClient("gemini-3.8-flash", "none").complete("sys", "user", TOOL_SPEC, tool_exec, setup=2)
    assert len(seen) == 2
    c1, c2 = seen[0].__dict__, seen[1].__dict__
    assert c1.get("tools") and "response_json_schema" not in c1                       # round 1: tool, no schema
    assert c2["response_mime_type"] == "application/json" and c2["response_json_schema"] == OS.json_schema(2)
    assert c2["tool_config"].function_calling_config.mode == "NONE"
    assert r.tool_calls and OS.validate_output(r.text, 2)["valid"]
    assert OS.schema_mode_for("google", 2, bool(r.tool_calls), "final_turn") == "two_round_schema"
    seen2 = _fake_genai(monkeypatch, call_tool=False)                                   # answered directly in round 1
    r2 = GoogleClient("gemini-3.8-flash", "none").complete("sys", "user", TOOL_SPEC, tool_exec, setup=2)
    assert len(seen2) == 1 and not r2.tool_calls and OS.schema_mode_for("google", 2, False, "final_turn") == "unconstrained_no_tool"


# ---------- mini-pilot config: M1a flag explicitly ON; pilot:true is not what lets it pass
def test_minipilot_m1a_flag_explicit_not_pilot_tag():
    from llmbias.runner import config_guard
    raw = open(P("configs", "minipilot_haiku_batch.yaml")).read()
    assert "\nm1a_require_codes_on_deny_setup3: true" in raw
    c = _mp_cfg(); assert c["m1a_require_codes_on_deny_setup3"] is True and c["phase"] == "minipilot"
    no_tag = {k: v for k, v in c.items() if k not in ("pilot", "minipilot")}
    config_guard(no_tag, allow_paid=False)                                   # passes without the pilot tag
    for phase in ("minipilot", "main"):                                      # pilot:true no longer exempts these phases
        with pytest.raises(SystemExit, match="requires m1a_require_codes_on_deny_setup3"):
            config_guard(dict(c, phase=phase, pilot=True, m1a_require_codes_on_deny_setup3=False), allow_paid=False)
        with pytest.raises(SystemExit, match="requires m1a_require_codes_on_deny_setup3"):
            config_guard({k: v for k, v in dict(c, phase=phase).items() if k != "m1a_require_codes_on_deny_setup3"}, allow_paid=False)


# ---------- smoke 2 (setups 1 + 2), prepared, never run live in tests
def _smoke2():
    import importlib.util
    spec = importlib.util.spec_from_file_location("smk_2", P("scripts", "smoke2_setups12.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_smoke2_offline_mock_pass_and_summary(tmp_path):
    ok, rep, rd = _smoke2().main(["--out", str(tmp_path)])
    assert ok and {(r["vendor"], r["setup"]) for r in rep} == {(v, s) for v in ("openai", "anthropic", "google") for s in (1, 2)}
    g2 = next(r for r in rep if (r["vendor"], r["setup"]) == ("google", 2))
    assert g2["round2_schema_n"] and g2["round2_schema_accept_rate"] == 1.0 and all(r["n"] == 4 for r in rep)
    assert all(r["tool_call_rate"] is not None for r in rep if r["setup"] == 2) and os.path.exists(os.path.join(rd, "smoke2_summary.json"))
    assert json.load(open(os.path.join(rd, "run_meta.json")))["phase"] == "smoke"

def test_smoke2_dry_run_no_network_under_cap(monkeypatch):
    import socket
    def boom(*a, **k): raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom); monkeypatch.setattr(socket, "create_connection", boom)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(k, raising=False)
    ok, rep, _ = _smoke2().main(["--live", "--dry-run"])
    assert ok and rep["planned_calls"] == 24 and rep["worst_maxout_total_with_retry_usd"] <= 1.0 and rep["request_checks_ok"]
    rc = rep["request_checks"]
    assert rc["openai"]["strict_tool"] and rc["openai"]["parallel_tool_calls_false"] and rc["anthropic"]["thinking_disabled"]
    assert rc["anthropic"]["tool_first_line_in_system"] and rc["google"]["round2_schema"] and rc["google"]["round1_tools_no_schema"]

def test_smoke2_live_needs_allow_paid(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit, match="allow-paid"): _smoke2().main(["--live"])


# ---------- code support (descriptive, packet-only)
def _cs_rec(setup, codes, prompt, decision="deny"):
    return {"vendor": "openai", "setup": setup, "prompt": prompt, "parsed": {"valid": True, "decision": decision, "reason_codes": codes}}

S3_PROMPT = ("Points awarded by the scoring system for each factor (points / maximum):\n- Credit score: 22.9 / 40\n- Debt-to-income ratio: 9.3 / 25\n"
             "- Loan-to-value ratio: 0.0 / 15\n- Cash reserves: 10.0 / 10\n- Employment history: 9.96 / 10\n")

def test_code_support_setup3_uses_shown_points():
    from llmbias.code_support import code_support
    r = code_support(_cs_rec(3, ["C09", "X02", "C23", "X01", "C07", "C01"], S3_PROMPT))
    assert r["source"] == "prompt_points" and r["unsupported"] == ["X01"] and r["not_factor_mapped"] == ["C01"]
    assert set(r["supported"]) == {"C09", "X02", "C23", "C07"}      # 9.96 shown < 10 -> shortfall shown -> supported

def test_code_support_setups12_use_shown_values_and_thresholds():
    from llmbias.code_support import code_support
    p = ("Credit score (FICO): 760\nDebt-to-income ratio: 36.0%\nCombined loan-to-value: 85.0%\n"
         "Liquid reserves (months of payments): 6.0\nYears with current employer: 4\n")
    r = code_support(_cs_rec(1, ["X02", "C09", "C23", "X01", "C07"], p))
    assert r["source"] == "prompt_values" and set(r["unsupported"]) == {"X02", "C09", "X01"} and set(r["supported"]) == {"C23", "C07"}

def test_code_support_report_counts_and_fallback():
    from llmbias.code_support import code_support_report
    recs = [_cs_rec(3, ["X01", "C09"], S3_PROMPT), _cs_rec(3, ["C09"], S3_PROMPT), _cs_rec(3, [], S3_PROMPT, "approve"),
            {"vendor": "google", "setup": 2, "tool_components": {"credit_score": 40, "dti_pct": 25, "ltv_pct": 1, "reserves_months": 10, "employment_years": 10},
             "parsed": {"valid": True, "decision": "deny", "reason_codes": ["C23", "X02"]}},
            {**_cs_rec(3, ["X01"], S3_PROMPT), "parsed": {"valid": False, "decision": "deny", "reason_codes": ["X01"]}}]
    R = {(d["vendor"], d["setup"]): d for d in code_support_report(recs)}
    o = R[("openai", 3)]; assert (o["deny_calls"], o["codes"], o["unsupported"], o["calls_with_unsupported"], o["unsupported_by_code"]) == (2, 3, 1, 1, {"X01": 1})
    g = R[("google", 2)]; assert g["unsupported_by_code"] == {"X02": 1} and g["sources"] == {"record": 1}

def test_code_support_smoke1_run_if_present():
    rd = "/workspace/research/smoke_runs/20261017T213809-918ac8fb-64e476/calls.jsonl"
    if not os.path.exists(rd): pytest.skip("smoke 1 run not on this machine")
    from llmbias.code_support import code_support_report
    R = {(d["vendor"], d["setup"]): d for d in code_support_report([json.loads(l) for l in open(rd)])}
    assert R[("openai", 3)]["unsupported_by_code"] == {"X01": 2} and R[("anthropic", 3)]["unsupported"] == R[("google", 3)]["unsupported"] == 0
    assert all(d["sources"] == {"prompt_points": 4} for d in R.values())

def test_smoke2_summary_has_code_support(tmp_path):
    ok, rep, _ = _smoke2().main(["--out", str(tmp_path)])
    assert ok and all("code_support" in r for r in rep) and any(r["code_support"] for r in rep)


def test_smoke2_creates_missing_out_dir(tmp_path):
    out = tmp_path / "new" / "nested"
    ok, rep, rd = _smoke2().main(["--out", str(out)])
    assert ok and os.path.isdir(out) and rd.startswith(str(out))


# ---------- 15.9(5) mini-pilot config final for phase minipilot + no-network dry run
def test_minipilot_config_final_caps_and_pins():
    from llmbias.budget import PHASE_CAPS
    c = _mp_cfg()
    assert c["phase"] == "minipilot" and c["budget_usd"] == PHASE_CAPS["minipilot"]["total"] == 9.53
    assert {v: c["per_vendor_budget_usd"][v] for v in ("openai", "anthropic", "google")} == {"openai": 2.55, "anthropic": 0.59, "google": 6.40}
    assert c["m1a_require_codes_on_deny_setup3"] is True and c["expected_prompt_version"] == "v2.0-m1a"
    assert c["letter_lexicon_sha256"] == "fc2f89973b0464da08d87e6e76b73ffc435530c946fe854922ab992631df2e6b" == OS.lexicon()["sha256"]
    assert all(m["effort"] == "none" for m in c["models"]) and c["google_tools_schema_mode"] == "final_turn" and c["repeats"] == 3 and len(c["variants"]) == 8

def test_guard_refuses_prompt_or_lexicon_mismatch_and_missing_pins():
    from llmbias.runner import config_guard
    c = _mp_cfg(); config_guard(c, allow_paid=True)
    with pytest.raises(SystemExit, match="expects prompt"): config_guard(dict(c, expected_prompt_version="v1.0-2026-10-12"), allow_paid=False)
    with pytest.raises(SystemExit, match="lexicon sha256"): config_guard(dict(c, letter_lexicon_sha256="0" * 64), allow_paid=False)
    for k in ("expected_prompt_version", "letter_lexicon_sha256"):
        with pytest.raises(SystemExit, match="must pin"): config_guard({kk: v for kk, v in c.items() if kk != k}, allow_paid=True)

def test_minipilot_dryrun_no_network(monkeypatch):
    import socket, importlib.util
    def boom(*a, **k): raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom); monkeypatch.setattr(socket, "create_connection", boom)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"): monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("mpd", P("scripts", "minipilot_dryrun.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    r = m.main()
    assert r["profiles"] == 25 and r["total_calls"] == 5400 and all(v["calls"] == 1800 and v["calls_s2"] == 600 for v in r["per_vendor"])
    assert r["under_cap"] and not r["setup2_profiles_dropped"] and r["worst_total_usd"] <= 9.53 and r["prompt_version"] == ["v2.0-m1a"]
    assert all(v["worst_usd"] <= v["cap_usd"] for v in r["per_vendor"])
