"""Reviewer conditions on 91baba3, commit B (gates the re-test): (5) cancel + drain in-flight batches at a compliance stop,
booking the in-flight worst case as a keyed reserve; (a) round-2 tool calls count toward multiple_tool_calls; (b) bad forced
tool arguments are a compliance stop, API/transport errors stay missing data and are counted; (c) judge / labels /
positive_control refuse probe output; (2) the re-test cap basis is recorded in run_meta and README."""
import json, os, shutil, sys, types, pytest, yaml
from types import SimpleNamespace as NS
from llmbias.build_data import P
from llmbias import batch as B, runner as RN, clients as CL, output_schema as OS, judge as JD, labels as LB, positive_control as PCM
from llmbias.budget import BudgetGuard, load_prices, cumulative_spend, read_ledger, PHASE_CAPS, retest_cap_basis

PRICES = load_prices(P("configs", "prices.yaml"))
EMU3 = [{"vendor": "mock", "model": f"mock-{v}", "effort": "none", "emulate": v} for v in ("openai", "anthropic", "google")]

@pytest.fixture(autouse=True)
def _mock_backend():
    B.MockBackend.reset(); yield; B.MockBackend.reset()

def _cfg(tmp_path, base="pilot_mock.yaml", name="s.yaml", **over):
    cfg = yaml.safe_load(open(P("configs", base))); cfg.update(over)
    p = tmp_path / name; yaml.safe_dump(cfg, open(p, "w")); return str(p)
def _calls(rd): return [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
def _meta(rd): return json.load(open(os.path.join(rd, "run_meta.json")))
def _rows(p): return [json.loads(l) for l in open(p)] if os.path.exists(p) else []
def _fault(kind, attempts=(1,), model=0): return dict(EMU3[model], fail={"p": 1.0, "kind": kind, "attempts": list(attempts)})

def _batch(tmp_path, models, **over):
    return B.start(_cfg(tmp_path, "pilot_batch_mock.yaml", "b.yaml", setups=[2], repeats=1, models=models, **over), out_root=str(tmp_path), limit=1, poll_s=0)

# ---------------------------------------------------------------- (5) cancel + drain at a batch compliance stop
def test_batch_stop_cancels_and_drains_inflight_with_reserve(tmp_path):
    B.MockBackend.DELAY["mock-anthropic"] = 50                   # the second vendor's batch is still running at the stop
    rd = _batch(tmp_path, [_fault("no_tool"), EMU3[1]])
    m = _meta(rd); cs = m["compliance_stop"]; st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert cs["reason"] == "no_tool_call" and cs["inflight_at_stop"]["n_batches"] == 1
    (bid, worst), = cs["inflight_at_stop"]["batches"].items()
    assert worst > 0 and m["inflight_worst_at_stop_usd"] == cs["inflight_at_stop"]["worst_usd"] == pytest.approx(worst)
    assert cs["inflight_at_stop"]["by_vendor"] == {"mock": pytest.approx(worst)}
    b = st["batches"][bid]
    assert b["cancel_requested"] and b["status"] == "cancelled" and b["released"] and bid in B.MockBackend.CANCELLED
    assert cs["drain"]["complete"] and cs["drain"]["still_in_flight"] == [] and cs["drain"]["reserve_outstanding_usd"] == 0
    assert not any(x["status"] == "submitted" for x in st["batches"].values())  # nothing left in flight
    led = _rows(os.path.join(rd, "spend_ledger.jsonl"))
    res = [r for r in led if r.get("kind") == "inflight_reserve"]; rel = [r for r in led if r.get("kind") == "inflight_release"]
    assert len(res) == len(rel) == 1 and res[0]["cost"] == pytest.approx(worst) and rel[0]["cost"] == pytest.approx(-worst)
    actual = sum(r["cost"] for r in led if not r.get("kind"))
    assert sum(r["cost"] for r in read_ledger(os.path.join(rd, "spend_ledger.jsonl"))[0]) == pytest.approx(actual) and m["spent_usd"] == pytest.approx(actual, abs=1e-6)
    # partial results of the cancelled batch were collected and billed; the rest are compliance_stop (not API errors)
    anth = [c for c in _calls(rd) if c["model"] == "mock-anthropic"]
    billed = {r["call_id"] for r in led if not r.get("kind") and r["model"] == "mock-anthropic"}
    assert 0 < len(billed) < len(anth)
    assert all(c["error"].startswith("compliance_stop") for c in anth if c.get("error"))
    assert m["error_counts"]["api_error"]["total"] == 0 and m["error_counts"]["compliance_stop"]["total"] > 0

def test_undrained_batch_keeps_reserve_then_resume_releases(tmp_path, monkeypatch):
    """A drain that cannot finish (timeout / crash) leaves the reserve booked, so spend is never undercounted; a resume
    does not book it twice and releases it once the batch is collected."""
    B.MockBackend.DELAY["mock-anthropic"] = 3
    monkeypatch.setattr(B.MockBackend, "cancel", lambda self, bid: None)          # vendor ignores the cancel; batch keeps running
    cfg = yaml.safe_load(open(_cfg(tmp_path, "pilot_batch_mock.yaml", "b.yaml", setups=[2], repeats=1, models=[_fault("no_tool"), EMU3[1]])))
    items = RN.load_items(cfg); items = items[items.profile_id.isin(items.profile_id.unique()[:1])]
    rd = str(tmp_path / "run"); os.makedirs(rd)
    meta = {"run_id": "r", "config": cfg, "excluded_from_confirmatory": True, "phase": None}
    br = B.BatchRun(rd, cfg, False); br.init(items, meta); br.loop(poll_s=0, max_wait_s=0)
    cs = br.state["compliance_stop"]; worst = cs["inflight_at_stop"]["worst_usd"]
    assert not cs["drain"]["complete"] and worst > 0 and cs["drain"]["reserve_outstanding_usd"] == pytest.approx(worst)
    led = os.path.join(rd, "spend_ledger.jsonl"); actual = lambda: sum(r["cost"] for r in _rows(led) if not r.get("kind"))
    assert br.guard.spent == pytest.approx(actual() + worst)                    # reserve counted while undrained
    rd2 = B.resume(rd, poll_s=0)
    st = json.load(open(os.path.join(rd2, "batch_state.json"))); cs = st["compliance_stop"]
    assert cs["drain"]["complete"] and [r["kind"] for r in _rows(led) if r.get("kind")] == ["inflight_reserve", "inflight_release"]
    assert sum(r["cost"] for r in read_ledger(led)[0]) == pytest.approx(actual())

def test_reserve_reaches_cumulative_ledger_idempotently(tmp_path):
    cum = str(tmp_path / "cum.jsonl"); led = str(tmp_path / "spend_ledger.jsonl")
    g = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum); base = sum(cumulative_spend(cum).values())
    for _ in range(3): g.reserve("anthropic", "claude-haiku-5-5", 0.05, "msgbatch_1")
    assert sum(cumulative_spend(cum).values()) == pytest.approx(base + 0.05) and len(_rows(cum)) == 1
    g2 = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum); g2.reserve("anthropic", "claude-haiku-5-5", 0.05, "msgbatch_1")
    assert len(_rows(cum)) == 1 and g2.spent == pytest.approx(0.05)
    g2.release("anthropic", "claude-haiku-5-5", "msgbatch_1"); g2.release("anthropic", "claude-haiku-5-5", "msgbatch_1")
    assert sum(cumulative_spend(cum).values()) == pytest.approx(base) and len(_rows(cum)) == 2 and g2.spent == pytest.approx(0)

@pytest.mark.parametrize("vendor,status,want", [("openai", "cancelled", "cancelled"), ("openai", "cancelling", "running"), ("openai", "expired", "expired"), ("openai", "failed", "failed"), ("google", "JOB_STATE_EXPIRED", "expired"),
                                                ("google", "JOB_STATE_CANCELLED", "cancelled"), ("google", "JOB_STATE_FAILED", "failed")])
def test_backend_cancelled_status(vendor, status, want):
    be = B.BACKENDS[vendor].__new__(B.BACKENDS[vendor])
    st = NS(status=status) if vendor == "openai" else NS(state=NS(name=status))
    be.c = NS(batches=NS(retrieve=lambda bid: st, get=lambda name: st))
    assert be.status("x") == want
    for v in ("openai", "anthropic", "google", "mock"): assert callable(getattr(B.BACKENDS[v], "cancel"))

# ---------------------------------------------------------------- (a) round-2 tool calls
def test_sync_round2_tool_call_counts_as_multiple(tmp_path):
    rd = RN.run(_cfg(tmp_path, setups=[2, 1], repeats=1, models=[_fault("round2_tool")]), out_root=str(tmp_path), limit=1)
    c, = _calls(rd); m = _meta(rd)
    assert m["compliance_stop"]["reason"] == "multiple_tool_calls" and c["attempt"] == 1 and c["first_attempt_forced_call_ok"] is False
    assert len(c["tool_calls"]) == 2 and c["tool_calls"][1]["round"] == 2 and c["tool_calls"][1]["result"] is None and "round" not in c["tool_calls"][0]

def test_batch_round2_tool_call_counts_as_multiple(tmp_path):
    rd = _batch(tmp_path, [_fault("round2_tool")])
    m = _meta(rd); bad = [c for c in _calls(rd) if c.get("s2_compliance") == "multiple_tool_calls"]
    assert m["compliance_stop"]["reason"] == "multiple_tool_calls" and bad
    assert all(c["attempt"] == 1 and [t.get("round") for t in c["tool_calls"]] == [None, 2] for c in bad)

@pytest.mark.parametrize("vendor", ["openai", "anthropic"])
def test_sync_clients_record_round2_tool_requests(monkeypatch, vendor):
    """Offline fake SDKs: round 2 asks for the tool again -> recorded (round 2, not executed) -> multiple_tool_calls."""
    sent = []
    if vendor == "openai":
        usage = NS(input_tokens=10, output_tokens=5, output_tokens_details=NS(reasoning_tokens=0))
        fc = lambda: NS(type="function_call", name="score_application", arguments='{"credit_score": 700}', call_id="c1")
        def create(**kw):
            sent.append(kw); return NS(output=[fc()], usage=usage, status="completed", incomplete_details=None, output_text="", model="m")
        mod = types.ModuleType("openai"); mod.OpenAI = lambda *a, **k: NS(responses=NS(create=create))
        cl = CL.OpenAIClient("gpt-5.4-mini", "none")
    else:
        usage = NS(input_tokens=10, output_tokens=5, output_tokens_details=NS(thinking_tokens=0))
        def create(**kw):
            sent.append(kw); return NS(content=[NS(type="tool_use", id="t", name="score_application", input={"credit_score": 700})], usage=usage, model="m", stop_reason="tool_use")
        mod = types.ModuleType("anthropic"); mod.Anthropic = lambda *a, **k: NS(messages=NS(create=create))
        cl = CL.AnthropicClient("claude-haiku-5-5", "none")
    monkeypatch.setitem(sys.modules, vendor, mod)
    res = cl.complete("s", "u", {"name": "score_application", "description": "d", "parameters": {"type": "object", "properties": {}}}, lambda a: {"decision": "deny"}, setup=2)
    assert len(sent) == 2 and [t.get("round") for t in res.tool_calls] == [None, 2] and OS.tool_rule(res.tool_calls) == "multiple_tool_calls"

# ---------------------------------------------------------------- (b) bad tool arguments vs API errors
def test_tool_rule_tool_args_invalid():
    ok = {"name": "score_application", "args": {}, "result": {}}
    assert OS.tool_rule([dict(ok, error="JSONDecodeError: x", result=None)]) == "tool_args_invalid" == OS.TOOL_ARGS_INVALID
    assert OS.tool_rule([dict(ok, name="other", error="x")]) == "wrong_tool" and OS.tool_rule([ok, dict(ok, error="x")]) == "multiple_tool_calls"
    with pytest.raises(CL.ToolArgumentError) as e: CL.exec_tool(lambda a: a, "score_application", '{"credit_score": 7')
    assert e.value.calls[0]["error"].startswith("JSONDecodeError") and e.value.calls[0]["args"] == '{"credit_score": 7'
    with pytest.raises(CL.ToolArgumentError): CL.exec_tool(lambda a: a, "score_application", "[1]")       # not an object
    with pytest.raises(CL.ToolArgumentError): CL.exec_tool(RN.tool_fn_factory(), "score_application", {"credit_score": 700})   # fails validation

def test_sync_bad_tool_args_is_compliance_stop_and_billed(tmp_path):
    rd = RN.run(_cfg(tmp_path, setups=[2, 1], repeats=1, models=[_fault("bad_tool_args")]), out_root=str(tmp_path), limit=1)
    c, = _calls(rd); m = _meta(rd)
    assert m["compliance_stop"]["reason"] == "tool_args_invalid" and c["s2_compliance"] == "tool_args_invalid" and c["attempt"] == 1
    assert not c.get("error") and c["tool_calls"][0]["error"] and c["tool_calls"][0]["result"] is None
    led = _rows(os.path.join(rd, "spend_ledger.jsonl")); assert len(led) == 1 and led[0]["cost"] > 0      # the answered round is billed
    assert m["error_counts"]["tool_args_invalid"]["total"] == 1 and m["error_counts"]["api_error"]["total"] == 0

def test_batch_bad_tool_args_is_compliance_stop(tmp_path):
    rd = _batch(tmp_path, [_fault("bad_tool_args")])
    m = _meta(rd); bad = [c for c in _calls(rd) if c.get("s2_compliance") == "tool_args_invalid"]
    assert m["compliance_stop"]["reason"] == "tool_args_invalid" and bad and all(c["attempt"] == 1 and not c.get("error") for c in bad)
    assert m["error_counts"]["tool_args_invalid"]["total"] == len(bad) and m["error_counts"]["api_error"]["total"] == 0

def test_openai_batch_parse_keeps_bad_json_arguments():
    nat = {"response": {"status_code": 200, "body": {"output": [{"type": "function_call", "call_id": "c", "name": "score_application", "arguments": '{"bad'}],
                                                       "usage": {"input_tokens": 5, "output_tokens": 2}, "model": "m"}}}
    p = B.OpenAIBackend.parse(None, nat); assert p["tool_requests"] == [("c", "score_application", '{"bad')]
    with pytest.raises(CL.ToolArgumentError): CL.exec_tool(B.tool_exec, "score_application", p["tool_requests"][0][2])

def test_sync_openai_bad_arguments_not_retried_as_transient(monkeypatch):
    """Arguments mentioning 500 must not look transient (no backoff loop); the error carries round-1 usage for billing."""
    sent = []
    def create(**kw):
        sent.append(kw)
        return NS(output=[NS(type="function_call", name="score_application", arguments='{"credit_score": 500', call_id="c")],
                  usage=NS(input_tokens=11, output_tokens=7, output_tokens_details=NS(reasoning_tokens=0)), model="m")
    mod = types.ModuleType("openai"); mod.OpenAI = lambda *a, **k: NS(responses=NS(create=create)); monkeypatch.setitem(sys.modules, "openai", mod)
    with pytest.raises(CL.ToolArgumentError) as e:
        CL.OpenAIClient("gpt-5.4-mini", "none").complete("s", "u", {"name": "score_application", "description": "d", "parameters": {"type": "object", "properties": {}}}, RN.tool_fn_factory(), setup=2)
    assert len(sent) == 1 and e.value.usage["input_tokens"] == 11 and e.value.usage["output_tokens"] == 7

def test_api_errors_stay_missing_data_and_are_counted(tmp_path, monkeypatch):
    def boom(self, *a, **k): raise RuntimeError("invalid_request_error: bad request")
    monkeypatch.setattr(CL.MockClient, "_complete", boom)
    rd = RN.run(_cfg(tmp_path, setups=[2], repeats=1, models=EMU3[:2]), out_root=str(tmp_path), limit=1)
    m = _meta(rd); cs = _calls(rd)
    assert m["compliance_stop"] is None and all(c["error"].startswith("RuntimeError") and c.get("s2_compliance") is None for c in cs)
    assert m["error_counts"]["api_error"] == {"total": len(cs), "by_vendor": {"anthropic": len(cs) // 2, "openai": len(cs) // 2}}

def test_batch_api_errors_stay_missing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(B.MockBackend, "parse", lambda self, nat: {"error": "server_error"})
    rd = _batch(tmp_path, EMU3[:1]); m = _meta(rd); cs = _calls(rd)
    assert m["compliance_stop"] is None and all(c["error"] == "server_error" for c in cs)
    assert m["error_counts"]["api_error"]["total"] == len(cs) and m["error_counts"]["compliance_stop"]["total"] == 0

def test_error_counts_classification():
    recs = [{"vendor": "openai", "error": "budget_stop: x"}, {"vendor": "openai", "error": "compliance_stop: y"}, {"vendor": "google", "error": "APIError: 500"},
            {"vendor": "mock", "schema_vendor": "anthropic", "s2_compliance": "tool_args_invalid"}, {"vendor": "openai"}]
    ec = OS.error_counts(recs)
    assert ec["budget_stop"]["total"] == ec["compliance_stop"]["total"] == ec["api_error"]["total"] == ec["tool_args_invalid"]["total"] == 1
    assert ec["api_error"]["by_vendor"] == {"google": 1} and ec["tool_args_invalid"]["by_vendor"] == {"anthropic": 1}

# ---------------------------------------------------------------- (c) probe refusal in judge / labels / positive_control
def _probe_copies(tmp_path):
    from llmbias.analysis import PROBE_MARKER
    rd = B.start(_cfg(tmp_path, "pilot_batch_mock.yaml", "b.yaml", setups=[3], repeats=1, models=EMU3[:1]), out_root=str(tmp_path / "plain"), limit=1, poll_s=0)
    JD.run_judge_batch(rd, poll_s=0)                                             # an ordinary run is judged
    out = {}
    for how in ("marker", "meta", "record"):
        d = str(tmp_path / how / "run"); shutil.copytree(rd, d); out[how] = d
        if how == "marker": open(os.path.join(os.path.dirname(d), PROBE_MARKER), "w").write("x")
        if how == "meta": m = _meta(d); m["probe"] = "s2"; json.dump(m, open(os.path.join(d, "run_meta.json"), "w"))
        if how == "record":
            cs = _calls(d); cs[0]["probe"] = "s2"; open(os.path.join(d, "calls.jsonl"), "w").write("".join(json.dumps(c) + "\n" for c in cs))
    return rd, out

def test_judge_labels_pc_refuse_probe(tmp_path):
    rd, probes = _probe_copies(tmp_path)
    assert len(LB.item_pool(rd)) >= 0 and JD.m4_span_pool(rd)                   # ordinary run: loaders work
    for how, d in probes.items():
        for fn in (lambda: JD.run_judge(d), lambda: JD.run_judge_batch(d, poll_s=0), lambda: list(JD._judge_items(d, (1, 2, 3))),
                   lambda: JD.m4_span_pool(d), lambda: LB.item_pool(d), lambda: LB.export(d, outdir=str(tmp_path / "lab")),
                   lambda: PCM.positive_control_mini(d), lambda: PCM.core_gate(d, ["openai"])):
            with pytest.raises(ValueError, match="probe"): fn()

def test_real_runs_not_refused():
    from llmbias.analysis import refuse_probe_run
    for r in ("20261012T143812-ff54324d-4745d6-batch", "20261018T124607-e5e8b45a-1a973e-batch"):
        d = P("runs", r)
        if os.path.isdir(d): refuse_probe_run(d)

# ---------------------------------------------------------------- (2) re-test cap basis
def test_retest_cap_basis_values():
    cb = retest_cap_basis(); v = cb["by_vendor"]
    assert cb["matches_phase_caps"] and cb["total_cap_usd"] == PHASE_CAPS["minipilot_s2_retest"]["total"] == 3.88
    assert {k: (x["pilot_basis_usd"], x["basis_used"], x["cap_usd"]) for k, x in v.items()} == {
        "openai": (1.0749, "core_actuals", 1.24), "anthropic": (0.2879, "core_actuals", 0.39), "google": (2.2497, "pilot_basis", 2.25)}
    assert v["anthropic"]["core_actuals_usd"] == 0.3857 and v["google"]["core_actuals_usd"] == 2.0781 and v["openai"]["core_actuals_usd"] == pytest.approx(1.2361, abs=1.5e-4)
    assert v["openai"]["includes_judge"] and cb["design"] == {"calls_per_vendor": 600, "judged_letters": 1800}

@pytest.mark.parametrize("path", ["sync", "batch"])
def test_retest_run_meta_records_cap_basis(tmp_path, path):
    over = dict(setups=[2], repeats=1, models=EMU3[:1], phase="minipilot_s2_retest")
    rd = RN.run(_cfg(tmp_path, **over), out_root=str(tmp_path), limit=1) if path == "sync" else \
        B.start(_cfg(tmp_path, "pilot_batch_mock.yaml", "b.yaml", **over), out_root=str(tmp_path), limit=1, poll_s=0)
    cb = _meta(rd)["cap_basis"]; assert cb == json.loads(json.dumps(retest_cap_basis())) and cb["matches_phase_caps"]
    other = RN.run(_cfg(tmp_path, name="o.yaml", setups=[2], repeats=1, models=EMU3[:1]), out_root=str(tmp_path / "o"), limit=1)
    assert "cap_basis" not in _meta(other)

def test_readme_documents_retest_cap_basis():
    # the published tree moved the command reference to docs/RUNNING.md; either place documents the cap basis
    t = open(P("README.md")).read() + (open(P("docs", "RUNNING.md")).read() if os.path.exists(P("docs", "RUNNING.md")) else "")
    assert "minipilot_s2_retest" in t and "max(pilot basis, S2_CORE_P95)" in t
    for s in ("1.0749", "1.2361", "0.2879", "0.3857", "2.2497", "2.0781", "$3.88"): assert s in t
