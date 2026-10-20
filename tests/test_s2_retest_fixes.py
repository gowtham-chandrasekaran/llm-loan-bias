"""Reviewer fixes before the setup-2 re-test (branch s2-retest-fixes, from 0d9f54b):
1 sync_call_id includes the model's index; config_guard rejects duplicate model entries.
2 runner.resume refuses a pre-21a6672 sync run (no attempts journal).
3 in-flight reserves: keyed, in both ledgers before the reserved flag, released only where reserved; crash at every point
  nets exactly the actual spend.
4 results of expired / cancelled / failed batches are fetched and billed before any release.
5 drain timeout measured from the stop and persisted; a cancel that raises keeps cancel_error and no 'cancelled batch' tag.
6 in-flight reserves on the max(pilot basis, S2_CORE_P95) cap basis.
All offline: mock clients / fake backends, no network, no keys."""
import glob, hashlib, json, os, time, pytest, yaml
from types import SimpleNamespace as NS
from llmbias.build_data import P
from llmbias import batch as B, runner as RN, budget as BU, clients as CL, output_schema as OS
from llmbias.budget import BudgetGuard, load_prices, read_ledger, ledger_key, _cum_key, call_cost, call_worst, call_worst_cap_basis, token_basis, S2_CORE_P95

PRICES = load_prices(P("configs", "prices.yaml"))
EMU3 = [{"vendor": "mock", "model": f"mock-{v}", "effort": "none", "emulate": v} for v in ("openai", "anthropic", "google")]

@pytest.fixture(autouse=True)
def _mock_backend():
    B.MockBackend.reset(); yield; B.MockBackend.reset()

def _cfg(tmp_path, base="pilot_mock.yaml", name="s.yaml", **over):
    cfg = yaml.safe_load(open(P("configs", base))); cfg.update(over)
    p = tmp_path / name; yaml.safe_dump(cfg, open(p, "w")); return str(p)
def _rows(p): return [json.loads(l) for l in open(p)] if os.path.exists(p) else []
def _calls(rd): return [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
def _meta(rd): return json.load(open(os.path.join(rd, "run_meta.json")))
def _only_run(root): return next(d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d))

# ================================================================ fix 1: sync_call_id + duplicate models
def test_sync_call_id_includes_model_index():
    m = {"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}; app = {"profile_id": "P000", "variant": "base", "variant_id": "v"}
    a, b = RN.sync_call_id("run", m, 2, app, 0, 0), RN.sync_call_id("run", m, 2, app, 0, 1)
    assert a != b and RN.sync_call_id("run", m, 2, app, 0, 0) == a                  # deterministic, distinct per position
    old = hashlib.sha256("|".join(map(str, ("run", "openai", "gpt-5.4-mini", "none", 2, "P000", "base", "v", 0))).encode()).hexdigest()[:32]
    assert RN.sync_call_id("run", m, 2, app, 0, 5, scheme=1) == old                # scheme 1 = the 21a6672..0d9f54b id (resume only)
    with pytest.raises(ValueError): RN.sync_call_id("run", m, 2, app, 0)

def test_config_guard_rejects_duplicate_models():
    cfg = yaml.safe_load(open(P("configs", "pilot_mock.yaml")))
    for models in ([EMU3[0], dict(EMU3[0])], [EMU3[0], dict(EMU3[0], fail={"p": 1, "kind": "no_tool"})],
                   [{"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}] * 2):
        with pytest.raises(SystemExit, match="duplicate model entries"): RN.config_guard(dict(cfg, models=models), False)
    RN.config_guard(dict(cfg, models=EMU3), False)                                  # same mock model name, different emulated vendor: ok
    RN.config_guard(dict(cfg, models=[dict(EMU3[0]), dict(EMU3[0], effort="low")]), False)   # different effort: distinct entries

def test_duplicate_models_refused_by_sync_and_batch(tmp_path):
    dup = [EMU3[0], dict(EMU3[0])]
    with pytest.raises(SystemExit, match="duplicate"): RN.run(_cfg(tmp_path, setups=[2], repeats=1, models=dup), out_root=str(tmp_path), limit=1)
    with pytest.raises(SystemExit, match="duplicate"):
        B.start(_cfg(tmp_path, "pilot_batch_mock.yaml", "b.yaml", setups=[2], repeats=1, models=dup), out_root=str(tmp_path), limit=1, poll_s=0)

def test_sync_run_ids_unique_per_model_position(tmp_path):
    rd = RN.run(_cfg(tmp_path, setups=[3, 2], repeats=1, models=EMU3), out_root=str(tmp_path), limit=1)
    ids = [c["call_id"] for c in _calls(rd)]; assert len(ids) == len(set(ids)) and _meta(rd)["sync_call_id_scheme"] == 2
    led = _rows(os.path.join(rd, "spend_ledger.jsonl")); assert len(led) == len({ledger_key(r) for r in led}) == len(ids)

def test_scheme1_run_resumes_with_its_own_ids(tmp_path, monkeypatch):
    """A run started before this fix (meta without sync_call_id_scheme) keeps resuming on the old ids: nothing re-sent or re-billed."""
    class Crash(BaseException): pass
    cfgp = _cfg(tmp_path, setups=[2], repeats=1, models=EMU3[:2])
    clean = RN.run(cfgp, out_root=str(tmp_path / "clean"), limit=1); clean_total = round(sum(r["cost"] for r in _rows(os.path.join(clean, "spend_ledger.jsonl"))), 12)
    monkeypatch.setattr(RN, "SYNC_CALL_ID_SCHEME", 1)
    orig_meta_dump = RN.json.dump
    n = {"i": 0}; orig = RN.BudgetGuard.record
    def rec(self, *a, **kw):
        n["i"] += 1; out = orig(self, *a, **kw)
        if n["i"] == 5: raise Crash
        return out
    monkeypatch.setattr(RN.BudgetGuard, "record", rec)
    with pytest.raises(Crash): RN.run(cfgp, out_root=str(tmp_path / "old"), limit=1)
    rd = _only_run(str(tmp_path / "old")); m = _meta(rd); m.pop("sync_call_id_scheme"); json.dump(m, open(os.path.join(rd, "run_meta.json"), "w"))
    monkeypatch.setattr(RN.BudgetGuard, "record", orig); monkeypatch.setattr(RN, "SYNC_CALL_ID_SCHEME", 2)
    sent = {"n": 0}; oc = CL.MockClient._complete
    def counting(self, *a, **kw): sent["n"] += 1; return oc(self, *a, **kw)
    monkeypatch.setattr(CL.MockClient, "_complete", counting)
    RN.resume(rd)
    led = _rows(os.path.join(rd, "spend_ledger.jsonl"))
    assert round(sum(r["cost"] for r in led), 12) == clean_total and len(led) == len({ledger_key(r) for r in led})
    assert sent["n"] == len(_calls(clean)) - 5 and len(_calls(rd)) == len(_calls(clean))   # only the unfinished calls were sent

# ================================================================ fix 2: resume refuses pre-21a6672 sync runs
def test_resume_refuses_run_without_attempts_journal(tmp_path, monkeypatch):
    class Crash(BaseException): pass
    n = {"i": 0}; orig = RN.BudgetGuard.record
    def rec(self, *a, **kw):
        n["i"] += 1; out = orig(self, *a, **kw)
        if n["i"] == 3: raise Crash
        return out
    monkeypatch.setattr(RN.BudgetGuard, "record", rec)
    with pytest.raises(Crash): RN.run(_cfg(tmp_path, setups=[2], repeats=1, models=EMU3[:1]), out_root=str(tmp_path / "r"), limit=1)
    monkeypatch.setattr(RN.BudgetGuard, "record", orig)
    rd = _only_run(str(tmp_path / "r")); os.remove(os.path.join(rd, "raw", "attempts_journal.jsonl"))   # = a pre-21a6672 run
    led = os.path.join(rd, "spend_ledger.jsonl"); before = open(led).read()
    sent = {"n": 0}; oc = CL.MockClient._complete
    monkeypatch.setattr(CL.MockClient, "_complete", lambda self, *a, **kw: sent.__setitem__("n", sent["n"] + 1) or oc(self, *a, **kw))
    with pytest.raises(SystemExit, match="attempts_journal"): RN.resume(rd)
    assert sent["n"] == 0 and open(led).read() == before                          # nothing sent, nothing billed

def test_resume_refuses_real_pre_fix_layout(tmp_path):
    rd = tmp_path / "old"; (rd / "raw").mkdir(parents=True)
    json.dump({"run_id": "x", "config": yaml.safe_load(open(P("configs", "pilot_mock.yaml")))}, open(rd / "run_meta.json", "w"))
    open(rd / "calls.jsonl", "w").write(json.dumps({"call_id": "0123abcd" * 4}) + "\n")
    with pytest.raises(SystemExit, match="before 21a6672"): RN.resume(str(rd))

# ================================================================ shared: paid-vendor fake backends (offline)
class FakePaid(B.MockBackend):
    """MockBackend registered under a paid vendor name, so the cumulative ledger is exercised. No SDK, no network."""

def _paid_setup(monkeypatch, tmp_path, delay=2, stop_vendor="anthropic"):
    for v in ("openai", "anthropic"): monkeypatch.setitem(B.BACKENDS, v, FakePaid)
    models = [{"vendor": "anthropic", "model": "claude-haiku-5-5", "effort": "none", "fail": {"p": 1.0, "kind": "no_tool", "attempts": [1]}},
              {"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}]
    if stop_vendor == "openai": models = [dict(models[0], fail=None), dict(models[1], fail={"p": 1.0, "kind": "no_tool", "attempts": [1]})]
    B.MockBackend.DELAY["gpt-5.4-mini" if stop_vendor == "anthropic" else "claude-haiku-5-5"] = delay
    return _cfg(tmp_path, "pilot_batch_mock.yaml", "paid.yaml", setups=[2], repeats=1, models=models, phase="minipilot_s2_retest",
                budget_usd=3.88, per_vendor_budget_usd={"openai": 1.24, "anthropic": 0.39, "google": 2.25})

def _nets(rd):
    """Per ledger (run, and this run's rows in the cumulative ledger): actual spend, net incl. reserves/releases, and
    reserve / release keys."""
    led = os.path.join(rd, "spend_ledger.jsonl"); cum = os.environ["LLMBIAS_CUMULATIVE_LEDGER"]; me = os.path.realpath(led)
    out = {}
    for name, rows in (("run", read_ledger(led)[0]), ("cumulative", [r for r in read_ledger(cum, _cum_key)[0] if os.path.realpath(r.get("ledger") or "") == me])):
        out[name] = {"actual": sum(r["cost"] for r in rows if not r.get("kind")), "net": sum(r["cost"] for r in rows),
                     "reserve": {r["batch_id"]: r["cost"] for r in rows if r.get("kind") == "inflight_reserve"},
                     "release": {r["batch_id"]: r["cost"] for r in rows if r.get("kind") == "inflight_release"}}
    return out

def _assert_nets_actual(rd, complete=True):
    n = _nets(rd)
    for name, x in n.items():
        assert set(x["release"]) <= set(x["reserve"]), f"{name}: release without reserve"
        for bid, c in x["release"].items(): assert c == pytest.approx(-x["reserve"][bid], abs=0)
        if complete: assert set(x["release"]) == set(x["reserve"]) and x["net"] == pytest.approx(x["actual"], abs=1e-12), name
    if complete: assert n["run"]["actual"] == pytest.approx(n["cumulative"]["actual"], abs=1e-12), (rd, n)  # paid rows are in both ledgers
    return n

def test_paid_fake_clean_run_reserves_and_releases(tmp_path, monkeypatch):
    rd = B.start(_paid_setup(monkeypatch, tmp_path), allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    m = _meta(rd); cs = m["compliance_stop"]; n = _assert_nets_actual(rd)
    assert cs["reason"] == "no_tool_call" and cs["inflight_at_stop"]["n_batches"] == 1 and cs["drain"]["complete"]
    (bid, amt), = cs["inflight_at_stop"]["batches"].items()
    assert n["run"]["reserve"] == n["cumulative"]["reserve"] == {bid: pytest.approx(amt)} and amt > 0

# ================================================================ fix 3: crash at every point nets the actual spend
class Crash(BaseException): pass

def _crash_harness(monkeypatch, k):
    """Crash at event k; events = before and after every ledger append and every state save."""
    ev = {"i": 0, "fired": False}; orig_append = BU._append; orig_save = B.BatchRun.save
    def tick():
        ev["i"] += 1
        if ev["i"] == k: ev["fired"] = True; raise Crash(f"event {k}")
    def app(path, row): tick(); orig_append(path, row); tick()
    def save(self): tick(); orig_save(self); tick()
    monkeypatch.setattr(BU, "_append", app); monkeypatch.setattr(B.BatchRun, "save", save)
    return ev, lambda: (monkeypatch.setattr(BU, "_append", orig_append), monkeypatch.setattr(B.BatchRun, "save", orig_save))

def test_crash_at_every_point_nets_actual_spend(tmp_path, monkeypatch):
    cfgp = _paid_setup(monkeypatch, tmp_path)
    k, ran, post_stop = 0, 0, 0
    while True:
        k += 1; B.MockBackend.POLLS.clear(); B.MockBackend.CANCELLED.clear(); B.MockBackend.STORE.clear()
        cum = str(tmp_path / f"cum{k}.jsonl"); monkeypatch.setenv("LLMBIAS_CUMULATIVE_LEDGER", cum)
        ev, restore = _crash_harness(monkeypatch, k); root = str(tmp_path / f"k{k}")
        try: B.start(cfgp, allow_paid=True, out_root=root, poll_s=0, limit=1)
        except Crash: pass
        restore()
        if not ev["fired"]: break                                                  # k is past the last event: done
        ran += 1; rd = _only_run(root)
        if not os.path.exists(os.path.join(rd, "batch_state.json")):
            assert not _rows(os.path.join(rd, "spend_ledger.jsonl")); continue    # crashed before anything was submitted
        st = json.load(open(os.path.join(rd, "batch_state.json")))
        post_stop += bool(st.get("compliance_stop"))
        _assert_nets_actual(rd, complete=False)                                   # even mid-crash: no release without its reserve
        for _ in range(6):                                                         # resume until drained
            B.resume(rd, allow_paid=True, poll_s=0)
            if (json.load(open(os.path.join(rd, "batch_state.json"))).get("compliance_stop") or {}).get("drain", {}).get("complete"): break
        st = json.load(open(os.path.join(rd, "batch_state.json")))
        assert st["compliance_stop"]["drain"]["complete"], k
        _assert_nets_actual(rd)
        led = _rows(os.path.join(rd, "spend_ledger.jsonl")); keys = [ledger_key(r) for r in led]
        assert len(keys) == len(set(keys)), k                                       # nothing written twice
    assert ran > 40 and post_stop > 15                                               # many crash points, many after the stop

def test_old_order_bug_is_fixed(tmp_path, monkeypatch):
    """The reviewer's scratch case: crash after b['reserved'] would have been persisted but before any reserve row. Now the
    flag is persisted only after both rows, and collect releases only where a ledger holds the reserve."""
    cfgp = _paid_setup(monkeypatch, tmp_path)
    orig = BudgetGuard.reserve
    def boom(self, *a, **kw): raise Crash("before reserve rows")
    monkeypatch.setattr(BudgetGuard, "reserve", boom)
    with pytest.raises(Crash): B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    monkeypatch.setattr(BudgetGuard, "reserve", orig)
    rd = _only_run(str(tmp_path / "r")); st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert all(b.get("reserved") is None for b in st["batches"].values()) and st["compliance_stop"]["inflight_at_stop"]["n_batches"] == 1
    B.MockBackend.DELAY.clear(); B.MockBackend.POLLS.clear()                      # the in-flight batch finishes before the resume's drain
    B.resume(rd, allow_paid=True, poll_s=0)
    n = _assert_nets_actual(rd)
    assert n["run"]["reserve"] == {} and n["run"]["release"] == {} and n["run"]["net"] > 0   # settled first: no reserve, no orphan release
    assert any(b.get("reserve_skipped") for b in json.load(open(os.path.join(rd, "batch_state.json")))["batches"].values())

def test_release_requires_reserve_per_ledger(tmp_path):
    cum = str(tmp_path / "cum.jsonl"); led = str(tmp_path / "spend_ledger.jsonl")
    g = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum)
    assert g.release("anthropic", "claude-haiku-5-5", "b1") == {} and not _rows(led) and not _rows(cum)   # nothing reserved: nothing released
    orig = BU._append; calls = {"n": 0}
    def first_only(path, row):
        calls["n"] += 1
        if calls["n"] == 2: raise Crash("between run and cumulative append")
        orig(path, row)
    BU._append = first_only
    try:
        with pytest.raises(Crash): g.reserve("anthropic", "claude-haiku-5-5", 0.03, "b1")
    finally: BU._append = orig
    g = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum)                     # run ledger has the reserve, cumulative not
    assert g.has_reserve("b1") == {"run": True, "cumulative": False}
    assert g.release("anthropic", "claude-haiku-5-5", "b1") == {"run": 0.03}       # released only in the run ledger
    assert sum(r["cost"] for r in _rows(led)) == 0 and _rows(cum) == []
    g.reserve("anthropic", "claude-haiku-5-5", 0.03, "b1")                         # a reserve after its release is refused in each ledger
    assert sum(r["cost"] for r in _rows(led)) == 0 and len(_rows(led)) == 2
    assert g.release("anthropic", "claude-haiku-5-5", "b1") == {}

# ================================================================ fix 4: expired / failed batches are fetched and billed
def test_expired_batch_results_billed_before_release(tmp_path, monkeypatch):
    cfgp = _paid_setup(monkeypatch, tmp_path, delay=1)
    B.MockBackend.EXPIRE.add("gpt-5.4-mini")
    monkeypatch.setattr(FakePaid, "cancel", lambda self, bid: None)              # the cancel does not land; the batch expires
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    st = json.load(open(os.path.join(rd, "batch_state.json"))); n = _assert_nets_actual(rd)
    ob = [b for b in st["batches"].values() if b["model"] == "gpt-5.4-mini"]
    assert ob and all(b["status"] == "expired" and b["released"] for b in ob)
    billed = {r["call_id"] for r in _rows(os.path.join(rd, "spend_ledger.jsonl")) if not r.get("kind") and r["model"] == "gpt-5.4-mini"}
    assert billed and n["run"]["actual"] > 0                                       # the completed half is billed
    oc = [c for c in _calls(rd) if c["model"] == "gpt-5.4-mini"]
    assert any(c.get("error", "").startswith("batch expired: no result") for c in oc)   # the unfinished half: missing, not 'cancelled batch'

def test_openai_expired_partial_results_fetched(monkeypatch):
    be = B.OpenAIBackend.__new__(B.OpenAIBackend)
    line = {"custom_id": "c1", "response": {"status_code": 200, "body": {"output": [], "usage": {"input_tokens": 9, "output_tokens": 3}, "model": "m"}}}
    be.c = NS(batches=NS(retrieve=lambda bid: NS(status="expired", output_file_id="f1", error_file_id=None)),
              files=NS(content=lambda fid: NS(text=json.dumps(line))))
    assert be.status("b") == "expired" and be.results("b") == {"c1": line}

def test_results_fetch_failure_keeps_reserve(tmp_path, monkeypatch):
    cfgp = _paid_setup(monkeypatch, tmp_path, delay=1)
    orig = FakePaid.results
    def results(self, bid):
        if bid in B.MockBackend.CANCELLED: raise RuntimeError("file service unavailable")
        return orig(self, bid)
    monkeypatch.setattr(FakePaid, "results", results)
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    cs = json.load(open(os.path.join(rd, "batch_state.json")))["compliance_stop"]; n = _nets(rd)
    (bid, amt), = cs["inflight_at_stop"]["batches"].items()
    assert cs["drain"]["reserve_kept_no_results"] == [bid] and cs["drain"]["reserve_outstanding_usd"] == pytest.approx(amt)
    for x in n.values(): assert x["release"] == {} and x["net"] == pytest.approx(x["actual"] + amt)   # over-, never under-counted

# ================================================================ fix 5: drain deadline from the stop; cancel errors
def test_drain_deadline_from_stop_and_persisted(tmp_path, monkeypatch):
    cfgp = _paid_setup(monkeypatch, tmp_path, delay=10_000)
    cfg = yaml.safe_load(open(cfgp)); cfg["drain_max_wait_s"] = 5; yaml.safe_dump(cfg, open(cfgp, "w"))
    monkeypatch.setattr(FakePaid, "cancel", lambda self, bid: None)              # vendor ignores the cancel: it never drains
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(B.time, "time", lambda: clock["t"]); monkeypatch.setattr(B.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + 1))
    orig_loop = B.BatchRun.loop
    def loop(self, poll_s=60, max_wait_s=26 * 3600, write=True):
        clock["t"] += 100                                                          # time spent in loop() before the stop is not drain time
        return orig_loop(self, poll_s, max_wait_s, write)
    monkeypatch.setattr(B.BatchRun, "loop", loop)
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=1, limit=1)
    cs = json.load(open(os.path.join(rd, "batch_state.json")))["compliance_stop"]
    assert cs["drain_deadline_ts"] == cs["stop_ts"] + 5 and cs["drain"]["timed_out"] and not cs["drain"]["complete"]
    assert cs["drain"]["reserve_outstanding_usd"] > 0
    stop, deadline = cs["stop_ts"], cs["drain_deadline_ts"]
    clock["t"] += 3600                                                             # resume an hour later: same deadline, one pass, stop
    polls = {"n": 0}; orig_status = FakePaid.status
    monkeypatch.setattr(FakePaid, "status", lambda self, bid: polls.__setitem__("n", polls["n"] + 1) or orig_status(self, bid))
    B.resume(rd, allow_paid=True, poll_s=1)
    cs = json.load(open(os.path.join(rd, "batch_state.json")))["compliance_stop"]
    assert (cs["stop_ts"], cs["drain_deadline_ts"]) == (stop, deadline) and cs["drain"]["timed_out"] and polls["n"] <= 3

def test_cancel_error_kept_and_requests_not_tagged_cancelled(tmp_path, monkeypatch):
    cfgp = _paid_setup(monkeypatch, tmp_path, delay=2)
    def bad_cancel(self, bid): raise RuntimeError("cancel endpoint 409")
    monkeypatch.setattr(FakePaid, "cancel", bad_cancel)
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    ob = [b for b in st["batches"].values() if b["model"] == "gpt-5.4-mini"]
    assert ob and all(b["cancel_error"].startswith("RuntimeError") and not b.get("cancel_requested") for b in ob)
    assert all(b["status"] == "settled" for b in ob)                                # not cancelled: it ran to completion
    assert not any("cancelled batch" in (c.get("error") or "") for c in _calls(rd))
    _assert_nets_actual(rd)

def test_cancel_retried_on_resume_after_error(tmp_path, monkeypatch):
    cfgp = _paid_setup(monkeypatch, tmp_path, delay=50)
    tries = {"n": 0}
    def flaky(self, bid):
        tries["n"] += 1
        if tries["n"] == 1: raise RuntimeError("transient")
        B.MockBackend.CANCELLED.add(bid)
    monkeypatch.setattr(FakePaid, "cancel", flaky)
    cfg = yaml.safe_load(open(cfgp)); cfg["drain_max_wait_s"] = 0; yaml.safe_dump(cfg, open(cfgp, "w"))
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    assert not json.load(open(os.path.join(rd, "batch_state.json")))["compliance_stop"]["drain"]["complete"]
    B.resume(rd, allow_paid=True, poll_s=0)
    st = json.load(open(os.path.join(rd, "batch_state.json"))); ob = [b for b in st["batches"].values() if b["model"] == "gpt-5.4-mini"]
    assert tries["n"] == 2 and all(b["cancel_requested"] and b["status"] == "cancelled" and "cancel_error" not in b for b in ob)
    _assert_nets_actual(rd)

# ================================================================ fix 6: reserve on the cap basis
def test_anthropic_setup2_reserve_not_below_cap_basis(tmp_path, monkeypatch):
    b = token_basis(); m = b["worst_multiplier"]; t = S2_CORE_P95["anthropic"]
    core = call_cost(PRICES, "anthropic", "claude-haiku-5-5", t["inp95"] * m, t["out95"] * m, True)
    pilot = call_worst(PRICES, "anthropic", "claude-haiku-5-5", 2, 0, 0, 1, True)
    assert pilot < core and call_worst_cap_basis(PRICES, "anthropic", "claude-haiku-5-5", 2, 0, 0, True) == pytest.approx(core)
    assert call_worst_cap_basis(PRICES, "google", "gemini-3.8-flash", 2, 0, 0, True) == pytest.approx(call_worst(PRICES, "google", "gemini-3.8-flash", 2, 0, 0, 1, True))  # pilot basis larger
    assert call_worst_cap_basis(PRICES, "anthropic", "claude-haiku-5-5", 3, 0, 0, True) == pytest.approx(call_worst(PRICES, "anthropic", "claude-haiku-5-5", 3, 0, 0, 1, True))
    # per-call cap basis = the minipilot_s2_retest anthropic cap / (600 calls x 1.05 rerun margin) before the cent ceiling
    assert 600 * core * (1 + b["worst_rerun_margin"]) == pytest.approx(BU.retest_cap_basis()["by_vendor"]["anthropic"]["core_actuals_usd"], abs=1e-4)
    cfgp = _paid_setup(monkeypatch, tmp_path, stop_vendor="openai")               # the Anthropic batch is the one in flight
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    cs = json.load(open(os.path.join(rd, "batch_state.json")))["compliance_stop"]; st = json.load(open(os.path.join(rd, "batch_state.json")))
    (bid, amt), = cs["inflight_at_stop"]["batches"].items(); bt = st["batches"][bid]
    assert bt["vendor"] == "anthropic" and amt >= len(bt["ids"]) * core - 1e-12 and amt > len(bt["ids"]) * pilot
    _assert_nets_actual(rd)

# ================================================================ minor: second tool call with bad arguments
def test_second_tool_call_bad_args_is_multiple_tool_calls():
    ok = {"name": "score_application", "args": {"credit_score": 700}, "result": {}}
    bad = {"name": "score_application", "args": '{"x', "result": None, "error": "JSONDecodeError"}
    assert OS.tool_rule([ok, bad]) == "multiple_tool_calls" and OS.tool_rule([bad]) == "tool_args_invalid"
