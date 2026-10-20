"""Human labeling for M1b (hedging) and M4 (proxy mention) under plan 15.8 / 15.8.1 and rubric v1.0.

  python -m llmbias.labels export      --run runs/<id> [--out /workspace/labeling/pilot-v1]
  python -m llmbias.labels agreement   --run runs/<id> --a <A.xlsx|csv> --b <B.xlsx|csv> [--adjudication <adj.xlsx>]
  (calibration: draw_calibration(...) — defined, not run; plan 15.8 only if a task fails the human-human gate)

Blinding: workbooks carry only opaque random item codes, task, redacted text. The code -> internal mapping (call, variant,
judge label) is written ONLY to /home/box/private/blind_keys/<run_id>/ (mode 700/600)."""
import argparse, datetime as dt, hashlib, json, math, os, random, re, secrets
import numpy as np, pandas as pd

PRIVATE = os.environ.get("LLMBIAS_PRIVATE_DIR", "/home/box/private/blind_keys")
RUBRIC = os.environ.get("LLMBIAS_RUBRIC", "/workspace/research/labeling_rubric.md")
RUBRIC_SHA_PREFIX = "31c4cb2a"; RUBRIC_VERSION = "v1.0"
RUBRIC_V11 = os.environ.get("LLMBIAS_RUBRIC_V11", "/workspace/research/labeling_rubric_v1.1.md"); RUBRIC_V11_SHA_PREFIX = "f4747b61"
RELEASE = "pilot-v1"
SELECT_SEED = 20261012                       # which 180 items (stratified by variant)
LABELER_SEEDS = {"A": 15801, "B": 15802}     # per-labeler order, fixed before labeling (15.8)
LABELERS = {"A": "Gowtham", "B": "colleague"}
VALID = ("1", "0", "unsure")
VARIANT_GROUP = {"base": "base", "name_b": "name", "zip_b": "zip"}
N_HEDGE, N_SPAN = 120, 60
BOOT_B, BOOT_SEED = 10000, 20261012
FORBIDDEN = ("variant", "vendor", "setup", "judge", "model", "call_id", "hedging_judge", "mentions_proxy")


# ------------------------------------------------------------------ private store
def _private_dir(run_id):
    d = os.path.join(PRIVATE, run_id); os.makedirs(d, exist_ok=True)
    for p in (PRIVATE, d): os.chmod(p, 0o700)
    return d

def _write_private(run_id, name, obj):
    p = os.path.join(_private_dir(run_id), name)
    with open(p, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.chmod(p, 0o600); return p

def _read_private(run_id, name):
    return json.load(open(os.path.join(PRIVATE, run_id, name)))

def rubric_text(path=None, prefix=None):
    path = path or RUBRIC; prefix = prefix or RUBRIC_SHA_PREFIX
    t = open(path, "rb").read(); h = hashlib.sha256(t).hexdigest()
    if not h.startswith(prefix): raise SystemExit(f"rubric hash {h} does not match frozen {prefix}…")
    return t.decode(), h


# ------------------------------------------------------------------ item pool / selection
def item_pool(run_dir):
    """All candidate items with internal ids, task, variant group, redacted text and judge label (never exported)."""
    from .analysis import refuse_probe_run, refuse_superseded      # 15.9.1 item 7 / Reviewer cond. (c): probe outputs are never labeled
    refuse_probe_run(run_dir); refuse_superseded(run_dir)          # 15.9.1 item 5: superseded core setup 2 is never labeled
    calls = {}
    for l in open(os.path.join(run_dir, "calls.jsonl")):
        r = json.loads(l); calls[r["call_id"]] = (r["setup"], r["variant"], (r.get("parsed") or {}).get("decision"))
    rows = []
    for l in open(os.path.join(run_dir, "judgments.jsonl")):
        j = json.loads(l); st, v, dec = calls[j["call_id"]]
        if v not in VARIANT_GROUP: continue
        if st == 3 and dec == "deny" and j.get("hedging_status", "done") == "done":
            rows.append(dict(internal_id=j["item_id"], task="hedging", variant=VARIANT_GROUP[v], call_id=j["call_id"],
                             text=j["letter_redacted"], judge=None if j.get("hedging") is None else int(bool(j["hedging"]))))
        for i, s in enumerate(j["spans"]):
            if s.get("status", "done") != "done": continue
            rows.append(dict(internal_id=f"{j['item_id']}#s{i}", task="proxy_mention", variant=VARIANT_GROUP[v], call_id=j["call_id"],
                             text=s["span_redacted"], judge=None if s.get("mentions_proxy") is None else int(bool(s["mentions_proxy"]))))
    return pd.DataFrame(rows)

def select_items(pool, seed=SELECT_SEED, n_hedge=N_HEDGE, n_span=N_SPAN):
    """Stratified by variant group (base/name/zip), equal shares; seeded."""
    out = []
    for task, n in (("hedging", n_hedge), ("proxy_mention", n_span)):
        p = pool[pool.task == task]; k = n // 3
        for g in ("base", "name", "zip"):
            q = p[p.variant == g]; out.append(q.sample(n=min(k, len(q)), random_state=seed))
    return pd.concat(out, ignore_index=True)


# ------------------------------------------------------------------ workbook
def _write_workbook(path, items, seed, rubric, rubric_sha, labeler, version=RUBRIC_VERSION):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook(); ws = wb.active; ws.title = "Items"
    cols = ["item_id", "task", "text", "label", "comment"]; ws.append(cols)
    for c in ws[1]: c.font = Font(bold=True)
    for _, r in items.iterrows(): ws.append([r.item_id, r.task, r.text, None, None])
    for col, w in zip("ABCDE", (14, 15, 100, 10, 40)): ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        row[2].alignment = Alignment(wrap_text=True, vertical="top")
        for c in (row[0], row[1], row[3], row[4]): c.alignment = Alignment(vertical="top", wrap_text=c.column == 5)
        ws.row_dimensions[row[0].row].height = min(409, 15 * max(2, math.ceil(len(str(row[2].value or "")) / 95) + str(row[2].value or "").count("\n")))
    ws.freeze_panes = "A2"
    add_label_validation(ws, "D", len(items) + 1)
    rs = wb.create_sheet("Rubric"); rs.column_dimensions["A"].width = 140
    rs.append([f"Labeling rubric {version}  sha256 {rubric_sha}"]); rs["A1"].font = Font(bold=True)
    for line in rubric.splitlines(): rs.append([line]); rs.cell(rs.max_row, 1).alignment = Alignment(wrap_text=True)
    ab = wb.create_sheet("About"); ab.column_dimensions["A"].width = 28; ab.column_dimensions["B"].width = 110
    for k, v in about_rows(labeler, seed, rubric_sha, items, version): ab.append([k, v]); ab.cell(ab.max_row, 2).alignment = Alignment(wrap_text=True)
    wb.save(path)

def add_label_validation(ws, col, last_row):
    from openpyxl.worksheet.datavalidation import DataValidation
    dv = DataValidation(type="list", formula1='"1,0,unsure"', allow_blank=True, showErrorMessage=True, errorStyle="stop",
                        errorTitle="Invalid label", error="Label must be exactly 1, 0 or unsure.", showDropDown=False)
    ws.add_data_validation(dv); dv.add(f"{col}2:{col}{last_row}")
    return dv

def about_rows(labeler, seed, rubric_sha, items, version=RUBRIC_VERSION):
    c = items.task.value_counts().to_dict()
    return [("Labeler", f"labeler_{labeler}"), ("Order seed", seed), ("Rubric", f"{version}, sha256 {rubric_sha}"),
            ("Items", f"{len(items)} (hedging {c.get('hedging', 0)}, proxy_mention {c.get('proxy_mention', 0)})"),
            ("Created", dt.datetime.now().astimezone().isoformat(timespec="seconds")),
            ("Instructions 1", "Read the Rubric sheet first. Label in the order given, alone; do not discuss items until both labelers have submitted."),
            ("Instructions 2", "label: choose exactly 1, 0 or unsure from the dropdown (other values are rejected)."),
            ("Instructions 3", "comment: optional, but REQUIRED when label is unsure (say what made it unclear). Keep unsure under ~5%."),
            ("Instructions 4", "hedging = Task A (whole letter, M1b); proxy_mention = Task B (flagged span, M4). Judge the text only."),
            ("Instructions 5", "Return the file unchanged apart from the label and comment columns."),
            ("Do not", "Do NOT open the repository's run folders (runs/…), any blind_key file, or any model output outside this workbook until labeling is complete.")]

def export(run_dir, outdir="/workspace/labeling/pilot-v1", seeds=LABELER_SEEDS, select_seed=SELECT_SEED):
    run_id = os.path.basename(os.path.normpath(run_dir)); rubric, rsha = rubric_text()
    os.makedirs(outdir, exist_ok=True)
    sel = select_items(item_pool(run_dir), select_seed)
    key = {"run_id": run_id, "created": dt.datetime.now().astimezone().isoformat(), "rubric_sha256": rsha, "select_seed": select_seed,
           "selected_internal_ids": sel.internal_id.tolist(), "labelers": {}}
    paths = {}
    for lab, seed in seeds.items():
        rng = random.Random(seed)
        order = list(range(len(sel))); rng.shuffle(order)
        items = sel.iloc[order].reset_index(drop=True).copy()
        used = set(); codes = []
        for _ in range(len(items)):
            c = lab + secrets.token_hex(4).upper()
            while c in used: c = lab + secrets.token_hex(4).upper()
            used.add(c); codes.append(c)
        items["item_id"] = codes
        base = os.path.join(outdir, f"labeler_{lab}")
        _write_workbook(base + ".xlsx", items, seed, rubric, rsha, lab)
        items[["item_id", "task", "text"]].assign(label="", comment="").to_csv(base + ".csv", index=False)
        key["labelers"][lab] = {"name": LABELERS[lab], "order_seed": seed, "file": base + ".xlsx",
                                "map": {r.item_id: {"internal_id": r.internal_id, "task": r.task, "variant": r.variant, "call_id": r.call_id, "judge": r.judge}
                                        for r in items.itertuples()}}
        paths[lab] = (base + ".xlsx", base + ".csv", len(items), items.task.value_counts().to_dict())
    kp = _write_private(run_id, "labeling_pilot-v1_key.json", key)
    with open(os.path.join(outdir, "LABELING_METADATA.json"), "w") as f:
        json.dump({"run": "pilot-v1", "rubric_version": RUBRIC_VERSION, "rubric_sha256": rsha, "order_seeds": {f"labeler_{k}": v for k, v in seeds.items()},
                   "n_items": len(sel), "per_task": sel.task.value_counts().to_dict(), "key_location": "Developer-only private store (not in this folder)"}, f, indent=1)
    check_no_leak(outdir)
    return paths, kp

def check_no_leak(outdir):
    """No exported file may contain variant/vendor/setup/judge columns or values."""
    from openpyxl import load_workbook
    for fn in os.listdir(outdir):
        p = os.path.join(outdir, fn)
        if fn.endswith(".csv"):
            cols = [c.lower() for c in pd.read_csv(p, nrows=0).columns]
        elif fn.endswith(".xlsx"):
            cols = [str(c.value).lower() for c in load_workbook(p, read_only=True)["Items"][1]]
        else: continue
        bad = [c for c in cols if any(k in c for k in FORBIDDEN)]
        assert cols[:5] == ["item_id", "task", "text", "label", "comment"] and not bad, f"leak in {p}: {bad}"


# ------------------------------------------------------------------ read-back
class LabelError(ValueError): pass

def norm_label(v):
    if v is None or (isinstance(v, float) and math.isnan(v)): return None
    s = str(v).strip().lower()
    if s in ("1", "1.0"): return "1"
    if s in ("0", "0.0"): return "0"
    if s == "unsure": return "unsure"
    if s == "": return None
    raise LabelError(f"invalid label {v!r}")

def read_labels(path, require_complete=True):
    if path.endswith(".xlsx"):
        df = pd.read_excel(path, sheet_name="Items", dtype=object)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    errs, labs = [], []
    for _, r in df.iterrows():
        try: labs.append(norm_label(r["label"]))
        except LabelError as e: errs.append(f"{r['item_id']}: {e}"); labs.append(None)
    if errs: raise LabelError("; ".join(errs))
    df["label"] = labs
    has_comment = "comment" in df.columns   # _final files (Project Owner) have no comment column -> all comments empty
    if not has_comment: df["comment"] = ""
    df["comment"] = df["comment"].apply(lambda c: "" if c is None or (isinstance(c, float) and math.isnan(c)) else str(c).strip())
    if require_complete and df.label.isna().any(): raise LabelError(f"{int(df.label.isna().sum())} items unlabeled")
    df["unsure_no_comment"] = (df.label == "unsure") & (df.comment == "")   # counted, never an error
    df.attrs["has_comment_column"] = has_comment
    return df[["item_id", "task", "label", "comment", "unsure_no_comment"]]

def map_back(df, run_id, labeler, release=None):
    m = _read_private(run_id, f"labeling_{release or RELEASE}_key.json")["labelers"][labeler]["map"]
    unknown = set(df.item_id) - set(m)
    if unknown: raise LabelError(f"unknown item ids for labeler {labeler}: {sorted(unknown)[:5]}")
    out = df.copy()
    for k in ("internal_id", "variant", "judge"): out[k] = out.item_id.map(lambda c: m[c][k])
    return out


# ------------------------------------------------------------------ agreement statistics
MIN_POSITIVES = 5   # 15.8.3 (5): a rater with < 5 positives makes the task "not evaluable"
NOT_EVALUABLE = "not evaluable"

def _undef(x): return x is None or (isinstance(x, float) and math.isnan(x))

def cohen_kappa(a, b):
    """Cohen's kappa; undefined (NaN) when n == 0 or expected agreement pe == 1 (e.g. both raters constant on one label)."""
    a, b = np.asarray(a, object), np.asarray(b, object); n = len(a)
    if n == 0: return float("nan")
    cats = sorted(set(a) | set(b), key=str); po = float(np.mean(a == b))
    pe = sum(float(np.mean(a == c)) * float(np.mean(b == c)) for c in cats)
    return float("nan") if pe >= 1 - 1e-12 else (po - pe) / (1 - pe)

def gwet_ac1(a, b):
    a, b = np.asarray(a, object), np.asarray(b, object); n = len(a)
    if n == 0: return float("nan")
    cats = sorted(set(a) | set(b), key=str); q = max(len(cats), 2)
    po = float(np.mean(a == b))
    pe = sum(((np.mean(a == c) + np.mean(b == c)) / 2) * (1 - (np.mean(a == c) + np.mean(b == c)) / 2) for c in cats) / (q - 1)
    return float("nan") if pe >= 1 - 1e-12 else (po - pe) / (1 - pe)

def pabak(a, b, k=None):
    a, b = np.asarray(a, object), np.asarray(b, object)
    if len(a) == 0: return float("nan")
    k = k or max(len(set(a) | set(b)), 2); po = float(np.mean(a == b))
    return (k * po - 1) / (k - 1)

def boot_ci(a, b, fn, B=BOOT_B, seed=BOOT_SEED):
    """Percentile CI over item resamples; resamples where the statistic is undefined are dropped (count via boot_undefined)."""
    a, b = np.asarray(a, object), np.asarray(b, object); n = len(a)
    if n == 0: return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed); idx = rng.integers(0, n, size=(B, n))   # same resamples for every statistic
    v = np.array([fn(a[i], b[i]) for i in idx], float); v = v[~np.isnan(v)]
    if len(v) == 0: return (float("nan"), float("nan"))
    return (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))

def n_positive(x): return int(sum(1 for v in x if str(v) == "1"))

def agree_block(a, b, gate=0.70, B=BOOT_B, seed=BOOT_SEED):
    """kappa gate per 15.8 / 15.8.3: pass only if kappa defined, both raters have >= 5 positives, and kappa >= 0.70;
    otherwise 'not evaluable' (counts as a fail). AC1/PABAK are descriptive and set undefined under the same < 5-positive rule."""
    pa, pb = n_positive(a), n_positive(b); few = min(pa, pb) < MIN_POSITIVES
    k = cohen_kappa(a, b)
    if _undef(k) or few:
        status = NOT_EVALUABLE; passed = False
        reason = ("kappa undefined (pe = 1)" if _undef(k) else "") + ("; " if _undef(k) and few else "") + (f"a rater has < {MIN_POSITIVES} positives ({pa}, {pb})" if few else "")
    else:
        status = "pass" if k >= gate else "fail"; passed = k >= gate; reason = ""
    nan = float("nan")
    return {"n": int(len(a)), "positives": [pa, pb],
            "raw_agreement": float(np.mean(np.asarray(a, object) == np.asarray(b, object))) if len(a) else nan,
            "kappa": None if _undef(k) else k, "kappa_ci": boot_ci(a, b, cohen_kappa, B, seed) if not _undef(k) else (nan, nan),
            "gate_status": status, "gate_0.70_pass": bool(passed), "gate_reason": reason,
            "ac1": nan if few else gwet_ac1(a, b), "ac1_ci": (nan, nan) if few else boot_ci(a, b, gwet_ac1, B, seed),
            "pabak": nan if few else pabak(a, b), "pabak_ci": (nan, nan) if few else boot_ci(a, b, pabak, B, seed)}

def prevalence(labels):
    s = pd.Series(labels); s = s[s.isin(["1", "0"])]
    return float((s == "1").mean()) if len(s) else float("nan")


# ------------------------------------------------------------------ agreement + adjudication
def agreement(run_dir, a_path, b_path, adjudication_path=None, adj_out=None, B=BOOT_B, seed=BOOT_SEED, release=None):
    release = release or RELEASE
    run_id = os.path.basename(os.path.normpath(run_dir))
    A = map_back(read_labels(a_path), run_id, "A", release); Bdf = map_back(read_labels(b_path), run_id, "B", release)
    M = A.merge(Bdf[["internal_id", "item_id", "label", "comment", "unsure_no_comment"]], on="internal_id", suffixes=("_A", "_B"))
    text = item_pool(run_dir).set_index("internal_id").text
    res = {"run_id": run_id, "flags_unsure_without_comment": {"A": A[A.unsure_no_comment].item_id.tolist(), "B": Bdf[Bdf.unsure_no_comment].item_id.tolist()},
           "count_unsure_without_comment": {"A": int(A.unsure_no_comment.sum()), "B": int(Bdf.unsure_no_comment.sum())},
           "per_task": {}}
    adj_rows = []
    for task, g in M.groupby("task"):
        both_unsure = (g.label_A == "unsure") & (g.label_B == "unsure")
        hh = g[~both_unsure]                                  # 15.8.1: one-unsure stays (counts as disagreement)
        t = {"n_items": int(len(g)), "both_unsure_excluded": int(both_unsure.sum()),
             "both_unsure_by_variant": g[both_unsure].variant.value_counts().to_dict(),
             "unsure_counts": {"A": int((g.label_A == "unsure").sum()), "B": int((g.label_B == "unsure").sum())},
             "prevalence": {"A": prevalence(g.label_A), "B": prevalence(g.label_B), "judge": float(pd.Series(g.judge).dropna().astype(int).mean())},
             "human_human": agree_block(hh.label_A.values, hh.label_B.values, B=B, seed=seed)}
        for lab in ("A", "B"):
            s = g[g[f"label_{lab}"].isin(["1", "0"]) & g.judge.notna()]
            t[f"{lab}_vs_judge"] = agree_block(s[f"label_{lab}"].values, s.judge.astype(int).astype(str).values, B=B, seed=seed)
            t[f"{lab}_vs_judge"]["excluded_unsure"] = int((g[f"label_{lab}"] == "unsure").sum())
        res["per_task"][task] = t
        dis = hh[hh.label_A != hh.label_B]
        for r in dis.itertuples():
            adj_rows.append(dict(internal_id=r.internal_id, task=task, text=text.get(r.internal_id, ""), label_A=r.label_A, comment_A=r.comment_A,
                                 label_B=r.label_B, comment_B=r.comment_B))
    adj = pd.DataFrame(adj_rows, columns=["internal_id", "task", "text", "label_A", "comment_A", "label_B", "comment_B"])
    res["n_disagreements"] = adj.task.value_counts().to_dict()
    adj_out = adj_out or os.path.join(os.path.dirname(a_path), "adjudication.xlsx")
    write_adjudication(adj, adj_out, run_id, release); res["adjudication_file"] = adj_out
    if adjudication_path:
        res["consensus"] = consensus(M, adjudication_path, run_id, B, seed, release)
    return res

def write_adjudication(adj, path, run_id, release=None):
    release = release or RELEASE
    """Disagreements with opaque adjudication codes (internal ids stay private)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    adj = adj.copy(); adj["adj_id"] = ["J" + secrets.token_hex(4).upper() for _ in range(len(adj))]
    try: allk = _read_private(run_id, f"labeling_{release}_adjudication_key.json")
    except FileNotFoundError: allk = {}
    allk[os.path.basename(path)] = dict(zip(adj.adj_id, adj.internal_id))   # one key per adjudication file
    _write_private(run_id, f"labeling_{release}_adjudication_key.json", allk)
    wb = Workbook(); ws = wb.active; ws.title = "Disagreements"
    cols = ["adj_id", "task", "text", "label_A", "comment_A", "label_B", "comment_B", "adjudicated_label", "reason"]; ws.append(cols)
    for c in ws[1]: c.font = Font(bold=True)
    for r in adj.itertuples(): ws.append([r.adj_id, r.task, r.text, r.label_A, r.comment_A, r.label_B, r.comment_B, None, None])
    for col, w in zip("ABCDEFGHI", (13, 15, 90, 9, 30, 9, 30, 12, 40)): ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2): row[2].alignment = Alignment(wrap_text=True, vertical="top")
    add_label_validation(ws, "H", max(2, len(adj) + 1))
    wb.save(path); return path

def read_adjudication(path, run_id, release=None):
    release = release or RELEASE
    df = pd.read_excel(path, sheet_name="Disagreements", dtype=object)
    km = _read_private(run_id, f"labeling_{release}_adjudication_key.json")[os.path.basename(path)]
    df["adjudicated_label"] = [norm_label(v) for v in df.adjudicated_label]
    df["internal_id"] = df.adj_id.map(km)
    if df.internal_id.isna().any(): raise LabelError("adjudication file has unknown adj_id codes")
    return df

def consensus(M, adjudication_path, run_id, B=BOOT_B, seed=BOOT_SEED, release=None):
    """Agreed labels + adjudicated labels; unresolved (blank or unsure) and both-unsure -> unsure, excluded from judge validation."""
    adj = read_adjudication(adjudication_path, run_id, release).set_index("internal_id").adjudicated_label
    M = M.copy()
    M["consensus"] = np.where(M.label_A == M.label_B, M.label_A, M.internal_id.map(adj))
    M["consensus"] = M.consensus.where(M.consensus.isin(["1", "0"]), "unsure")
    out = {}
    for task, g in M.groupby("task"):
        unresolved = g[g.consensus == "unsure"]
        s = g[(g.consensus != "unsure") & g.judge.notna()]
        blk = agree_block(s.consensus.values, s.judge.astype(int).astype(str).values, B=B, seed=seed)
        out[task] = {"unresolved_or_both_unsure": int(len(unresolved)), "unresolved_by_variant": unresolved.variant.value_counts().to_dict(),
                     "prevalence_consensus": prevalence(g.consensus), "judge_vs_consensus": blk, "judge_validated": blk["gate_0.70_pass"]}
    return out


# ------------------------------------------------------------------ calibration (15.8; defined, not run)
def draw_calibration(run_dir, n=20, seed=15803, outdir="/workspace/labeling/pilot-v1/calibration"):
    """Seeded 20-item calibration set from outside the 180 (both tasks, proportional), logged to the private store."""
    run_id = os.path.basename(os.path.normpath(run_dir))
    used = set(_read_private(run_id, "labeling_pilot-v1_key.json")["selected_internal_ids"])
    pool = item_pool(run_dir); pool = pool[~pool.internal_id.isin(used)]
    nh = round(n * N_HEDGE / (N_HEDGE + N_SPAN))
    sel = pd.concat([pool[pool.task == "hedging"].sample(n=nh, random_state=seed), pool[pool.task == "proxy_mention"].sample(n=n - nh, random_state=seed)])
    sel = sel.sample(frac=1, random_state=seed).reset_index(drop=True)
    sel["item_id"] = ["C" + secrets.token_hex(4).upper() for _ in range(len(sel))]
    os.makedirs(outdir, exist_ok=True); rubric, rsha = rubric_text()
    _write_workbook(os.path.join(outdir, "calibration.xlsx"), sel, seed, rubric, rsha, "calibration")
    _write_private(run_id, "labeling_pilot-v1_calibration_key.json",
                   {"seed": seed, "created": dt.datetime.now().astimezone().isoformat(), "map": {r.item_id: {"internal_id": r.internal_id, "task": r.task, "variant": r.variant} for r in sel.itertuples()}})
    return os.path.join(outdir, "calibration.xlsx")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["export", "agreement"]); ap.add_argument("--run", required=True)
    ap.add_argument("--out", default="/workspace/labeling/pilot-v1"); ap.add_argument("--a"); ap.add_argument("--b"); ap.add_argument("--adjudication")
    a = ap.parse_args()
    if a.cmd == "export": print(json.dumps(export(a.run, a.out), indent=1, default=str))
    else: print(json.dumps(agreement(a.run, a.a, a.b, a.adjudication), indent=1, default=str))


# ------------------------------------------------------------------ pilot-v2 (15.8.3)
RELEASE_V2 = "pilot-v2"
V2_SELECT_SEED = 15810                         # item draw (logged in the private selection key)
V2_LABELER_SEEDS = {"A": 15811, "B": 15812}    # per-labeler order (15.8.3 (4))
V2_N_SPAN, V2_N_HEDGE, V2_N_BORDER = 100, 120, 30
AREA_CUE_RE = re.compile(r"\b(area|areas|neighbou?rhoods?|communit(y|ies)|region(al)?|local|locality|market|located|location|census|tract|urban|rural|city|county|suburb\w*)\b", re.I)
HEDGE_CUE_RE = re.compile(r"unfortunately|we encourage|at this stage|may wish to|responsible", re.I)
# words that would disclose how the set was assembled; checked in every non-item cell, file name and metadata
DISCLOSURE_TERMS = ("enrich", "select", "stratif", "oversampl", "cue", "seed set", "positive", "hedging_judge")
VENDOR_TERMS = ("gpt", "openai", "claude", "anthropic", "gemini", "google", "nano", "sonnet", "haiku", "opus", "flash")

def span_text_feature(text):
    """Observable text feature for M4 strata (never a label): location/person placeholder or area-language cue."""
    from .blind import PLACEHOLDER_RE
    return "cue" if (PLACEHOLDER_RE.search(text) or AREA_CUE_RE.search(text)) else "no_cue"

def _alloc(n, k):
    return [n // k + (1 if i < n % k else 0) for i in range(k)]

def select_v2(pool, seed=V2_SELECT_SEED, long_q=0.9):
    """Returns (items DataFrame with 'stratum', selection log). Strata use text only, except that all judge-positive
    hedging letters and judge-positive spans are included (15.8.3 (3), memo s.5)."""
    from .blind import filter_spans
    rng = np.random.default_rng(seed); groups = ("base", "name", "zip"); out = []; log = {"seed": seed}
    # --- M4: 100 substantive unique spans, cue/no_cue x variant
    sp = pool[pool.task == "proxy_mention"].sort_values("internal_id").to_dict("records")
    kept, n_sal, n_dup = filter_spans(sp, key="text")
    sp = pd.DataFrame(kept); sp["feature"] = sp.text.map(span_text_feature)
    log["m4_pool"] = {"spans": len(pool[pool.task == "proxy_mention"]), "salutation_only_dropped": n_sal, "duplicates_dropped": n_dup,
                      "unique_substantive": len(sp), "by_feature_variant": {f"{a}:{b}": int(n) for (a, b), n in sp.groupby(["feature", "variant"]).size().items()}}
    forced = sp[sp.judge == 1]; chosen = []
    for feat, nf in zip(("cue", "no_cue"), _alloc(V2_N_SPAN, 2)):
        for g, ng in zip(groups, _alloc(nf, 3)):
            f = forced[(forced.feature == feat) & (forced.variant == g)]; rest = sp[(sp.feature == feat) & (sp.variant == g) & ~sp.internal_id.isin(f.internal_id)]
            take = max(ng - len(f), 0)
            pick = rest.iloc[rng.choice(len(rest), size=min(take, len(rest)), replace=False)] if take else rest.iloc[:0]
            chosen.append(pd.concat([f, pick]).assign(stratum=f"m4:{feat}:{g}"))
    m4 = pd.concat(chosen); log["m4_judge_positive_included"] = int((m4.judge == 1).sum())
    # --- M1b: all judge positives, ~30 cue/long borderline, rest random by variant
    h = pool[pool.task == "hedging"].sort_values("internal_id").reset_index(drop=True)
    pos = h[h.judge == 1].assign(stratum="m1b:judge_positive")
    rest = h[~h.internal_id.isin(pos.internal_id)]
    long_cut = float(h.text.str.len().quantile(long_q))
    cue = rest[rest.text.str.contains(HEDGE_CUE_RE) | (rest.text.str.len() >= long_cut)]
    bl = cue.iloc[rng.choice(len(cue), size=min(V2_N_BORDER, len(cue)), replace=False)].assign(stratum="m1b:borderline_cue")
    rest = rest[~rest.internal_id.isin(bl.internal_id)]; n_rand = V2_N_HEDGE - len(pos) - len(bl); rnd = []
    for g, ng in zip(groups, _alloc(n_rand, 3)):
        q = rest[rest.variant == g]; rnd.append(q.iloc[rng.choice(len(q), size=min(ng, len(q)), replace=False)].assign(stratum=f"m1b:random:{g}"))
    m1 = pd.concat([pos, bl] + rnd)
    log["m1b_pool"] = {"letters": len(h), "judge_positive": len(pos), "cue_or_long_candidates": len(cue), "long_cut_chars": long_cut,
                       "cue_regex": HEDGE_CUE_RE.pattern}
    items = pd.concat([m4.drop(columns=["feature"]), m1], ignore_index=True)
    assert items.internal_id.is_unique
    log["counts_by_stratum"] = items.stratum.value_counts().sort_index().to_dict()
    log["counts_by_task_variant"] = {f"{t}:{v}": int(n) for (t, v), n in items.groupby(["task", "variant"]).size().items()}
    return items, log

def _v1_codes(run_id):
    try: k = _read_private(run_id, "labeling_pilot-v1_key.json")
    except FileNotFoundError: return set()
    return {c for L in k["labelers"].values() for c in L["map"]}

def export_v2(run_dir, outdir="/workspace/labeling/pilot-v2", seeds=V2_LABELER_SEEDS, select_seed=V2_SELECT_SEED):
    run_id = os.path.basename(os.path.normpath(run_dir)); rubric, rsha = rubric_text(RUBRIC_V11, RUBRIC_V11_SHA_PREFIX)
    os.makedirs(outdir, exist_ok=True)
    sel, log = select_v2(item_pool(run_dir), select_seed)
    sel = sel.sort_values("internal_id").reset_index(drop=True)      # canonical order before per-labeler shuffles
    taken = _v1_codes(run_id); key = {"run_id": run_id, "release": RELEASE_V2, "created": dt.datetime.now().astimezone().isoformat(),
           "rubric_version": "v1.1", "rubric_sha256": rsha, "select_seed": select_seed, "selected_internal_ids": sel.internal_id.tolist(), "labelers": {}}
    paths = {}; code_rng = random.Random(f"{select_seed}-codes-{secrets.token_hex(8)}")
    for lab, seed in seeds.items():
        order = list(range(len(sel))); random.Random(seed).shuffle(order)
        items = sel.iloc[order].reset_index(drop=True).copy(); codes = []
        for _ in range(len(items)):
            c = "%08X" % code_rng.getrandbits(32)
            while c in taken: c = "%08X" % code_rng.getrandbits(32)
            taken.add(c); codes.append(c)
        items["item_id"] = codes
        base = os.path.join(outdir, f"labeler_{lab}")
        _write_workbook(base + ".xlsx", items, seed, rubric, rsha, lab, version="v1.1")
        items[["item_id", "task", "text"]].assign(label="", comment="").to_csv(base + ".csv", index=False)
        key["labelers"][lab] = {"name": LABELERS[lab], "order_seed": seed, "file": base + ".xlsx",
                                "map": {r.item_id: {"internal_id": r.internal_id, "task": r.task, "variant": r.variant, "call_id": r.call_id,
                                                    "judge": None if pd.isna(r.judge) else int(r.judge), "stratum": r.stratum} for r in items.itertuples()}}
        paths[lab] = (base + ".xlsx", base + ".csv", len(items), items.task.value_counts().to_dict())
    kp = _write_private(run_id, f"labeling_{RELEASE_V2}_key.json", key)
    sp = _write_private(run_id, f"labeling_{RELEASE_V2}_selection.json",
                        {**log, "order_seeds": seeds, "rubric_sha256": rsha, "strata": sel[["internal_id", "task", "variant", "stratum"]].to_dict("records")})
    with open(os.path.join(outdir, "LABELING_METADATA.json"), "w") as f:
        json.dump({"run": RELEASE_V2, "rubric_version": "v1.1", "rubric_sha256": rsha, "order_seeds": {f"labeler_{k}": v for k, v in seeds.items()},
                   "n_items": len(sel), "per_task": sel.task.value_counts().to_dict(), "key_location": "Developer-only private store (not in this folder)"}, f, indent=1)
    check_no_leak(outdir); check_no_disclosure(outdir, rubric)
    return paths, kp, sp, log

def check_no_disclosure(outdir, rubric_text_=""):
    """Every cell outside the item text (headers, About, Rubric title), every file name and the metadata must not
    mention how items were assembled, nor any vendor/model; item texts must not name a vendor/model; nothing private here."""
    from openpyxl import load_workbook
    rub = set(rubric_text_.splitlines())
    def bad(s, terms): s = str(s).lower(); return [t for t in terms if t in s]
    for fn in os.listdir(outdir):
        p = os.path.join(outdir, fn); assert not bad(fn, DISCLOSURE_TERMS + VENDOR_TERMS + ("key",)), f"file name {fn}"
        if fn.endswith(".xlsx"):
            wb = load_workbook(p, read_only=True)
            for ws in wb.worksheets:
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    for j, v in enumerate(row):
                        if v is None: continue
                        is_item_text = ws.title == "Items" and i > 0 and j == 2
                        if ws.title == "Rubric" and str(v) in rub: continue       # verbatim rubric v1.1 lines
                        terms = VENDOR_TERMS if is_item_text else DISCLOSURE_TERMS + VENDOR_TERMS
                        if ws.title == "Items" and i > 0 and j in (0, 1): terms = DISCLOSURE_TERMS
                        b = [t for t in bad(v, terms) if not (is_item_text and t in ("google", "flash", "nano"))]
                        assert not b, f"{fn}:{ws.title} r{i+1}c{j+1}: {b}"
        elif fn.endswith(".json"):
            assert not bad(open(p).read(), DISCLOSURE_TERMS + VENDOR_TERMS + ("variant", "judge", "internal_id", "call_id")), f"{fn}"
        elif fn.endswith(".csv"):
            assert not bad(open(p).readline(), DISCLOSURE_TERMS + VENDOR_TERMS), f"{fn} header"
