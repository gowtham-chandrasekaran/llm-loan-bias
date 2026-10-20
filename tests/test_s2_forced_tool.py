"""15.9.1 (memos/draft_15.9.1.md, logged sha256 5041167f...): forced round-1 tool call in setup 2 (item 1), setup-2
compliance = 100% after the retry or the run stops (item 3), phase caps (items 4/7), probe + analysis exclusion (item 7)."""
import json, math, os, pytest, yaml
from llmbias.build_data import P
from llmbias import batch as B, output_schema as OS, probe_s2 as PR
from llmbias.runner import run, load_items
from llmbias.budget import PHASE_CAPS, S2_CORE_P95, load_prices, call_cost, phase_projection, token_basis

EMU3 = [{"vendor": "mock", "model": f"mock-{v}", "effort": "none", "emulate": v} for v in ("openai", "anthropic", "google")]

def _cfg(tmp_path, base="pilot_mock.yaml", name="s.yaml", **over):
    cfg = yaml.safe_load(open(P("configs", base))); cfg.update(over)
    p = tmp_path / name; yaml.safe_dump(cfg, open(p, "w")); return str(p)

def _calls(rd): return [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
def _meta(rd): return json.load(open(os.path.join(rd, "run_meta.json")))

# ---------------------------------------------------------------- item 1: request fragments
def test_forced_tool_fragments_exact():
    assert OS.openai_tool_choice() == {"type": "function", "name": "score_application"}
    assert OS.anthropic_tool_choice() == {"type": "tool", "name": "score_application"}
    assert OS.google_forced_tool_config() == {"function_calling_config": {"mode": "ANY", "allowed_function_names": ["score_application"]}}
    assert OS.MAX_TOOL_CALLS_PER_CALL == 1

def _it(round_, attempt=1, setup=2, effort="none"):
    return {"model": "m", "system": "s", "user": "u", "max_out": 100, "effort": effort, "tools": setup == 2, "history": None,
            "round": round_, "setup": setup, "attempt": attempt, "google_mode": "final_turn"}

@pytest.mark.parametrize("vendor", ["openai", "anthropic", "google"])
def test_batch_bodies_round1_forced_round2_not(vendor):
    body = B.BACKENDS[vendor].body
    r1, r2, s1, s3 = body(None, _it(1)), body(None, _it(2)), body(None, _it(1, setup=1)), body(None, _it(1, setup=3))
    r1b = body(None, _it(1, attempt=2))                     # the single retry restarts at round 1: forced again
    if vendor == "google":
        assert r1["config"]["tool_config"] == OS.google_forced_tool_config() == r1b["config"]["tool_config"]
        assert r2["config"]["tool_config"] == {"function_calling_config": {"mode": "NONE"}} and r2["config"]["response_json_schema"]
        assert "tool_config" not in s1["config"] and "tool_config" not in s3["config"]
    else:
        tc = OS.openai_tool_choice() if vendor == "openai" else OS.anthropic_tool_choice()
        assert r1["tool_choice"] == tc == r1b["tool_choice"] and "tool_choice" not in r2
        assert "tool_choice" not in s1 and "tool_choice" not in s3
    if vendor == "openai": assert r1["parallel_tool_calls"] is False and r1["tools"][0]["strict"] is True
    if vendor == "anthropic": assert r1["thinking"] == {"type": "disabled"}       # forced tool_choice is valid only without thinking
    if vendor == "anthropic":
        with pytest.raises(ValueError): body(None, _it(1, effort="low"))           # thinking can never be enabled (15.9 (j))

# ---------------------------------------------------------------- item 2: phase caps
def test_phase_caps_15_9_1():
    assert PHASE_CAPS["minipilot_s2_probe"] == {"total": 0.15}
    c = PHASE_CAPS["minipilot_s2_retest"]
    assert c == {"total": 3.88, "openai": 1.24, "anthropic": 0.39, "google": 2.25} and round(c["openai"] + c["anthropic"] + c["google"], 2) == c["total"]

def test_retest_caps_reproduced_on_cap_basis():
    """Same basis as the other caps (p95 x1.25, Google x2, +5% reruns, judge x3, cent ceiling); per vendor the larger of the
    pilot token basis and the core run's setup-2 actuals; 600 setup-2 calls per vendor, judge on 1,800 letters."""
    pr = load_prices(P("configs", "prices.yaml")); cfg = yaml.safe_load(open(P("configs", "minipilot_haiku_batch.yaml")))
    b = token_basis(); base = phase_projection(cfg["models"], cfg["judge"], {2: 600}, pr)
    judge = 1800 * call_cost(pr, "openai", "gpt-5-nano", b["judge_per_item"]["input"], b["judge_per_item"]["output"], True) * b["worst_judge_multiplier"]
    for m in cfg["models"]:
        v = m["vendor"]; t = S2_CORE_P95[v]
        core = 600 * call_cost(pr, v, m["model"], t["inp95"] * 1.25, t["out95"] * 1.25, True) * (2 if v == "google" else 1) * 1.05 + (judge if v == "openai" else 0)
        assert math.ceil(round(max(base[v], core) * 100, 6)) / 100 == PHASE_CAPS["minipilot_s2_retest"][v]

def test_probe_cost_estimate_under_cap():
    cfg = yaml.safe_load(open(PR.CFG)); est = PR.cost_estimate(cfg, load_prices(P("configs", "prices.yaml")))
    assert est["calls_total"] == 12 and est["api_requests_total"] == 24 and est["under_cap"] and est["cap_usd"] == 0.15
    assert est["worst_if_every_call_retried_usd"] <= 0.15

# ---------------------------------------------------------------- item 3: compliance
def test_s2_compliance_reasons():
    tc = [{"name": "score_application", "args": {}, "result": {}}]
    blank = {"letter": "", "valid": False}; full = {"letter": "Dear applicant ...", "valid": True}
    assert OS.s2_compliance({"setup": 2, "tool_calls": tc, "parsed": full}) is None
    assert OS.BLANK_LETTER == "blank_letter"
    assert OS.s2_compliance({"setup": 2, "tool_calls": tc, "parsed": blank}) == "blank_letter"       # correct call, empty letter
    assert OS.s2_compliance({"setup": 2, "tool_calls": tc, "response": "", "parsed": {"valid": False}}) == "blank_letter"  # empty text
    assert OS.s2_compliance({"setup": 2, "tool_calls": tc, "response": "  \n", "parsed": full}) == "blank_letter"
    assert OS.s2_compliance({"setup": 2, "tool_calls": [], "parsed": blank}) == "no_tool_call"       # tool rule outranks blank
    assert OS.s2_compliance({"setup": 2, "tool_calls": tc * 2, "response": "", "parsed": blank}) == "multiple_tool_calls"
    assert OS.s2_compliance({"setup": 2, "tool_calls": [], "parsed": full}) == "no_tool_call"
    assert OS.s2_compliance({"setup": 2, "tool_calls": [], "parsed": {"valid": False, "errors": ["no_json"]}}) == "no_tool_call"
    assert OS.s2_compliance({"setup": 2, "tool_calls": tc * 2, "parsed": full}) == "multiple_tool_calls"
    assert OS.s2_compliance({"setup": 2, "tool_calls": [{"name": "other"}], "parsed": full}) == "wrong_tool"
    assert OS.s2_compliance({"setup": 2, "tool_calls": [], "error": "api_error: x"}) is None      # missingness, not compliance
    assert OS.s2_compliance({"setup": 1, "tool_calls": [], "parsed": blank}) is None

def _models(kind, attempts):
    return [dict(m, fail={"p": 1.0, "kind": kind, "attempts": attempts}) for m in EMU3[:1]]

def test_sync_blank_letter_retried_then_compliant(tmp_path):
    rd = run(_cfg(tmp_path, setups=[2], repeats=1, models=_models("blank_letter", [1])), out_root=str(tmp_path), limit=2)
    calls = _calls(rd); assert _meta(rd)["compliance_stop"] is None and len(calls) == 2 * 8
    for c in calls:
        assert c["attempt"] == 2 and c["retry_reason"] == "validation" and len(c["attempt1_tool_calls"]) == 1 and "letter empty" in c["original_error"]
        assert c["first_attempt_forced_call_ok"] is True
        assert c["tool_called"] and c["s2_compliance"] is None and c["parsed"]["valid"]

# (kind, attempts the fault fires on, stop reason, attempt the run stops on). Tool-rule faults stop on the attempt they
# occur with no retry; a blank letter after exactly one correct call gets the single retry and stops if still blank.
NONCOMPLIANT = [("blank_letter", [1, 2], "blank_letter", 2), ("no_tool", [1], "no_tool_call", 1),
                ("no_tool_blank", [1], "no_tool_call", 1), ("no_tool", [2], "no_tool_call", 2), ("no_tool_blank", [2], "no_tool_call", 2)]

def _pre(kind, attempts):
    """Attempt-2 tool faults need an attempt-1 retry trigger: a blank letter after a correct call on attempt 1."""
    if attempts != [2]: return _models(kind, attempts)
    return [dict(m, fail=[{"p": 1.0, "kind": "blank_letter", "attempts": [1]}, {"p": 1.0, "kind": kind, "attempts": [2]}]) for m in EMU3[:1]]

@pytest.mark.parametrize("kind,attempts,reason,stop_attempt", NONCOMPLIANT)
def test_sync_noncompliance_stops_run(tmp_path, kind, attempts, reason, stop_attempt):
    rd = run(_cfg(tmp_path, setups=[2, 1], repeats=1, models=_pre(kind, attempts)), out_root=str(tmp_path), limit=2)
    calls = _calls(rd); m = _meta(rd)
    assert m["compliance_stop"]["reason"] == reason and len(calls) == 1          # stopped at the first non-compliant call
    assert calls[0]["s2_compliance"] == reason and not any(c["setup"] == 1 for c in calls)
    assert calls[0]["attempt"] == stop_attempt
    assert calls[0]["first_attempt_forced_call_ok"] is (attempts != [1] or kind == "blank_letter")
    if kind == "no_tool" and stop_attempt == 1: assert calls[0]["parsed"]["valid"]   # valid letter w/o tool: no retry, still stops

@pytest.mark.parametrize("kind,attempts,reason,stop_attempt", NONCOMPLIANT)
def test_batch_noncompliance_stops_run(tmp_path, kind, attempts, reason, stop_attempt):
    B.MockBackend.STORE.clear()
    rd = B.start(_cfg(tmp_path, "pilot_batch_mock.yaml", "b.yaml", setups=[2], repeats=1, models=_pre(kind, attempts)), out_root=str(tmp_path), limit=2, poll_s=0)
    calls = _calls(rd); m = _meta(rd)
    assert m["compliance_stop"]["reason"] == reason
    bad = [c for c in calls if c.get("s2_compliance") == reason]; assert bad
    assert all(c["attempt"] == stop_attempt for c in bad)                        # tool-rule faults are never retried
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert st["compliance_stop"]["reason"] == reason

def test_batch_blank_letter_retried_then_compliant(tmp_path):
    B.MockBackend.STORE.clear()
    rd = B.start(_cfg(tmp_path, "pilot_batch_mock.yaml", "b.yaml", setups=[2], repeats=1, models=_models("blank_letter", [1])), out_root=str(tmp_path), limit=2, poll_s=0)
    calls = _calls(rd); assert _meta(rd)["compliance_stop"] is None and len(calls) == 16
    assert all(c["attempt"] == 2 and len(c["attempt1_tool_calls"]) == 1 and c["tool_called"] and c["s2_compliance"] is None for c in calls)
    assert all(c["first_attempt_forced_call_ok"] is True for c in calls)

def test_setup_summary_counts_noncompliance():
    tc = [{"name": "score_application"}]
    rows = OS.setup_summary([{"vendor": "anthropic", "setup": 2, "tool_called": True, "tool_calls": tc, "response": "", "parsed": {"letter": "", "valid": False},
                              "first_attempt_forced_call_ok": True},
                             {"vendor": "anthropic", "setup": 2, "tool_called": True, "tool_calls": tc, "response": "x", "parsed": {"letter": "x", "valid": True},
                              "first_attempt_forced_call_ok": False},
                             {"vendor": "anthropic", "setup": 2, "tool_called": False, "tool_calls": [], "response": "x", "parsed": {"letter": "x", "valid": True}}])
    assert rows[0]["s2_noncompliance"] == {"blank_letter": 1, "no_tool_call": 1} and rows[0]["tool_call_rate"] == round(2 / 3, 4)
    assert rows[0]["first_attempt_n"] == 2 and rows[0]["first_attempt_forced_ok"] == 1 and rows[0]["first_attempt_forced_call_rate"] == 0.5

# ---------------------------------------------------------------- item 7: probe
def test_probe_profiles_deterministic():
    cfg = yaml.safe_load(open(PR.CFG)); sel = PR.select_profiles(load_items(dict(cfg, profile_ids=None)))
    assert sel == {"approve": "P001", "deny": "P000"} == cfg["probe_profiles"] and sorted(cfg["profile_ids"]) == ["P000", "P001"]

def test_probe_guard_refuses_wrong_design(tmp_path):
    cfg = yaml.safe_load(open(PR.CFG)); pr = load_prices(P("configs", "prices.yaml")); items = load_items(cfg)
    PR.probe_guard(cfg, items, pr)
    for bad in ({"probe_profiles": {"approve": "P002", "deny": "P000"}}, {"setups": [1, 2]}, {"repeats": 3}, {"phase": "minipilot"},
                {"amendment_sha256": "0" * 64}, {"judge": {"vendor": "openai", "model": "gpt-5-nano"}}):
        with pytest.raises(SystemExit): PR.probe_guard({**cfg, **bad}, items, pr)

def test_probe_script_refuses_live_without_flag(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("rsp", P("scripts", "run_s2_probe.py")); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    with pytest.raises(SystemExit, match="--live"): mod.main(["--allow-paid", "--out", str(tmp_path)])
    called = {}
    monkeypatch.setattr(PR, "run_probe", lambda cfg, live, out_root: called.setdefault("live", live) and None or
                        {"probe_id": "x", "live": live, "profiles": {}, "spent_usd": 0, "paths": {}, "verdict": "v", "pass": True})
    mod.main(["--out", str(tmp_path)]); assert called["live"] is False           # default = free dry run

def test_probe_dry_run_passes_and_is_excluded_from_analysis(tmp_path, capsys):
    from llmbias.analysis import load_calls, analyze, PROBE_MARKER
    rep = PR.run_probe(PR.CFG, live=False, out_root=str(tmp_path))
    out = capsys.readouterr().out; assert "COST ESTIMATE" in out and '"cap_usd": 0.15' in out
    assert rep["pass"] and set(rep["paths"]) == {"sync", "batch"} and rep["paths"]["batch"]["payload_check"]["pass"]
    assert rep["paths"]["batch"]["payload_check"]["round1"] == rep["paths"]["batch"]["payload_check"]["round2"] == 6
    for p in ("sync", "batch"):
        d = rep["paths"][p]; assert d["n_calls"] == 6 and not d["failed_criteria"]
        assert d["first_attempt_forced_call_rate"] == {v: {"ok": 2, "n": 2, "rate": 1.0} for v in ("anthropic", "google", "openai")}
        assert {(r["vendor"], r["profile_id"]) for r in d["rows"]} == {(v, pid) for v in ("openai", "anthropic", "google") for pid in ("P000", "P001")}
        assert all(r["haiku_thinking"] is True for r in d["rows"] if r["vendor"] == "anthropic")
        calls = _calls(d["run_dir"]); assert all(c["probe"] and c["excluded_from_analysis"] and c["vendor"] == "mock" for c in calls)
        assert _meta(d["run_dir"])["probe"] and os.path.exists(os.path.join(tmp_path, PROBE_MARKER))
        with pytest.raises(ValueError, match="probe"): load_calls(d["run_dir"])
        with pytest.raises(ValueError, match="probe"): analyze(d["run_dir"], n_perm=10)

def test_analysis_refuses_probe_by_marker_meta_or_record(tmp_path):
    """A copied probe run is still refused: by an ancestor marker, by run_meta, or by any probe-tagged record."""
    import shutil
    from llmbias.analysis import load_calls, PROBE_MARKER
    rd = run(_cfg(tmp_path, setups=[2], repeats=1, models=EMU3[:1]), out_root=str(tmp_path / "plain"), limit=1)
    load_calls(rd)                                                               # an ordinary run loads
    for how in ("marker", "meta", "record"):
        d = str(tmp_path / how / "run"); shutil.copytree(rd, d)
        if how == "marker": open(os.path.join(os.path.dirname(d), PROBE_MARKER), "w").write("x")
        if how == "meta": m = _meta(d); m["probe"] = "s2"; json.dump(m, open(os.path.join(d, "run_meta.json"), "w"))
        if how == "record":
            cs = _calls(d); cs[0]["probe"] = "s2"; open(os.path.join(d, "calls.jsonl"), "w").write("".join(json.dumps(c) + "\n" for c in cs))
        with pytest.raises(ValueError, match="probe"): load_calls(d)

def test_probe_checks_fail_closed():
    ok = {"vendor": "mock", "schema_vendor": "anthropic", "model": "m", "profile_id": "P000", "effort": "none", "reasoning_tokens": 0,
          "reasoning": None, "reasoning_kind": None, "tool_calls": [{"name": "score_application"}], "setup": 2,
          "parsed": {"valid": True, "letter": "x"}, "refusal": None, "truncated": False}
    assert PR.check_call(ok)["pass"]
    for bad, crit in (({"reasoning_tokens": None}, "haiku_thinking"), ({"reasoning": "thinking..."}, "haiku_thinking"),
                      ({"tool_calls": []}, "forced_tool_call"), ({"attempt1_tool_calls": []}, "forced_tool_call"),
                      ({"tool_calls": [{"name": "score_application"}] * 2}, "max_one_tool_call"),
                      ({"parsed": {"valid": False, "letter": ""}}, "schema_valid_final"), ({"error": "api_error"}, "forced_tool_call")):
        r = PR.check_call({**ok, **bad}); assert not r["pass"] and r[crit] is False
    assert PR.check_call({**ok, "schema_vendor": "google", "reasoning_tokens": None})["haiku_thinking"] is None   # Haiku-only criterion

# ---------------------------------------------------------------- Reviewer cond. 3: sync Anthropic thinking over both rounds
def _fake_anthropic(monkeypatch, tt1, tt2):
    """Offline stand-in for the anthropic SDK (no network, no key): round 1 = forced tool_use, round 2 = the letter."""
    import sys, types
    from types import SimpleNamespace as NS
    def usage(o, tt): return NS(input_tokens=100, output_tokens=o, output_tokens_details=None if tt == "absent" else NS(thinking_tokens=tt))
    sent = []
    class Messages:
        def create(self, **kw):
            sent.append(kw)
            if len(sent) == 1:
                return NS(content=[NS(type="tool_use", id="tu1", name="score_application", input={"credit_score": 700.0})],
                          usage=usage(20, tt1), model="claude-haiku-5-5", stop_reason="tool_use")
            return NS(content=[NS(type="text", text='{"decision": "deny"}')], usage=usage(50, tt2), model="claude-haiku-5-5", stop_reason="end_turn")
    mod = types.ModuleType("anthropic"); mod.Anthropic = lambda *a, **k: NS(messages=Messages())
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return sent

@pytest.mark.parametrize("tt1,tt2,want", [(0, 0, 0), (37, 0, 37), (5, 7, 12), ("absent", 0, None), (0, "absent", None)])
def test_sync_anthropic_reasoning_tokens_summed_over_both_rounds(monkeypatch, tt1, tt2, want):
    from llmbias.clients import AnthropicClient
    sent = _fake_anthropic(monkeypatch, tt1, tt2)
    spec = {"name": "score_application", "description": "d", "parameters": {"type": "object"}}
    res = AnthropicClient("claude-haiku-5-5", "none").complete("sys", "user", spec, lambda a: {"decision": "deny"}, setup=2)
    assert len(sent) == 2 and sent[0]["tool_choice"] == OS.anthropic_tool_choice() and "tool_choice" not in sent[1]
    assert res.reasoning_tokens == want and res.output_tokens == 70
    rec = {"vendor": "anthropic", "model": "claude-haiku-5-5", "profile_id": "P000", "effort": "none", "reasoning_tokens": res.reasoning_tokens,
           "reasoning": res.reasoning, "reasoning_kind": res.reasoning_kind, "tool_calls": res.tool_calls, "setup": 2,
           "parsed": {"valid": True, "letter": "x"}, "refusal": None, "truncated": False}
    # round-1 thinking (or a missing count in either round) must fail the probe's Haiku no-thinking check
    assert PR.check_call(rec)["haiku_thinking"] is (want == 0)
