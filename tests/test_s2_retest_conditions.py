"""Reviewer conditions on 31b136e (branch s2-retest-fixes), before the 15.9.1 setup-2 re-test:
1 minipilot_s2_retest config + guard + launcher (mock dry run, per-vendor estimate vs caps); item-5 archive/replacement
  (llmbias.s2_replace); judge blind keys only in the private store (600) with MANIFEST lines.
2 an ended batch whose results fetch fails RESULTS_FETCH_TRIES times (no stop) books a keyed worst-case placeholder in
  both ledgers; only a later fetch that bills the actuals releases it.
3 response-side logging: Gemini thought_signature as sha256 + length and the full usage_metadata; per-round reasoning
  tokens [r1, r2] next to the sum (sync and batch). The request fixture stays at 3fda5441.
All offline: mocks / fake SDKs, no network, no keys."""
import base64, glob, hashlib, json, os, shutil, stat, sys, types, pytest, yaml
from types import SimpleNamespace as NS
from llmbias.build_data import P
from llmbias import batch as B, budget as BU, judge as JD, labels as LB, s2_replace as SR, s2_retest as RT, analysis as AN, output_schema as OS
from llmbias.budget import BudgetGuard, load_prices, read_ledger, ledger_key, _cum_key

PRICES = load_prices(P("configs", "prices.yaml"))
CORE = P("runs", SR.CORE_RUN)

@pytest.fixture(autouse=True)
def _mock_backend():
    B.MockBackend.reset(); yield; B.MockBackend.reset()

def _cfg(tmp_path, base="pilot_batch_mock.yaml", name="c.yaml", **over):
    cfg = yaml.safe_load(open(P("configs", base))); cfg.update(over)
    p = tmp_path / name; yaml.safe_dump(cfg, open(p, "w")); return str(p)
def _rows(p): return [json.loads(l) for l in open(p)] if os.path.exists(p) else []
def _calls(rd): return [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]
def _mode(p): return stat.S_IMODE(os.stat(p).st_mode)
def _retest_cfg(): return yaml.safe_load(open(RT.CFG))

# ================================================================ 1a. config + guard + estimate
def test_retest_config_guard_and_estimate():
    from llmbias.runner import config_guard, load_items
    cfg = _retest_cfg(); config_guard(cfg, allow_paid=True); items = load_items(cfg)
    est = RT.retest_guard(cfg, items, PRICES)
    caps = {r["vendor"]: r["cap_usd"] for r in est["rows"]}
    assert caps == {"openai": 1.24, "anthropic": 0.39, "google": 2.25} and est["cap_total_usd"] == 3.88
    assert est["calls_total"] == 1800 and all(r["calls"] == 600 and r["api_requests"] == 1200 for r in est["rows"])
    assert est["under_cap"] and est["cap_basis_matches_phase_caps"] and est["worst_total_usd"] <= 3.88
    assert {r["vendor"]: r["worst_basis"] for r in est["rows"]} == {"openai": "core_actuals", "anthropic": "core_actuals", "google": "pilot_basis"}
    core = yaml.safe_load(open(RT.CORE_CFG))
    assert all(cfg[k] == core[k] for k in RT.SAME_AS_CORE) and cfg["setups"] == [2] and items.profile_id.nunique() == 25

@pytest.mark.parametrize("change,msg", [({"setups": [1, 2]}, "setup 2 only"), ({"budget_usd": 4.0}, "caps"),
                                         ({"per_vendor_budget_usd": {"openai": 1.3, "anthropic": 0.39, "google": 2.25}}, "caps"),
                                         ({"variants": ["base"]}, "differ"), ({"repeats": 1}, "differ"), ({"max_output_tokens": 1024}, "differ"),
                                         ({"require_all_profiles": False}, "require_all_profiles"), ({"replaces_setup2_of": "x"}, "replaces_setup2_of"),
                                         ({"amendment_sha256": "0" * 64}, "amendment"), ({"phase": "minipilot"}, "phase"),
                                         ({"models": [{"vendor": "openai", "model": "gpt-5-mini", "effort": "none"}]}, "differ")])
def test_retest_guard_refuses(change, msg):
    from llmbias.runner import load_items
    cfg = _retest_cfg(); items = load_items(cfg); cfg.update(change)
    with pytest.raises(SystemExit, match=msg): RT.retest_guard(cfg, items, PRICES)

def test_require_all_profiles_refuses_trim(tmp_path):
    p = _cfg(tmp_path, setups=[2], repeats=1, require_all_profiles=True, budget_usd=0.0001, per_vendor_budget_usd={"mock": 0.0001})
    with pytest.raises(SystemExit, match="require_all_profiles"): B.start(p, out_root=str(tmp_path / "r"), poll_s=0, limit=3)

def test_launcher_flags(capsys):
    sys.path.insert(0, P("scripts")); import run_s2_retest as L
    assert L.main(["--estimate-only"]) == 0
    out = capsys.readouterr().out
    assert "openai" in out and "1.24" in out and "0.39" in out and "2.25" in out and "3.88" in out and "TOTAL" in out
    with pytest.raises(SystemExit, match="--live"): L.main(["--allow-paid"])
    with pytest.raises(SystemExit, match="PASSING live probe"): L.main(["--live"])

def test_mock_dry_run_end_to_end(tmp_path, capsys):
    """The launcher's dry run: 1,800 mock calls, mock judge, blind key only in the private root, replacement demo on a copy."""
    priv = tmp_path / "priv"; core = CORE if os.path.exists(os.path.join(CORE, "calls.jsonl")) else None
    rep = RT.run_retest(out_root=str(tmp_path / "out"), private_root=str(priv), core_rd=core)
    assert rep["live"] is False and rep["retest"]["calls"] == 1800 and set(rep["retest"]["by_vendor"]) == {"openai", "anthropic", "google"}
    assert all(d["n"] == 600 for d in rep["retest"]["by_vendor"].values()) and rep["retest"]["cap_basis_in_run_meta"]
    assert rep["judge"]["judgments"] == 1800 and not rep["judge"]["blind_key_in_run_folder"]
    assert rep["judge"]["blind_key"].startswith(str(priv)) and _mode(rep["judge"]["blind_key"]) == 0o600
    assert not glob.glob(os.path.join(rep["run_dir"], "**", "blind_key*"), recursive=True)
    assert "COST ESTIMATE" in capsys.readouterr().out
    if core:
        d = rep["replace_demo"]; ops = d["operational"]["anthropic"]
        assert ops["first_attempt"] == {"tool": 513, "blank_no_tool": 53, "full_no_tool": 34} and ops["final"]["tool"] == 549
        assert d["merged_counts"]["calls"] == {"core_setup1": 1800, "core_setup3": 1800, "s2_retest_setup2": 1800}

# ================================================================ 1b. archive / replacement (synthetic core + re-test)
def _rec(cid, vendor, setup, pid, var, rep, **kw):
    return {"call_id": cid, "run_id": "x", "vendor": vendor, "model": f"m-{vendor}", "effort": "none", "setup": setup, "profile_id": pid, "variant": var,
            "repeat": rep, "attempt": 1, "tool_called": True if setup == 2 else None, "parsed": {"letter": "Dear applicant", "valid": True}, **kw}

def _mk_runs(tmp_path, retest_extra=None, drop_one=False):
    core, ret = tmp_path / "runs" / "core1", tmp_path / "runs" / "ret1"
    for d in (core, ret): d.mkdir(parents=True)
    grid = [(v, p, var, r) for v in ("openai", "anthropic") for p in ("P000", "P001") for var in ("base", "name_b") for r in (0, 1)]
    cl, jl, n = [], [], 0
    for s in (3, 1, 2):
        for v, p, var, r in grid:
            n += 1; kw = {}
            if s == 2 and v == "anthropic" and p == "P000" and var == "base":
                kw = ({"attempt": 2, "attempt1_tool_calls": [], "original_error": "letter: Value error, letter empty after strip", "retry_reason": "validation"}
                      if r == 0 else {"tool_called": False})
            cl.append(_rec(f"c{n}", v, s, p, var, r, **kw)); jl.append({"item_id": f"i{n}", "call_id": f"c{n}", "hedging": 1, "spans": []})
    with open(core / "calls.jsonl", "w") as f: f.writelines(json.dumps(x) + "\n" for x in cl)
    with open(core / "judgments.jsonl", "w") as f: f.writelines(json.dumps(x) + "\n" for x in jl)
    json.dump({"run_id": "core1", "phase": "minipilot", "excluded_from_confirmatory": True, "config": {"setups": [3, 1, 2]}}, open(core / "run_meta.json", "w"))
    rl = [_rec(f"r{i}", v, 2, p, var, r) for i, (v, p, var, r) in enumerate(grid[:-1] if drop_one else grid)] + (retest_extra or [])
    with open(ret / "calls.jsonl", "w") as f: f.writelines(json.dumps(x) + "\n" for x in rl)
    with open(ret / "judgments.jsonl", "w") as f: f.writelines(json.dumps({"item_id": f"j{x['call_id']}", "call_id": x["call_id"], "spans": []}) + "\n" for x in rl)
    json.dump({"run_id": "ret1", "phase": "minipilot_s2_retest", "config": {"setups": [2], "replaces_setup2_of": "core1", "amendment_sha256": SR.AMENDMENT_SHA256}},
              open(ret / "run_meta.json", "w"))
    return str(core), str(ret), cl

def _tree_hash(d):
    return {os.path.relpath(p, d): hashlib.sha256(open(p, "rb").read()).hexdigest() for p in glob.glob(os.path.join(d, "**"), recursive=True) if os.path.isfile(p)}

def test_replace_archives_merges_and_points(tmp_path):
    core, ret, cl = _mk_runs(tmp_path); before = {f: open(os.path.join(core, f), "rb").read() for f in ("calls.jsonl", "judgments.jsonl")}
    out = SR.replace(core, ret)
    ad = out["archive"]
    # core sources untouched; archive = the core setup-2 lines byte for byte; read-only; manifest hashes verify
    assert all(open(os.path.join(core, f), "rb").read() == b for f, b in before.items())
    core_s2 = [l for l in before["calls.jsonl"].splitlines(keepends=True) if json.loads(l)["setup"] == 2]
    assert open(os.path.join(ad, "calls_setup2.jsonl"), "rb").read() == b"".join(core_s2)
    assert _mode(ad) == 0o555 and all(_mode(os.path.join(ad, f)) == 0o444 for f in os.listdir(ad)) and _mode(out["pointer"]) == 0o444
    m = json.load(open(os.path.join(ad, "MANIFEST.json")))
    assert all(hashlib.sha256(open(os.path.join(ad, f), "rb").read()).hexdigest() == h["sha256"] for f, h in m["files"].items())
    assert m["counts"] == {"calls": 16, "judgments": 16} and os.path.exists(os.path.join(ad, SR.MARKER))
    ops = json.load(open(os.path.join(ad, "operational.json")))["by_vendor"]
    assert ops["anthropic"]["first_attempt"] == {"tool": 6, "blank_no_tool": 1, "full_no_tool": 1} and ops["anthropic"]["retried"] == 1
    assert ops["openai"]["final"] == {"tool": 8}
    # merged = core setups 1+3 + all re-test setup-2 records; tagged; no ledger
    mc = _calls(out["merged"]); src = {(r["setup"], r["replacement_source"].split(":")[0]) for r in mc}
    assert src == {(1, "core"), (3, "core"), (2, "s2_retest")} and len(mc) == 48
    assert {r["call_id"] for r in mc if r["setup"] == 2} == {f"r{i}" for i in range(16)}
    assert len(_rows(os.path.join(out["merged"], "judgments.jsonl"))) == 48 and not os.path.exists(os.path.join(out["merged"], "spend_ledger.jsonl"))
    prov = json.load(open(os.path.join(out["merged"], "run_meta.json")))["setup2_replacement"]
    assert prov["core_run"] == "core1" and prov["retest_run"] == "ret1" and prov["counts"]["calls"] == {"core_setup1": 16, "core_setup3": 16, "s2_retest_setup2": 16}
    # loaders: core folder and the archive refused; merged dataset loads
    for p in (core, ad):
        with pytest.raises(ValueError, match="superseded"): AN.load_calls(p)
        with pytest.raises(ValueError, match="superseded"): LB.item_pool(p)
        with pytest.raises(ValueError, match="superseded"): JD._no_probe(p)
    assert len(AN.load_calls(out["merged"])) == 48
    # idempotent: a second run verifies and changes nothing
    h1 = _tree_hash(os.path.dirname(core)); SR.replace(core, ret); assert _tree_hash(os.path.dirname(core)) == h1

def test_replace_refuses_bad_inputs(tmp_path):
    core, ret, _ = _mk_runs(tmp_path / "a", drop_one=True)
    with pytest.raises(SystemExit, match="grid"): SR.replace(core, ret)
    assert not os.path.exists(os.path.join(core, SR.ARCHIVE)) and not os.path.exists(os.path.join(core, SR.POINTER))
    core, ret, _ = _mk_runs(tmp_path / "b", retest_extra=[_rec("rx", "openai", 1, "P000", "base", 0)])
    with pytest.raises(SystemExit, match="non-setup-2"): SR.replace(core, ret)
    core, ret, _ = _mk_runs(tmp_path / "c")
    m = json.load(open(os.path.join(ret, "run_meta.json"))); m["config"]["replaces_setup2_of"] = "other"; json.dump(m, open(os.path.join(ret, "run_meta.json"), "w"))
    with pytest.raises(SystemExit, match="replaces_setup2_of"): SR.replace(core, ret)
    core, ret, _ = _mk_runs(tmp_path / "d")
    json.dump({"requests": {"a": {"status": "pending"}}, "batches": {}}, open(os.path.join(ret, "batch_state.json"), "w"))
    with pytest.raises(SystemExit, match="not fully collected"): SR.replace(core, ret)
    core, ret, _ = _mk_runs(tmp_path / "e")
    json.dump({"requests": {}, "batches": {"b1": {"status": "unfetched"}}}, open(os.path.join(ret, "batch_state.json"), "w"))
    with pytest.raises(SystemExit, match="not fully collected"): SR.replace(core, ret)

def test_replace_detects_tampering_and_second_retest(tmp_path):
    core, ret, _ = _mk_runs(tmp_path); out = SR.replace(core, ret)
    other = tmp_path / "runs" / "ret2"; shutil.copytree(ret, other)
    m = json.load(open(other / "run_meta.json")); m["run_id"] = "ret2"; json.dump(m, open(other / "run_meta.json", "w"))
    with pytest.raises(SystemExit, match="already superseded by ret1"): SR.replace(core, str(other))
    ad = out["archive"]; os.chmod(ad, 0o755); p = os.path.join(ad, "operational.json"); os.chmod(p, 0o644)
    open(p, "a").write(" "); os.chmod(ad, 0o555)
    with pytest.raises(SystemExit, match="archive files changed"): SR.replace(core, ret)
    os.chmod(ad, 0o755)                                                                   # let tmp_path cleanup proceed

def test_core_gate_still_reads_superseded_core(tmp_path):
    from llmbias import positive_control as PC
    core, ret, _ = _mk_runs(tmp_path); SR.replace(core, ret)
    json.dump({"requests": {"a": {"status": "done"}}, "batches": {}}, open(os.path.join(core, "batch_state.json"), "w"))
    PC.core_gate(core, ["openai"])                                                        # operational reader: not refused

@pytest.mark.skipif(not os.path.exists(os.path.join(CORE, "calls.jsonl")), reason="core run not present")
def test_real_core_operational_rows_match_memo():
    """Read-only on the real core: the appendix rows reproduce the memo's trigger counts."""
    recs = [json.loads(l) for l in open(os.path.join(CORE, "calls.jsonl"))]
    ops = SR.operational_counts([r for r in recs if r["setup"] == 2])
    a = ops["anthropic"]
    assert a["first_attempt"] == {"tool": 513, "blank_no_tool": 53, "full_no_tool": 34} and a["final"]["tool"] == 549
    assert a["retried"] == 53 and a["retry_final"] == {"tool": 36, "blank_no_tool": 16, "full_no_tool": 1}
    assert ops["openai"]["final"] == {"tool": 600} and ops["google"]["final"] == {"tool": 600}
    assert not os.path.exists(os.path.join(CORE, SR.POINTER)) and not os.path.exists(os.path.join(CORE, SR.ARCHIVE))

# ================================================================ 1c. blind keys: private store only
def test_judge_blind_key_private_only(tmp_path):
    rd = B.start(_cfg(tmp_path, setups=[2], repeats=1), out_root=str(tmp_path / "r"), poll_s=0, limit=2)
    JD.run_judge_batch(rd, poll_s=0)
    run_id = json.load(open(os.path.join(rd, "run_meta.json")))["run_id"]
    assert not glob.glob(os.path.join(rd, "**", "blind_key*"), recursive=True)
    pd_ = os.path.join(LB.PRIVATE, run_id); kp = os.path.join(pd_, "blind_key.jsonl")
    assert _mode(pd_) == 0o700 and _mode(LB.PRIVATE) == 0o700 and _mode(kp) == 0o600
    h = hashlib.sha256(open(kp, "rb").read()).hexdigest(); n = len(_rows(kp))
    assert n == len(_rows(os.path.join(rd, "judgments.jsonl"))) > 0
    assert any(h in l and "blind_key.jsonl" in l for l in open(os.path.join(pd_, "MANIFEST")))
    assert any(l.startswith(f"{h}  {run_id}/blind_key.jsonl  added ") for l in open(os.path.join(LB.PRIVATE, "MANIFEST")))
    assert _mode(os.path.join(pd_, "MANIFEST")) == 0o600 and _mode(os.path.join(LB.PRIVATE, "MANIFEST")) == 0o600
    JD.run_judge(rd)                                                                       # re-judge (sync): old key kept, not overwritten
    old = glob.glob(os.path.join(pd_, "blind_key.superseded-*.jsonl"))
    assert len(old) == 1 and hashlib.sha256(open(old[0], "rb").read()).hexdigest() == h and _mode(old[0]) == 0o600
    assert not glob.glob(os.path.join(rd, "**", "blind_key*"), recursive=True)
    assert sum("blind_key.jsonl" in l for l in open(os.path.join(LB.PRIVATE, "MANIFEST"))) == 2

# ================================================================ 2. unfetched placeholder
class FakePaid(B.MockBackend):
    """MockBackend under a paid vendor name (cumulative ledger exercised). No SDK, no network."""

def _expired_setup(monkeypatch, tmp_path):
    monkeypatch.setitem(B.BACKENDS, "openai", FakePaid)
    B.MockBackend.EXPIRE.add("gpt-5.4-mini")
    return _cfg(tmp_path, setups=[2], repeats=1, models=[{"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}],
                phase="minipilot_s2_retest", budget_usd=3.88, per_vendor_budget_usd={"openai": 1.24, "anthropic": 0.39, "google": 2.25})

def _holds(rd):
    led = os.path.join(rd, "spend_ledger.jsonl"); me = os.path.realpath(led); out = {}
    for name, rows in (("run", read_ledger(led)[0]), ("cumulative", [r for r in read_ledger(os.environ["LLMBIAS_CUMULATIVE_LEDGER"], _cum_key)[0]
                                                                   if os.path.realpath(r.get("ledger") or "") == me])):
        out[name] = {"actual": sum(r["cost"] for r in rows if not r.get("kind")), "net": sum(r["cost"] for r in rows),
                     "ph": {r["batch_id"]: r["cost"] for r in rows if r.get("kind") == "unfetched_placeholder"},
                     "rel": {r["batch_id"]: r["cost"] for r in rows if r.get("kind") == "unfetched_release"}}
    return out

def test_unfetched_batch_books_placeholder_then_releases_on_later_fetch(tmp_path, monkeypatch):
    cfgp = _expired_setup(monkeypatch, tmp_path); fail = {"on": True, "n": 0}
    orig = FakePaid.results
    def results(self, bid):
        if fail["on"]: fail["n"] += 1; raise RuntimeError("results file unavailable")
        return orig(self, bid)
    monkeypatch.setattr(FakePaid, "results", results)
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=2)
    st = json.load(open(os.path.join(rd, "batch_state.json"))); (bid, b), = st["batches"].items()
    assert fail["n"] == B.RESULTS_FETCH_TRIES and b["status"] == "unfetched" and b["ended_status"] == "expired"
    assert b["placeholder"] == pytest.approx(b["worst"]) and not st.get("compliance_stop")
    h = _holds(rd)
    for x in h.values():                                                                  # both ledgers: worst case held, nothing billed yet
        assert x["ph"] == {bid: pytest.approx(b["worst"])} and x["rel"] == {} and x["actual"] == 0 and x["net"] == pytest.approx(b["worst"])
    calls = _calls(rd); assert all("results unfetched" in c["error"] for c in calls)
    meta = json.load(open(os.path.join(rd, "run_meta.json"))); assert meta["unfetched_batches"][bid]["placeholder_usd"] == pytest.approx(b["worst"])
    B.resume(rd, allow_paid=True, poll_s=0)                                              # still failing: hold kept, never released
    st = json.load(open(os.path.join(rd, "batch_state.json"))); assert st["batches"][bid]["status"] == "unfetched"
    assert all(x["rel"] == {} for x in _holds(rd).values())
    fail["on"] = False; B.resume(rd, allow_paid=True, poll_s=0)                          # the fetch works: bill actuals, then release
    st = json.load(open(os.path.join(rd, "batch_state.json"))); b1 = st["batches"][bid]
    assert b1["status"] == "expired" and b1["released"]["placeholder"] == {"run": pytest.approx(b["worst"]), "cumulative": pytest.approx(b["worst"])}
    h = _holds(rd)
    for x in h.values():
        assert x["actual"] > 0 and x["rel"] == {bid: pytest.approx(-b["worst"])} and x["net"] == pytest.approx(x["actual"], abs=1e-12)
    assert h["run"]["actual"] == pytest.approx(h["cumulative"]["actual"], abs=1e-12)
    assert not any(r.get("unfetched") for r in st["requests"].values())
    calls = _calls(rd); assert any(not c.get("error") for c in calls) and not any("results unfetched" in (c.get("error") or "") for c in calls)

def test_placeholder_guard_keyed_and_per_ledger(tmp_path):
    led = str(tmp_path / "l.jsonl"); g = BudgetGuard(10, {}, PRICES, led)
    assert g.placeholder("openai", "gpt-5.4-mini", 0.5, "b1") == 0.5 and g.placeholder("openai", "gpt-5.4-mini", 0.5, "b1") == 0.5
    rows = read_ledger(led)[0]; assert sum(r.get("kind") == "unfetched_placeholder" for r in rows) == 1
    assert g.has_reserve("b1", "placeholder") == {"run": True, "cumulative": True} and g.has_reserve("b1") == {"run": False, "cumulative": False}
    assert g.release_all("openai", "gpt-5.4-mini", "b1") == {"placeholder": {"run": 0.5, "cumulative": 0.5}}
    assert g.release_all("openai", "gpt-5.4-mini", "b1") == {}                            # once only
    assert g.placeholder("openai", "gpt-5.4-mini", 0.5, "b1") == 0.0                      # never re-held after release
    assert g.release("openai", "gpt-5.4-mini", "b2", "placeholder") == {}                 # nothing held -> nothing released
    assert sum(r["cost"] for r in read_ledger(led)[0]) == pytest.approx(0.0, abs=0)

def test_unfetched_with_stop_reserve_books_no_extra_placeholder(tmp_path, monkeypatch):
    """A batch already covered by a compliance-stop reserve keeps that reserve (no second worst-case row)."""
    for v in ("openai", "anthropic"): monkeypatch.setitem(B.BACKENDS, v, FakePaid)
    models = [{"vendor": "anthropic", "model": "claude-haiku-5-5", "effort": "none", "fail": {"p": 1.0, "kind": "no_tool", "attempts": [1]}},
              {"vendor": "openai", "model": "gpt-5.4-mini", "effort": "none"}]
    B.MockBackend.DELAY["gpt-5.4-mini"] = 1
    cfgp = _cfg(tmp_path, setups=[2], repeats=1, models=models, phase="minipilot_s2_retest", budget_usd=3.88,
                per_vendor_budget_usd={"openai": 1.24, "anthropic": 0.39, "google": 2.25})
    orig = FakePaid.results
    def results(self, bid):
        if bid in B.MockBackend.CANCELLED: raise RuntimeError("file service unavailable")
        return orig(self, bid)
    monkeypatch.setattr(FakePaid, "results", results)
    rd = B.start(cfgp, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    (bid, amt), = st["compliance_stop"]["inflight_at_stop"]["batches"].items()
    assert st["batches"][bid]["status"] == "unfetched" and st["batches"][bid]["reserve_kept"] and "placeholder" not in st["batches"][bid]
    assert all(x["ph"] == {} for x in _holds(rd).values())

# ================================================================ 3. response-side logging
def _fake_genai_sig(monkeypatch, sig1=b"\x01sig-one", sig2=b"\x02sig-two-longer"):
    seen = []
    class O:
        def __init__(self, **kw): self.__dict__.update(kw)
    ty = types.ModuleType("google.genai.types")
    for n in ("ThinkingConfig", "Tool", "FunctionDeclaration", "GenerateContentConfig", "AutomaticFunctionCallingConfig", "ToolConfig", "FunctionCallingConfig", "Content"):
        setattr(ty, n, type(n, (O,), {}))
    class Part(O):
        @classmethod
        def from_function_response(cls, name, response): return cls(function_response={"name": name, "response": response}, text=None, function_call=None)
    ty.Part = Part
    class Usage(O):
        def model_dump(self, mode=None, exclude_none=False): return {k: v for k, v in self.__dict__.items() if not (exclude_none and v is None)}
    u1 = Usage(prompt_token_count=100, candidates_token_count=20, thoughts_token_count=3, total_token_count=123, cached_content_token_count=None)
    u2 = Usage(prompt_token_count=180, candidates_token_count=50, thoughts_token_count=4, total_token_count=234)
    args = {"credit_score": 700, "dti_pct": 40.0, "ltv_pct": 80.0, "reserves_months": 3.0, "employment_years": 5}
    final = json.dumps({"decision": "approve", "letter": " ".join(["word"] * 25), "reason_codes": [], "reasons_text": [], "rationale": "ok"})
    def resp(parts, u): return O(candidates=[O(content=O(role="model", parts=parts), finish_reason=O(name="STOP"))], usage_metadata=u, model_version="fake")
    class Models:
        def generate_content(self, model, contents, config):
            seen.append(contents)
            if len(seen) == 1: return resp([O(text=None, thought=False, thought_signature=sig1, function_call=O(name="score_application", args=args))], u1)
            return resp([O(text=final, thought=False, thought_signature=sig2, function_call=None)], u2)
    ge = types.ModuleType("google.genai"); ge.types = ty; ge.Client = lambda api_key=None: O(models=Models())
    g = types.ModuleType("google"); g.genai = ge
    for k, v in (("google", g), ("google.genai", ge), ("google.genai.types", ty)): monkeypatch.setitem(sys.modules, k, v)
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-placeholder")
    return seen

def test_sync_gemini_signature_hash_and_usage_metadata(monkeypatch):
    from llmbias.clients import GoogleClient, signature_digest
    from llmbias.runner import finish_record
    seen = _fake_genai_sig(monkeypatch)
    r = GoogleClient("gemini-3.8-flash", "none").complete("sys", "user", B.TOOL_SPEC, B.tool_exec, setup=2)
    assert len(seen) == 2 and r.reasoning_tokens_by_round == [3, 4] and r.reasoning_tokens == 7
    assert r.usage_metadata == [{"prompt_token_count": 100, "candidates_token_count": 20, "thoughts_token_count": 3, "total_token_count": 123},
                                {"prompt_token_count": 180, "candidates_token_count": 50, "thoughts_token_count": 4, "total_token_count": 234}]
    assert r.thought_signatures == [{"round": 1, "part": 0, "sha256": hashlib.sha256(b"\x01sig-one").hexdigest(), "len": 8},
                                    {"round": 2, "part": 0, "sha256": hashlib.sha256(b"\x02sig-two-longer").hexdigest(), "len": 15}]
    assert signature_digest(base64.b64encode(b"\x01sig-one").decode()) == {"sha256": hashlib.sha256(b"\x01sig-one").hexdigest(), "len": 8}  # batch JSON form agrees
    rec = finish_record({"setup": 2, "vendor": "google", "model": "gemini-3.8-flash", "schema_vendor": "google", "tool_decision": "approve"}, r, 0.0)
    assert rec["thought_signatures"] == r.thought_signatures and rec["usage_metadata"] == r.usage_metadata and rec["reasoning_tokens_by_round"] == [3, 4]
    blob = json.dumps(rec)
    assert "sig-one" not in blob and base64.b64encode(b"\x01sig-one").decode() not in blob     # never the raw value

def test_sync_anthropic_reasoning_by_round(monkeypatch):
    from llmbias.clients import AnthropicClient
    def usage(o, tt): return NS(input_tokens=100, output_tokens=o, output_tokens_details=None if tt is None else NS(thinking_tokens=tt))
    sent = []
    class Messages:
        def create(self, **kw):
            sent.append(kw)
            if len(sent) == 1: return NS(content=[NS(type="tool_use", id="t", name="score_application", input={"credit_score": 700.0})], usage=usage(20, 5), model="h", stop_reason="tool_use")
            return NS(content=[NS(type="text", text='{"decision": "deny"}')], usage=usage(50, 7), model="h", stop_reason="end_turn")
    mod = types.ModuleType("anthropic"); mod.Anthropic = lambda *a, **k: NS(messages=Messages()); monkeypatch.setitem(sys.modules, "anthropic", mod)
    spec = {"name": "score_application", "description": "d", "parameters": {"type": "object"}}
    r = AnthropicClient("claude-haiku-5-5", "none").complete("s", "u", spec, lambda a: {"decision": "deny"}, setup=2)
    assert r.reasoning_tokens_by_round == [5, 7] and r.reasoning_tokens == 12

class FakeGoogleBatch(B.GoogleBackend):
    """GoogleBackend with its real body/parse/next_history, but an in-memory 'server' returning JSON dumps (base64 signatures)."""
    STORE = {}
    def __init__(self): pass
    def submit(self, vendor, model, items):
        bid = f"batches/fake{len(FakeGoogleBatch.STORE)}"; out = {}
        for it in items:
            self.body(it)
            args = {"credit_score": 700, "dti_pct": 40.0, "ltv_pct": 80.0, "reserves_months": 3.0, "employment_years": 5}
            if it["round"] == 1:
                parts = [{"function_call": {"name": "score_application", "args": args}, "thought_signature": base64.b64encode(b"R1" + it["cid"].encode()).decode()}]
                u = {"prompt_token_count": 100, "candidates_token_count": 20, "thoughts_token_count": 2, "total_token_count": 122}
            else:
                assert it["history"][0]["parts"][0]["thought_signature"]                    # round 2 resends the opaque round-1 signature
                fin = json.dumps({"decision": "approve", "letter": " ".join(["word"] * 25), "reason_codes": [], "reasons_text": [], "rationale": "ok"})
                parts = [{"text": fin, "thought_signature": base64.b64encode(b"R2").decode()}]
                u = {"prompt_token_count": 150, "candidates_token_count": 60, "thoughts_token_count": 1, "total_token_count": 211}
            out[it["cid"]] = {"candidates": [{"content": {"role": "model", "parts": parts}, "finish_reason": "STOP"}], "usage_metadata": u, "model_version": "fake"}
        FakeGoogleBatch.STORE[bid] = out; return bid
    def status(self, bid): return "done"
    def cancel(self, bid): pass
    def results(self, bid, order=None): return FakeGoogleBatch.STORE[bid]

def test_batch_gemini_signatures_usage_and_rounds(tmp_path, monkeypatch):
    monkeypatch.setitem(B.BACKENDS, "google", FakeGoogleBatch)
    p = _cfg(tmp_path, setups=[2], repeats=1, models=[{"vendor": "google", "model": "gemini-3.8-flash", "effort": "none"}],
             phase="minipilot_s2_retest", budget_usd=3.88, per_vendor_budget_usd={"openai": 1.24, "anthropic": 0.39, "google": 2.25})
    rd = B.start(p, allow_paid=True, out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    calls = _calls(rd); assert calls
    for c in calls:
        assert c["reasoning_tokens_by_round"] == [2, 1] and c["reasoning_tokens"] == 3
        assert [u["prompt_token_count"] for u in c["usage_metadata"]] == [100, 150]
        s1, s2 = c["thought_signatures"]; raw1 = b"R1" + c["custom_id"].encode()
        assert s1 == {"round": 1, "part": 0, "sha256": hashlib.sha256(raw1).hexdigest(), "len": len(raw1)}
        assert s2 == {"round": 2, "part": 0, "sha256": hashlib.sha256(b"R2").hexdigest(), "len": 2}
        assert base64.b64encode(b"R2").decode() not in json.dumps(c)

def test_batch_mock_records_carry_reasoning_by_round(tmp_path):
    rd = B.start(_cfg(tmp_path, setups=[2, 1], repeats=1), out_root=str(tmp_path / "r"), poll_s=0, limit=1)
    for c in _calls(rd):
        rr = c["reasoning_tokens_by_round"]
        assert len(rr) == (2 if c["setup"] == 2 and c.get("tool_calls") else 1)
        assert c["reasoning_tokens"] == (None if None in rr else sum(rr))

def test_request_fixture_unchanged():
    p = os.path.join(os.path.dirname(__file__), "fixtures", "request_payloads_15_9_1.json")
    assert hashlib.sha256(open(p, "rb").read()).hexdigest().startswith("3fda5441")

# ================================================================ follow-up on e391171
SYN_SIG = bytes([0xFB, 0xEF, 0xFF, 0xFE, 0x3E, 0x3F, 0x00, 0x01, 0xFA]) + b"synthetic-test-signature"   # synthetic, not a real signature

def test_signature_digest_urlsafe_base64_from_batch_json():
    from llmbias.clients import signature_digest
    url = base64.urlsafe_b64encode(SYN_SIG).decode()
    assert "-" in url and "_" in url                                                     # the google-genai JSON alphabet
    want = {"sha256": hashlib.sha256(SYN_SIG).hexdigest(), "len": len(SYN_SIG)}
    for s in (SYN_SIG, url, url.rstrip("="), base64.b64encode(SYN_SIG).decode()):
        assert signature_digest(s) == want

def test_sync_and_batch_signature_digests_match(monkeypatch):
    """The same signature reaches sync as bytes (SDK object) and batch as URL-safe base64 inside model_dump(mode='json')."""
    from llmbias.clients import GoogleClient, thought_signature_digests
    _fake_genai_sig(monkeypatch, sig1=SYN_SIG, sig2=SYN_SIG[::-1])
    r = GoogleClient("gemini-3.8-flash", "none").complete("sys", "user", B.TOOL_SPEC, B.tool_exec, setup=2)
    nat = {"candidates": [{"content": {"role": "model", "parts": [{"function_call": {"name": "score_application", "args": {}},
                                                                    "thought_signature": base64.urlsafe_b64encode(SYN_SIG).decode()}]},
                           "finish_reason": "STOP"}], "usage_metadata": {"prompt_token_count": 1}, "model_version": "fake"}
    p = B.GoogleBackend.parse(None, nat)
    assert thought_signature_digests(p["signature_parts"], 1) == [r.thought_signatures[0]]
    assert r.thought_signatures[1]["sha256"] == hashlib.sha256(SYN_SIG[::-1]).hexdigest()

def test_sync_usage_keeps_thoughts_token_count_null(monkeypatch):
    from llmbias.clients import _usage_dump
    class U:
        def model_dump(self, mode=None, exclude_none=False):
            d = {"prompt_token_count": 10, "candidates_token_count": 5, "thoughts_token_count": None, "cached_content_token_count": None}
            return {k: v for k, v in d.items() if not (exclude_none and v is None)}
    assert _usage_dump(U()) == {"prompt_token_count": 10, "candidates_token_count": 5, "thoughts_token_count": None}
    assert _usage_dump({"thoughts_token_count": 0})["thoughts_token_count"] == 0

def _launcher():
    sys.path.insert(0, P("scripts")); import run_s2_retest as L; return L

def test_probe_report_hand_made_refused(tmp_path, monkeypatch):
    L = _launcher()
    fake = tmp_path / "probe_report.json"; json.dump({"live": True, "pass": True}, open(fake, "w"))
    with pytest.raises(SystemExit, match="runs/probe_s2"): L.check_probe_report(str(fake))
    with pytest.raises(SystemExit, match="PASSING live probe"): L.main(["--live", "--probe-report", str(fake)])
    root = tmp_path / "runs" / "probe_s2"; d = root / "20990101T000000-abcdef-live"; d.mkdir(parents=True)
    monkeypatch.setattr(L, "P", lambda *a: str(tmp_path.joinpath(*a)))
    rp = d / "probe_report.json"; json.dump({"live": True, "pass": True}, open(rp, "w"))
    with pytest.raises(SystemExit, match="marker"): L.check_probe_report(str(rp))
    (d / AN.PROBE_MARKER).write_text("x")
    with pytest.raises(SystemExit, match="amendment_sha256"): L.check_probe_report(str(rp))
    json.dump({"live": True, "pass": True, "amendment_sha256": SR.AMENDMENT_SHA256, "phase": "minipilot"}, open(rp, "w"))
    with pytest.raises(SystemExit, match="phase"): L.check_probe_report(str(rp))
    json.dump({"live": False, "pass": True, "amendment_sha256": SR.AMENDMENT_SHA256, "phase": "minipilot_s2_probe"}, open(rp, "w"))
    with pytest.raises(SystemExit, match="live"): L.check_probe_report(str(rp))
    json.dump({"live": True, "pass": True, "amendment_sha256": SR.AMENDMENT_SHA256, "phase": "minipilot_s2_probe"}, open(rp, "w"))
    assert L.check_probe_report(str(rp))["pass"] is True

REAL_PROBE = P("runs", "probe_s2", "20261020T181308-684ca6-live", "probe_report.json")

@pytest.mark.skipif(not os.path.exists(REAL_PROBE), reason="live probe report not present in this checkout")
def test_real_probe_report_passes_gate():
    rep = _launcher().check_probe_report(REAL_PROBE)
    assert rep["live"] is True and rep["pass"] is True and rep["probe_id"] == "20261020T181308-684ca6-live"
