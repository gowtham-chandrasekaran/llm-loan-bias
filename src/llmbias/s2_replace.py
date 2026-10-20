"""15.9.1 item 5 (amendment memos/draft_15.9.1.md, logged sha256 5041167f...): the setup-2 re-test data replace the core
mini-pilot's setup-2 data UNCONDITIONALLY, whatever the re-test shows. The superseded data are archived read-only, never
analysed for effects, and reported only as operational rows (first-attempt and final counts per vendor) in an appendix.
Setups 1 and 3 from the core are unchanged.

  python -m llmbias.s2_replace --core runs/<core> --retest runs/<retest> [--out runs/<merged>]

Steps (each idempotent; nothing is deleted and the core calls.jsonl / judgments.jsonl are never modified):
 1. validate: core phase minipilot, re-test phase minipilot_s2_retest with setups [2] and replaces_setup2_of = core run id,
    neither a probe, every re-test batch collected (none pending / submitted / unfetched), and the re-test covers exactly
    the core's setup-2 grid (vendor, model, profile, variant, repeat): 1 record per cell, both sides.
 2. archive: <core>/superseded_setup2_15_9_1/ = calls_setup2.jsonl (the core setup-2 lines, byte for byte),
    judgments_setup2.jsonl (their judgments), operational.json (appendix rows), MANIFEST.json (sha256 of every file and of
    the core sources) and a SUPERSEDED marker; files 0444, folder 0555. Re-running verifies the hashes instead of rewriting.
 3. merge: <out>/ = calls.jsonl (core setup 1 + 3 lines + re-test setup-2 lines, each tagged replacement_source),
    judgments.jsonl (the same split), run_meta.json (core meta + setup2_replacement provenance with hashes). No ledger.
 4. pointer: <core>/SETUP2_SUPERSEDED.json (0444). From then on the analysis, label and judge loaders refuse the core
    folder (and anything inside the archive) and name the merged folder (analysis.refuse_superseded). Operational readers
    (positive_control.core_gate) still read the core batch state."""
import argparse, collections, datetime as dt, hashlib, json, os, shutil, stat

AMENDMENT_SHA256 = "5041167fe1ed230086a89b940d7c54baa639aaccae77c4f1487885919de8e85b"
CORE_RUN = "20261018T124607-e5e8b45a-1a973e-batch"
RETEST_PHASE = "minipilot_s2_retest"
ARCHIVE = "superseded_setup2_15_9_1"
POINTER = "SETUP2_SUPERSEDED.json"
MARKER = "SUPERSEDED"
VENDORS = ("openai", "anthropic", "google")

def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def _lines(p):
    """[(raw line bytes, parsed record)] of a jsonl file (blank lines skipped)."""
    with open(p, "rb") as f: return [(l, json.loads(l)) for l in f if l.strip()]

def _meta(rd): return json.load(open(os.path.join(rd, "run_meta.json")))

def vendor_of(r, allow_mock=False):
    v = r["vendor"]
    if v == "mock" and allow_mock: return r.get("schema_vendor") or v
    return v

def cell(r, allow_mock=False):
    return (vendor_of(r, allow_mock), r["profile_id"], r["variant"], int(r["repeat"]))

# ------------------------------------------------------------------ appendix: operational rows only
def classify(r):
    """First-attempt and final outcome of a setup-2 record: tool / blank_no_tool / full_no_tool / error. First attempt:
    attempt-2 records carry attempt1_tool_calls and the retry reason (blank letter -> validation retry)."""
    letter = lambda x: bool(((x.get("parsed") or {}).get("letter") or "").strip())
    if r.get("attempt", 1) == 1:
        first = "error" if r.get("error") and not r.get("tool_called") else ("tool" if r.get("tool_called") else ("full_no_tool" if letter(r) else "blank_no_tool"))
    elif r.get("attempt1_tool_calls"): first = "tool"
    else: first = "blank_no_tool" if "letter empty" in (r.get("original_error") or "") else "full_no_tool"
    final = "error" if r.get("error") else ("tool" if r.get("tool_called") else ("full_no_tool" if letter(r) else "blank_no_tool"))
    return first, final

def operational_counts(records, allow_mock=False):
    out = {}
    for r in records:
        v = vendor_of(r, allow_mock); first, final = classify(r)
        d = out.setdefault(v, {"n": 0, "first_attempt": collections.Counter(), "final": collections.Counter(), "retried": 0,
                               "retry_final": collections.Counter()})
        d["n"] += 1; d["first_attempt"][first] += 1; d["final"][final] += 1
        if r.get("attempt", 1) == 2: d["retried"] += 1; d["retry_final"][final] += 1
    return {v: {k: (dict(x) if isinstance(x, collections.Counter) else x) for k, x in d.items()} for v, d in sorted(out.items())}

# ------------------------------------------------------------------ 1. validate
def _batch_open(rd):
    p = os.path.join(rd, "batch_state.json")
    if not os.path.exists(p): return None
    st = json.load(open(p))
    return {"requests_open": sum(r["status"] in ("pending", "submitted") for r in st["requests"].values()),
            "batches_unfetched": sorted(b for b, x in st["batches"].items() if x["status"] in ("submitted", "unfetched"))}

def validate(core_rd, retest_rd, allow_mock=False):
    from .analysis import refuse_probe_run
    refuse_probe_run(core_rd); refuse_probe_run(retest_rd)
    cm, rm = _meta(core_rd), _meta(retest_rd); rc = rm.get("config") or {}
    if cm.get("phase") != "minipilot": raise SystemExit(f"REFUSING: core run phase {cm.get('phase')!r} is not minipilot")
    if rm.get("phase") != RETEST_PHASE or rc.get("setups") != [2]:
        raise SystemExit(f"REFUSING: re-test run must be phase {RETEST_PHASE} with setups [2] (got {rm.get('phase')!r}, {rc.get('setups')})")
    if rc.get("replaces_setup2_of") != cm["run_id"]:
        raise SystemExit(f"REFUSING: re-test replaces_setup2_of {rc.get('replaces_setup2_of')!r} != core run {cm['run_id']!r}")
    if rc.get("amendment_sha256") != AMENDMENT_SHA256: raise SystemExit("REFUSING: re-test config amendment_sha256 is not the logged 15.9.1 hash")
    ob = _batch_open(retest_rd)
    if ob and (ob["requests_open"] or ob["batches_unfetched"]):
        raise SystemExit(f"REFUSING: re-test run is not fully collected ({ob}); resume it first")
    core = [x for x in _lines(os.path.join(core_rd, "calls.jsonl")) if x[1].get("setup") == 2]
    new = _lines(os.path.join(retest_rd, "calls.jsonl"))
    if any(r.get("setup") != 2 for _, r in new): raise SystemExit("REFUSING: re-test calls.jsonl holds non-setup-2 records")
    if any(r["vendor"] == "mock" for _, r in new) and not allow_mock: raise SystemExit("REFUSING: re-test holds mock records (dry run); pass allow_mock only for a dry run")
    cc, nc = collections.Counter(cell(r) for _, r in core), collections.Counter(cell(r, allow_mock) for _, r in new)
    if any(n != 1 for n in cc.values()) or any(n != 1 for n in nc.values()):
        raise SystemExit("REFUSING: duplicate (vendor, profile, variant, repeat) cells in core or re-test setup 2")
    if set(cc) != set(nc):
        raise SystemExit(f"REFUSING: re-test grid != core setup-2 grid (missing {len(set(cc) - set(nc))}, extra {len(set(nc) - set(cc))})")
    if not allow_mock:
        cmod = {r["vendor"]: r["model"] for _, r in core}; nmod = {r["vendor"]: r["model"] for _, r in new}
        if cmod != nmod: raise SystemExit(f"REFUSING: re-test models {nmod} != core models {cmod}")
    for name in ("judgments.jsonl",):
        if not os.path.exists(os.path.join(retest_rd, name)): raise SystemExit(f"REFUSING: re-test has no {name}; judge it first")
    return core, new

# ------------------------------------------------------------------ 2. archive
def _ro(path, mode): os.chmod(path, mode)

def archive(core_rd, retest_id, core_lines=None, allow_mock=False):
    """Archive the core setup-2 records + judgments read-only. Idempotent: an existing archive is verified, not rewritten."""
    ad = os.path.join(core_rd, ARCHIVE); mp = os.path.join(ad, "MANIFEST.json")
    src = {n: _sha(os.path.join(core_rd, n)) for n in ("calls.jsonl", "judgments.jsonl")}
    if os.path.exists(mp):
        m = json.load(open(mp))
        if m["sources"] != src: raise SystemExit(f"REFUSING: core sources changed since archiving ({m['sources']} vs {src})")
        bad = [f for f, h in m["files"].items() if _sha(os.path.join(ad, f)) != h["sha256"]]
        if bad: raise SystemExit(f"REFUSING: archive files changed: {bad}")
        return ad, m
    if os.path.exists(ad): raise SystemExit(f"REFUSING: {ad} exists without MANIFEST.json (interrupted archive?); inspect by hand")
    core_lines = core_lines if core_lines is not None else [x for x in _lines(os.path.join(core_rd, "calls.jsonl")) if x[1].get("setup") == 2]
    ids = {r["call_id"] for _, r in core_lines}
    jl = [x for x in _lines(os.path.join(core_rd, "judgments.jsonl")) if x[1].get("call_id") in ids]
    tmp = ad + ".tmp"
    if os.path.exists(tmp):
        for root, dirs, files in os.walk(tmp): os.chmod(root, 0o755)
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    with open(os.path.join(tmp, "calls_setup2.jsonl"), "wb") as f: f.writelines(l if l.endswith(b"\n") else l + b"\n" for l, _ in core_lines)
    with open(os.path.join(tmp, "judgments_setup2.jsonl"), "wb") as f: f.writelines(l if l.endswith(b"\n") else l + b"\n" for l, _ in jl)
    ops = {"note": "15.9.1 item 5: operational rows only (appendix). These records are superseded by the setup-2 re-test and "
                   "are never analysed for effects.", "core_run": os.path.basename(os.path.normpath(core_rd)), "replaced_by": retest_id,
           "by_vendor": operational_counts([r for _, r in core_lines], allow_mock)}
    json.dump(ops, open(os.path.join(tmp, "operational.json"), "w"), indent=1)
    open(os.path.join(tmp, MARKER), "w").write(f"Superseded core setup-2 data (15.9.1 item 5, sha256 {AMENDMENT_SHA256[:8]}): read-only archive, "
                                               f"never analysed for effects; replaced by re-test {retest_id}.\n")
    files = {f: {"sha256": _sha(os.path.join(tmp, f)), "lines": sum(1 for _ in open(os.path.join(tmp, f)))} for f in sorted(os.listdir(tmp))}
    m = {"amendment_sha256": AMENDMENT_SHA256, "item": "15.9.1 item 5", "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
         "core_run": ops["core_run"], "replaced_by": retest_id, "sources": src, "files": files,
         "counts": {"calls": len(core_lines), "judgments": len(jl)}}
    json.dump(m, open(os.path.join(tmp, "MANIFEST.json"), "w"), indent=1)
    for f in os.listdir(tmp): _ro(os.path.join(tmp, f), 0o444)
    os.replace(tmp, ad); _ro(ad, 0o555)
    return ad, m

# ------------------------------------------------------------------ 3. merge
def merge(core_rd, retest_rd, out_rd, archive_manifest, allow_mock=False):
    cm, rm = _meta(core_rd), _meta(retest_rd)
    if os.path.exists(os.path.join(out_rd, "run_meta.json")):
        m = _meta(out_rd)["setup2_replacement"]
        if m["core_run"] != cm["run_id"] or m["retest_run"] != rm["run_id"]: raise SystemExit(f"REFUSING: {out_rd} is a different merge")
        for f, h in m["files"].items():
            if _sha(os.path.join(out_rd, f)) != h: raise SystemExit(f"REFUSING: merged {f} changed since it was written")
        return out_rd, m
    os.makedirs(out_rd, exist_ok=True)
    def tag(rec, src): return json.dumps({**rec, "replacement_source": src}) + "\n"
    n = collections.Counter(); keep_ids = set()
    with open(os.path.join(out_rd, "calls.jsonl"), "w") as f:
        for _, r in _lines(os.path.join(core_rd, "calls.jsonl")):
            if r.get("setup") == 2: continue
            f.write(tag(r, f"core:{cm['run_id']}")); n[("core", r["setup"])] += 1; keep_ids.add(r["call_id"])
        new_ids = set()
        for _, r in _lines(os.path.join(retest_rd, "calls.jsonl")):
            f.write(tag(r, f"s2_retest:{rm['run_id']}")); n[("s2_retest", 2)] += 1; new_ids.add(r["call_id"])
    if keep_ids & new_ids: raise SystemExit("REFUSING: call_id collision between core and re-test")
    nj = collections.Counter()
    with open(os.path.join(out_rd, "judgments.jsonl"), "w") as f:
        for _, j in _lines(os.path.join(core_rd, "judgments.jsonl")):
            if j["call_id"] in keep_ids: f.write(tag(j, f"core:{cm['run_id']}")); nj["core"] += 1
        for _, j in _lines(os.path.join(retest_rd, "judgments.jsonl")):
            if j["call_id"] in new_ids: f.write(tag(j, f"s2_retest:{rm['run_id']}")); nj["s2_retest"] += 1
    prov = {"amendment_sha256": AMENDMENT_SHA256, "item": "15.9.1 item 5 (unconditional replacement)", "core_run": cm["run_id"], "retest_run": rm["run_id"],
            "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "dry_run": bool(allow_mock),
            "sources": {"core_calls_sha256": _sha(os.path.join(core_rd, "calls.jsonl")), "core_judgments_sha256": _sha(os.path.join(core_rd, "judgments.jsonl")),
                        "retest_calls_sha256": _sha(os.path.join(retest_rd, "calls.jsonl")), "retest_judgments_sha256": _sha(os.path.join(retest_rd, "judgments.jsonl"))},
            "archive": {"path": os.path.join(cm["run_id"], ARCHIVE), "manifest_files": archive_manifest["files"]},
            "counts": {"calls": {f"{k[0]}_setup{k[1]}": v for k, v in sorted(n.items())}, "judgments": dict(nj)},
            "spend": "not part of this folder: spend stays in the core and re-test run ledgers and the cumulative ledger"}
    prov["files"] = {f: _sha(os.path.join(out_rd, f)) for f in ("calls.jsonl", "judgments.jsonl")}
    meta = {**cm, "run_id": f"{cm['run_id']}+s2retest", "mode": "merged", "setup2_replacement": prov,
            "excluded_from_confirmatory": cm.get("excluded_from_confirmatory", True)}
    json.dump(meta, open(os.path.join(out_rd, "run_meta.json"), "w"), indent=2, default=str)
    return out_rd, prov

# ------------------------------------------------------------------ 4. pointer + driver
def replace(core_rd, retest_rd, out_rd=None, allow_mock=False):
    core_rd, retest_rd = os.path.abspath(core_rd), os.path.abspath(retest_rd)
    rid = _meta(retest_rd)["run_id"]
    out_rd = os.path.abspath(out_rd or os.path.join(os.path.dirname(core_rd), f"{os.path.basename(core_rd)}+s2retest"))
    pp = os.path.join(core_rd, POINTER)
    if os.path.exists(pp):
        p = json.load(open(pp))
        if p["retest_run"] != rid: raise SystemExit(f"REFUSING: core setup 2 already superseded by {p['retest_run']}")
    core_lines, _ = validate(core_rd, retest_rd, allow_mock)
    ad, am = archive(core_rd, rid, core_lines, allow_mock)
    _, prov = merge(core_rd, retest_rd, out_rd, am, allow_mock)
    if not os.path.exists(pp):
        json.dump({"superseded": "setup 2 (15.9.1 item 5)", "retest_run": rid, "merged_dataset": out_rd, "archive": ad,
                   "archive_manifest_sha256": _sha(os.path.join(ad, "MANIFEST.json")), "created": prov["created"],
                   "rule": "analyse the merged dataset; this folder's setup-2 records are archived and never analysed"}, open(pp, "w"), indent=1)
        _ro(pp, 0o444)
    return {"archive": ad, "merged": out_rd, "pointer": pp, "archive_counts": am["counts"], "merged_counts": prov["counts"],
            "operational": json.load(open(os.path.join(ad, "operational.json")))["by_vendor"]}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", required=True); ap.add_argument("--retest", required=True); ap.add_argument("--out")
    a = ap.parse_args(); print(json.dumps(replace(a.core, a.retest, a.out), indent=1))
