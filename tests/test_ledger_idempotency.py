"""Spend-ledger idempotency: keyed rows (call_id, attempt, round, batch_id) are written at most once per ledger; collect()
progress is journaled per result and sync attempts are journaled before billing, so a crash partway through and a resume
give the same totals as an uninterrupted run. Existing (unkeyed) ledgers load and total exactly as before."""
import glob, hashlib, json, os, pytest, yaml
from llmbias.build_data import P
from llmbias import batch as B, runner as RN, clients as CL
from llmbias.budget import BudgetGuard, load_prices, cumulative_spend, read_ledger, ledger_key, CUMULATIVE_SEED

PRICES = load_prices(P("configs", "prices.yaml"))
EMU3 = [{"vendor": "mock", "model": f"mock-{v}", "effort": "none", "emulate": v} for v in ("openai", "anthropic", "google")]

def _rows(p): return [json.loads(l) for l in open(p)] if os.path.exists(p) else []
def _calls(rd): return [json.loads(l) for l in open(os.path.join(rd, "calls.jsonl"))]

# ---------------------------------------------------------------- unit: re-recording a key
def test_rerecord_same_key_does_not_change_totals(tmp_path):
    led, cum = str(tmp_path / "run" / "spend_ledger.jsonl"), str(tmp_path / "cum.jsonl"); os.makedirs(tmp_path / "run")
    g = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum)
    k = {"attempt": 1, "round": 1, "batch_id": "b1"}
    c = g.record("openai", "gpt-5.4-mini", 1000, 200, "cid1", batch=True, key=k)
    before = (g.spent, len(_rows(led)), len(_rows(cum)), sum(cumulative_spend(cum).values()))
    for _ in range(3): assert g.record("openai", "gpt-5.4-mini", 1000, 200, "cid1", batch=True, key=k) == c
    assert (g.spent, len(_rows(led)), len(_rows(cum)), sum(cumulative_spend(cum).values())) == before
    g2 = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum)                 # a fresh guard (resume) knows the key too
    g2.record("openai", "gpt-5.4-mini", 1000, 200, "cid1", batch=True, key=k)
    assert g2.spent == pytest.approx(c) and len(_rows(led)) == 1 and len(_rows(cum)) == 1
    # setup-2 round 2 shares the call_id but is a different key; so are a retry (attempt 2) and a re-submitted batch
    for kk in ({"attempt": 1, "round": 2, "batch_id": "b2"}, {"attempt": 2, "round": 1, "batch_id": "b3"}, {"attempt": 1, "round": 1, "batch_id": "b9"}):
        g2.record("openai", "gpt-5.4-mini", 1000, 200, "cid1", batch=True, key=kk)
    assert len(_rows(led)) == 4 and g2.spent == pytest.approx(4 * c) and sum(cumulative_spend(cum).values()) == pytest.approx(sum(CUMULATIVE_SEED.values()) + 4 * c)
    # unkeyed (legacy / sync judge: several calls under one item_id) always counts
    g2.record("openai", "gpt-5-nano", 100, 10, "item"); g2.record("openai", "gpt-5-nano", 100, 10, "item")
    assert len(_rows(led)) == 6 and sum(1 for r in _rows(led) if ledger_key(r) is None) == 2

def test_crash_between_run_and_cumulative_append_is_repaired(tmp_path):
    led, cum = str(tmp_path / "spend_ledger.jsonl"), str(tmp_path / "cum.jsonl")
    g = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum); k = {"attempt": 1, "round": 1, "batch_id": "b1"}
    g.record("anthropic", "claude-haiku-5-5", 3000, 600, "c", batch=True, key=k)
    open(cum, "w").close()                                                       # the cumulative append was lost in the crash
    g = BudgetGuard(10, {}, PRICES, led, cumulative_path=cum); g.record("anthropic", "claude-haiku-5-5", 3000, 600, "c", batch=True, key=k)
    assert len(_rows(led)) == 1 and len(_rows(cum)) == 1 and os.path.isabs(_rows(cum)[0]["ledger"])

def test_cumulative_spend_drops_duplicate_keyed_rows_only(tmp_path):
    cum = tmp_path / "cum.jsonl"
    row = {"call_id": "c", "vendor": "google", "model": "m", "in": 1, "out": 1, "batch": True, "cost": 0.5, "ledger": str(tmp_path / "l"), "phase": None}
    keyed = {**row, "attempt": 1, "round": 2, "batch_id": "b"}
    cum.write_text("".join(json.dumps(r) + "\n" for r in (row, row, keyed, keyed)))
    assert cumulative_spend(str(cum))["google"] == pytest.approx(CUMULATIVE_SEED["google"] + 1.5)

# ---------------------------------------------------------------- batch: crash partway through collect, then resume
def _bcfg(tmp_path):
    cfg = yaml.safe_load(open(P("configs", "pilot_batch_mock.yaml")))
    cfg.update(setups=[3, 2], repeats=1, models=EMU3, budget_usd=50, per_vendor_budget_usd={"mock": 50})
    p = tmp_path / "b.yaml"; yaml.safe_dump(cfg, open(p, "w")); return str(p)

def _nat(rec): return (rec["schema_vendor"], rec["setup"], rec["profile_id"], rec["variant"], rec["repeat"])

def _summary(rd, id_field="custom_id"):
    led = _rows(os.path.join(rd, "spend_ledger.jsonl")); calls = _calls(rd)
    keys = [ledger_key(r) for r in led]
    assert None not in keys and len(keys) == len(set(keys))                    # every row keyed, no key twice
    nat = {c[id_field]: _nat(c) for c in calls}
    led_nat = sorted((nat[r["call_id"]], r["attempt"], r["round"], round(r["cost"], 12)) for r in led)
    return {"total": round(sum(r["cost"] for r in led), 12), "n_rows": len(led), "ledger": led_nat,
            "calls": sorted((_nat(c), c.get("attempt"), round(c.get("cost_usd") or 0, 12), c.get("response"), json.dumps(c.get("tool_calls"), sort_keys=True))
                            for c in calls),
            "spent_meta": round(json.load(open(os.path.join(rd, "run_meta.json")))["spent_usd"], 6)}

def _only_run(root): return next(d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d))

@pytest.mark.parametrize("where,k", [("before_ledger", 7), ("after_ledger", 7), ("after_journal", 7), ("after_ledger", 13)])
def test_batch_crash_mid_collect_then_resume_matches_clean_run(tmp_path, monkeypatch, where, k):
    B.MockBackend.STORE.clear()
    clean = _summary(B.start(_bcfg(tmp_path), out_root=str(tmp_path / "clean"), limit=2, poll_s=0))
    assert any(r[2] == 2 for r in clean["ledger"]) and any(r[2] == 1 and r[0][1] == 2 for r in clean["ledger"])   # setup-2 rounds 1 + 2 both billed
    B.MockBackend.STORE.clear(); n = {"i": 0}
    class Crash(Exception): pass
    orig_rec, orig_j = B.BudgetGuard.record, B.BatchRun._journal
    def rec(self, *a, **kw):
        n["i"] += 1
        if where == "before_ledger" and n["i"] == k: raise Crash
        out = orig_rec(self, *a, **kw)
        if where == "after_ledger" and n["i"] == k: raise Crash                # ledger row written, progress not yet persisted
        return out
    def jour(self, *a):
        orig_j(self, *a)
        if where == "after_journal" and n["i"] == k: raise Crash
    monkeypatch.setattr(B.BudgetGuard, "record", rec); monkeypatch.setattr(B.BatchRun, "_journal", jour)
    with pytest.raises(Crash): B.start(_bcfg(tmp_path), out_root=str(tmp_path / "crash"), limit=2, poll_s=0)
    monkeypatch.setattr(B.BudgetGuard, "record", orig_rec); monkeypatch.setattr(B.BatchRun, "_journal", orig_j)
    rd = _only_run(str(tmp_path / "crash"))
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert any(b["status"] == "submitted" for b in st["batches"].values())     # crashed inside collect
    n_before = len(_rows(os.path.join(rd, "spend_ledger.jsonl")))
    assert n_before == (k - 1 if where == "before_ledger" else k)
    B.resume(rd, poll_s=0)
    assert _summary(rd) == clean
    st = json.load(open(os.path.join(rd, "batch_state.json")))
    assert not os.path.exists(os.path.join(rd, "batch_state.json.collect_journal.jsonl"))
    assert all(r["status"] == "done" for r in st["requests"].values()) and all("collected" not in b for b in st["batches"].values())
    if where == "after_journal": assert st["journal_replays"][0]["entries"] >= 1

def test_batch_torn_journal_line_is_redone_once(tmp_path, monkeypatch):
    B.MockBackend.STORE.clear()
    clean = _summary(B.start(_bcfg(tmp_path), out_root=str(tmp_path / "clean"), limit=2, poll_s=0))
    B.MockBackend.STORE.clear(); n = {"i": 0}
    class Crash(Exception): pass
    def jour(self, bid, cid, r):
        n["i"] += 1
        line = json.dumps({"bid": bid, "cid": cid, "req": r, "compliance_stop": None}, default=str)
        with open(self.jp, "a") as f: f.write(line if n["i"] == 5 else line + "\n")
        if n["i"] == 5: f.close(); raise Crash                                  # half-written last line
    monkeypatch.setattr(B.BatchRun, "_journal", jour)
    with pytest.raises(Crash): B.start(_bcfg(tmp_path), out_root=str(tmp_path / "crash"), limit=2, poll_s=0)
    monkeypatch.undo(); rd = _only_run(str(tmp_path / "crash"))
    open(os.path.join(rd, "batch_state.json.collect_journal.jsonl"), "r+").truncate(os.path.getsize(os.path.join(rd, "batch_state.json.collect_journal.jsonl")) - 10)
    B.resume(rd, poll_s=0); assert _summary(rd) == clean

# ---------------------------------------------------------------- sync: crash partway through, then resume
def _scfg(tmp_path):
    cfg = yaml.safe_load(open(P("configs", "pilot_mock.yaml")))
    models = [dict(m, fail={"p": 0.4, "kind": "empty_letter", "attempts": [1]}) for m in EMU3]   # some calls retried (attempt 2)
    cfg.update(setups=[3, 2], repeats=1, models=models, budget_usd=50, per_vendor_budget_usd={"mock": 50})
    p = tmp_path / "s.yaml"; yaml.safe_dump(cfg, open(p, "w")); return str(p)

@pytest.mark.parametrize("where,k", [("before_ledger", 9), ("after_ledger", 9), ("after_ledger", 20)])
def test_sync_crash_then_resume_matches_clean_run(tmp_path, monkeypatch, where, k):
    sent = {"n": 0}; orig_c = CL.MockClient._complete
    def counting(self, *a, **kw): sent["n"] += 1; return orig_c(self, *a, **kw)
    monkeypatch.setattr(CL.MockClient, "_complete", counting)
    clean = _summary(RN.run(_scfg(tmp_path), out_root=str(tmp_path / "clean"), limit=2), id_field="call_id"); sent_clean = sent["n"]; sent["n"] = 0
    assert any(r[1] == 2 for r in clean["ledger"])                              # retries present
    class Crash(BaseException): pass       # a hard stop (kill / Ctrl-C), not an API error the runner would log and continue past
    n = {"i": 0}; orig_rec = RN.BudgetGuard.record
    def rec(self, *a, **kw):
        n["i"] += 1
        if where == "before_ledger" and n["i"] == k: raise Crash                # attempt journaled, spend not yet recorded
        out = orig_rec(self, *a, **kw)
        if where == "after_ledger" and n["i"] == k: raise Crash                 # spend recorded, call not yet logged
        return out
    monkeypatch.setattr(RN.BudgetGuard, "record", rec)
    with pytest.raises(Crash): RN.run(_scfg(tmp_path), out_root=str(tmp_path / "crash"), limit=2)
    monkeypatch.setattr(RN.BudgetGuard, "record", orig_rec)
    rd = _only_run(str(tmp_path / "crash"))
    assert len(_rows(os.path.join(rd, "spend_ledger.jsonl"))) == (k - 1 if where == "before_ledger" else k)
    RN.resume(rd)
    assert _summary(rd, id_field="call_id") == clean
    assert sent["n"] == sent_clean                                              # no attempt was sent twice (journal reused)

def test_sync_resume_refuses_after_compliance_stop(tmp_path):
    cfg = yaml.safe_load(open(P("configs", "pilot_mock.yaml")))
    cfg.update(setups=[2], repeats=1, models=[dict(EMU3[0], fail={"p": 1.0, "kind": "no_tool", "attempts": [1]})])
    p = tmp_path / "c.yaml"; yaml.safe_dump(cfg, open(p, "w"))
    rd = RN.run(str(p), out_root=str(tmp_path), limit=1)
    with pytest.raises(SystemExit, match="compliance"): RN.resume(rd)

# ---------------------------------------------------------------- existing ledgers load and total as before
PILOT = P("runs", "20261012T143812-ff54324d-4745d6-batch", "raw", "spend_ledger.jsonl")     # tracked in git
EXISTING = {   # path -> (sha256 prefix, rows, total, by vendor)   (untracked ledgers are checked when present)
    PILOT: ("c27c9c8fa539", 15753, 10.5810, {"openai": 1.6328, "anthropic": 6.9397, "google": 2.0085}),
    P("runs", "20261018T124607-e5e8b45a-1a973e-batch", "spend_ledger.jsonl"): ("91b1a0426007", 16393, 3.9873, {"openai": 1.9400, "anthropic": 0.4586, "google": 1.5887}),
    "/workspace/research/smoke_runs/20261017T213809-918ac8fb-64e476/spend_ledger.jsonl": ("5fa7a1d16ce7", 36, 0.0372, {"openai": 0.0183, "anthropic": 0.0040, "google": 0.0149}),
    "/workspace/research/smoke_runs/smoke2-20261017T225139/20261017T225139-9c4f9094-aa232a/spend_ledger.jsonl": ("db7f9710fb52", 24, 0.0380, {"openai": 0.0175, "anthropic": 0.0048, "google": 0.0157}),
}

@pytest.mark.parametrize("path", list(EXISTING), ids=["pilot", "core", "smoke1", "smoke2"])
def test_existing_ledgers_load_and_total_unchanged(path, tmp_path):
    sha, n, total, by = EXISTING[path]
    if path != PILOT and not os.path.exists(path): pytest.skip("untracked ledger not present on this machine")
    assert hashlib.sha256(open(path, "rb").read()).hexdigest().startswith(sha)
    rows, keys = read_ledger(path); assert len(rows) == n and not keys          # pre-fix rows are unkeyed: nothing dropped
    g = BudgetGuard(1e9, {}, PRICES, path, cumulative_path=str(tmp_path / "cum.jsonl"))
    assert round(g.spent, 4) == total and {v: round(x, 4) for v, x in g.by_vendor.items()} == by
    if path == PILOT: assert {v: round(x, 4) for v, x in g.by_vendor.items()} == CUMULATIVE_SEED   # the seed is this ledger

def test_cumulative_ledger_total_unchanged():
    p = P("runs", "cumulative_spend_ledger.jsonl")
    if not os.path.exists(p): pytest.skip("cumulative ledger not present on this machine")
    # the ledger is append-only: its first 16,453 rows are the Oct 20, 2026 state (later runs, e.g. the live probe, append)
    head = b"".join(open(p, "rb").readlines()[:16453])
    if not hashlib.sha256(head).hexdigest().startswith("39fdb4f530d8"): pytest.skip("cumulative ledger prefix differs from Oct 20, 2026")
    tmp = os.path.join(os.path.dirname(os.environ["LLMBIAS_CUMULATIVE_LEDGER"]), "cum_prefix.jsonl"); open(tmp, "wb").write(head)
    c = cumulative_spend(tmp)
    assert round(sum(c.values()), 4) == 14.6435
    assert {v: round(x, 4) for v, x in c.items()} == {"openai": 3.6086, "anthropic": 7.4071, "google": 3.6278}
