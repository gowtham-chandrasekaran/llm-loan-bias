"""Pilot-v2.1 labeling export (plan 15.8.4, rubric v1.2). Rubric and constructed-letter source are read at export time;
their sha256 values go into the metadata and the private key, so a wording change is a plain re-export."""
import datetime as dt, hashlib, json, os, random, re
import numpy as np, pandas as pd
from . import labels as LB
from .blind import (B2_TOKENS, PLACEHOLDER_RE, filter_spans_random, find_places, is_salutation_only, normalize_span,
                    reredact, template_brackets)

RELEASE = "pilot-v2.1"
RUBRIC = "/workspace/research/labeling_rubric_v1.2.md"
RUBRIC_SHA = "083848f70a9d9665c64580faf214486b20d953256d7798093c85734b9fced342"
RUBRIC_VERSION = "v1.2"
CANDIDATES = "/workspace/research/memos/hedging_constructed_candidates.md"
DRAW_SEED = 15820
ORDER_SEEDS = {"A": 15821, "B": 15822}
N_SPAN, N_HEDGE, N_BORDER, BOILER_CAP = 100, 120, 30, 30
SIM = 0.90

LENDER_RE = re.compile(r"equifax|experian|transunion|trans union|bureau|commission|consumer financial|p\.?\s?o\.? box|our (?:office|branch|headquarters)|washington,? d\.?c|federal reserve|comptroller|\bfdic\b|\bhud\b", re.I)
DEMOG_RE = re.compile(r"\brace\b|racial|ethnic|minorit|national origin|\bage\b|retire|stage of life|single applicant|marital|familial|disabilit|public assistance|demograph|communit|underserved|low[- ]income (?:area|neighbou?rhood)|diverse", re.I)
AREA_RE = re.compile(r"neighbou?rhood|\barea\b|\bregion|\blocal\b|locality|market|surrounding|\blocation\b|where (?:the|your) (?:property|home) is", re.I)
NOTICE_RE = re.compile(r"equal credit opportunity|\becoa\b|discriminat|fair credit reporting|\bfcra\b", re.I)
HEDGE_CUE_RE = re.compile(r"unfortunately|we encourage|at this stage|may wish to|responsible", re.I)
_DEAR = re.compile(r"^\s*(?:dear\b.*|\[NAME\],?|(?:mr|ms|mrs|dr)\.?\s.*,)\s*$", re.I)

def sha(path): return hashlib.sha256(open(path, "rb").read()).hexdigest()

def rubric(path=RUBRIC, expected=RUBRIC_SHA):
    t = open(path, "rb").read(); h = hashlib.sha256(t).hexdigest()
    if h != expected: raise SystemExit(f"rubric sha256 {h} != expected {expected}")
    return t.decode(), h

def load_constructed(path=CANDIDATES):
    """[(cid, letter)] from '## Cxx' sections; drops headings, notes, quotes, comments and other annotations."""
    out = []
    for blk in re.split(r"(?m)^## ", open(path).read())[1:]:
        head, _, body = blk.partition("\n"); cid = head.strip().split()[0]
        if not re.fullmatch(r"C\d+", cid): continue
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        keep = [l for l in body.splitlines() if not re.match(r"\s*(>|#|\*\*?(feature|note|annotation)|(feature|note|annotation|key)s?\s*:|---\s*$)", l, re.I)]
        out.append((cid, "\n".join(keep).strip()))
    return out

def normalize_salutation(letter):
    """Exactly 'Dear Applicant,' as the salutation line (replaces an existing salutation or adds one)."""
    lines = (letter or "").strip("\n").splitlines()
    i = next((k for k, l in enumerate(lines) if l.strip()), None)
    if i is not None and (_DEAR.match(lines[i]) or is_salutation_only(lines[i].strip())): lines[i] = "Dear Applicant,"
    else: lines = ["Dear Applicant,", ""] + lines
    return "\n".join(lines)

def clusters(texts, thr=SIM):
    """Single-linkage clusters on TF-IDF char n-gram cosine >= thr. Returns a cluster label per text."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    if not texts: return []
    X = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=True).fit_transform(texts)
    S = (X @ X.T).toarray(); par = list(range(len(texts)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    for i, j in zip(*np.where(np.triu(S, 1) >= thr)): par[f(i)] = f(j)
    return [f(i) for i in range(len(texts))]

def m4_stratum(text):
    if template_brackets(text): return "hard:template_bracket"
    if LENDER_RE.search(text): return "hard:lender_vs_applicant_address"
    if NOTICE_RE.search(text) and not PLACEHOLDER_RE.search(text) and not AREA_RE.search(text): return "notice"   # generic ECOA-type notice (B8/A5a: 0)
    if DEMOG_RE.search(text): return "hard:area_demographic"
    if AREA_RE.search(text): return "hard:area_demographic" if PLACEHOLDER_RE.search(text) else "hard:implicit_location"
    if PLACEHOLDER_RE.search(text): return "placeholder_boilerplate"
    return "no_cue"

def _pick(df, n, rng):
    if n >= len(df): return df
    return df.iloc[sorted(rng.choice(len(df), size=n, replace=False))]

def _balanced(df, n, rng, by):
    """n rows spread as evenly as possible over the values of `by` (leftover capacity goes to larger groups)."""
    groups = {k: g for k, g in df.groupby(by)}; take = {k: 0 for k in groups}; left = n
    while left > 0 and any(take[k] < len(g) for k, g in groups.items()):
        for k in sorted(groups):
            if left and take[k] < len(groups[k]): take[k] += 1; left -= 1
    return pd.concat([_pick(groups[k], take[k], rng) for k in sorted(groups)]) if groups else df.iloc[:0]

def build_m4(pool, rng, seed=DRAW_SEED):
    sp = pool[pool.task == "proxy_mention"].sort_values("internal_id").copy()
    sp["orig_places"] = sp.text.map(find_places); sp["text"] = sp.text.map(reredact)
    recs, n_sal, n_dup = filter_spans_random(sp.to_dict("records"), key="text", seed=seed)
    u = pd.DataFrame(recs).sort_values("internal_id").reset_index(drop=True)
    u["cluster"] = clusters(u.text.tolist())
    cl = u.groupby("cluster", sort=True).apply(lambda g: g.iloc[[rng.integers(len(g))]], include_groups=False).reset_index(level=0)
    cl["stratum"] = cl.text.map(m4_stratum); cl["placeholder_free"] = ~cl.text.map(lambda t: bool(PLACEHOLDER_RE.search(t)))
    log = {"spans": int(len(sp)), "salutation_only_dropped": n_sal, "duplicates_dropped": n_dup, "unique_after_dedup": int(len(u)),
           "clusters": int(len(cl)), "available_by_stratum": cl.stratum.value_counts().sort_index().to_dict()}
    hard = cl[cl.stratum.str.startswith("hard:")]; boiler = cl[cl.stratum == "placeholder_boilerplate"]; none = cl[cl.stratum.isin(["no_cue", "notice"])]
    n_boiler = min(BOILER_CAP, len(boiler)); n_hard = min(len(hard), N_SPAN - n_boiler)
    h = _balanced(hard, n_hard, rng, "stratum"); b = _balanced(boiler, n_boiler, rng, "variant")
    z = _balanced(none, max(0, N_SPAN - len(h) - len(b)), rng, "stratum")
    out = pd.concat([h, b, z]); log["n_selected"] = int(len(out)); log["short_of_target"] = int(N_SPAN - len(out))
    return out, cl, log

def build_hedging(pool, constructed, rng):
    h = pool[pool.task == "hedging"].sort_values("internal_id").copy()
    h["orig_places"] = h.text.map(find_places); h["text"] = h.text.map(lambda t: normalize_salutation(reredact(t)))
    pos = h[h.judge == 1].assign(stratum="m1b:judge_positive")
    rest = h[~h.internal_id.isin(pos.internal_id)]; cut = float(h.text.str.len().quantile(0.9))
    cue = rest[rest.text.str.contains(HEDGE_CUE_RE) | (rest.text.str.len() >= cut)]
    bl = _pick(cue, N_BORDER, rng).assign(stratum="m1b:borderline_cue")
    rest = rest[~rest.internal_id.isin(bl.internal_id)]
    c = pd.DataFrame([dict(internal_id=f"constructed:{cid}", task="hedging", variant="constructed", call_id=None, judge=None,
                           text=normalize_salutation(reredact(t)), orig_places=find_places(t), stratum="m1b:constructed", constructed_id=cid)
                      for cid, t in constructed])
    n_rand = N_HEDGE - len(pos) - len(bl) - len(c)
    rnd = _balanced(rest, max(n_rand, 0), rng, "variant"); rnd = rnd.assign(stratum="m1b:random:" + rnd.variant)
    out = pd.concat([c, pos, bl, rnd]); pool_left = rest[~rest.internal_id.isin(rnd.internal_id)]
    return out, pool_left, {"letters": int(len(h)), "judge_positive": int(len(pos)), "cue_or_long_candidates": int(len(cue)),
                            "long_cut_chars": cut, "constructed": int(len(c))}

def replace_residual_places(items, spares, rng):
    """15.8.4 / review 4: any selected item still showing a real place name is replaced from the same stratum."""
    reps = []
    for i, r in items.iterrows():
        if not find_places(r.text): continue
        cand = spares[(spares.stratum == r.stratum) & ~spares.internal_id.isin(items.internal_id) & ~spares.text.map(lambda t: bool(find_places(t)))]
        if len(cand) == 0: reps.append({"out": r.internal_id, "in": None, "places": find_places(r.text)}); continue
        new = cand.iloc[rng.integers(len(cand))]; reps.append({"out": r.internal_id, "in": new.internal_id, "places": find_places(r.text)})
        items.loc[i] = new
    return items, reps

def reuse_flags(items, pool, run_id):
    """Exact / near (TF-IDF cosine >= 0.90) reuse of pilot-v1 items, same task. Compared on re-redacted text."""
    try: v1 = LB._read_private(run_id, "labeling_pilot-v1_key.json")
    except FileNotFoundError: return {}, {"exact": 0, "near": 0}
    v1ids = set(v1["selected_internal_ids"]); p = pool[pool.internal_id.isin(v1ids)].copy()
    p["norm"] = p.text.map(lambda t: normalize_span(normalize_salutation(reredact(t)) if "\n" in t else reredact(t)))
    from sklearn.feature_extraction.text import TfidfVectorizer
    flags = {}
    for task in ("hedging", "proxy_mention"):
        a = items[items.task == task]; b = p[p.task == task]
        if not len(a) or not len(b): continue
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit(list(a.text) + list(b.text))
        S = (vec.transform(a.text) @ vec.transform(b.text).T).toarray(); bn = set(b.norm); bid = set(b.internal_id)
        for k, r in enumerate(a.itertuples()):
            if r.internal_id in bid or normalize_span(r.text) in bn: flags[r.internal_id] = "exact"
            elif S[k].max() >= SIM: flags[r.internal_id] = "near"
    return flags, {"exact": sum(v == "exact" for v in flags.values()), "near": sum(v == "near" for v in flags.values())}

def check_items(items):
    """No real place name, no bracket token outside the B2 list except literal template brackets (B2b hard cases)."""
    bad_place = {r.internal_id: find_places(r.text) for r in items.itertuples() if find_places(r.text)}
    assert not bad_place, f"place names survive: {bad_place}"
    for r in items.itertuples():
        stray = template_brackets(r.text)
        assert not stray or (r.task == "proxy_mention" and r.stratum == "hard:template_bracket") or (r.task == "hedging" and all(re.fullmatch(r"\[[A-Za-z][A-Za-z ,'/&-]*\]", s) for s in stray)), f"{r.internal_id}: {stray}"
        assert not re.search(r"\[(TRACT|COUNTY)\]", r.text)
    hed = items[items.task == "hedging"]
    assert (hed.text.map(lambda t: t.split("\n", 1)[0]) == "Dear Applicant,").all()

def export(run_dir, outdir="/workspace/labeling/pilot-v2.1", rubric_path=RUBRIC, rubric_sha=RUBRIC_SHA, candidates=CANDIDATES,
           seed=DRAW_SEED, seeds=ORDER_SEEDS, write=True):
    run_id = os.path.basename(os.path.normpath(run_dir))
    rtext, rsha = rubric(rubric_path, rubric_sha)            # read + verified at export time
    csha = sha(candidates); constructed = load_constructed(candidates)
    rng = np.random.default_rng(seed); pool = LB.item_pool(run_dir)
    m4, m4_spare, m4log = build_m4(pool, rng, seed)
    hed, hed_spare, hlog = build_hedging(pool, constructed, rng)
    items = pd.concat([m4, hed], ignore_index=True)
    caught = sorted({p for lst in items.orig_places for p in lst})
    spares = pd.concat([m4_spare[~m4_spare.internal_id.isin(m4.internal_id)], hed_spare], ignore_index=True)
    items, reps = replace_residual_places(items, spares, rng)
    check_items(items); assert items.internal_id.is_unique
    flags, reuse = reuse_flags(items, pool, run_id)
    items["reused_v1"] = items.internal_id.map(flags)
    items = items.sort_values("internal_id").reset_index(drop=True)
    log = {"draw_seed": seed, "order_seeds": seeds, "rubric": {"path": rubric_path, "sha256": rsha, "version": RUBRIC_VERSION},
           "candidates": {"path": candidates, "sha256": csha, "n": len(constructed)}, "m4": m4log, "hedging": hlog,
           "place_names_in_selected_before_redaction": caught, "residual_place_replacements": reps, "reuse_v1": reuse,
           "counts_by_stratum": items.stratum.value_counts().sort_index().to_dict(),
           "counts_by_task_variant": {f"{t}:{v}": int(n) for (t, v), n in items.groupby(["task", "variant"]).size().items()},
           "m4_placeholder_free": int(items[(items.task == "proxy_mention")].placeholder_free.sum())}
    if not write: return items, log
    os.makedirs(outdir, exist_ok=True)
    taken = set()
    for rel in ("pilot-v1", "pilot-v2"):
        try: taken |= {c for L in LB._read_private(run_id, f"labeling_{rel}_key.json")["labelers"].values() for c in L["map"]}
        except FileNotFoundError: pass
    code_rng = random.Random(os.urandom(16)); key = {"run_id": run_id, "release": RELEASE, "created": dt.datetime.now().astimezone().isoformat(),
           "rubric_version": RUBRIC_VERSION, "rubric_sha256": rsha, "candidates_sha256": csha, "draw_seed": seed,
           "selected_internal_ids": items.internal_id.tolist(), "labelers": {}}
    for lab, s in seeds.items():
        order = list(range(len(items))); random.Random(s).shuffle(order)
        it = items.iloc[order].reset_index(drop=True).copy(); codes = []
        for _ in range(len(it)):
            c = "%08X" % code_rng.getrandbits(32)
            while c in taken: c = "%08X" % code_rng.getrandbits(32)
            taken.add(c); codes.append(c)
        it["item_id"] = codes; base = os.path.join(outdir, f"labeler_{lab}")
        LB._write_workbook(base + ".xlsx", it, s, rtext, rsha, lab, version=RUBRIC_VERSION)
        it[["item_id", "task", "text"]].assign(label="", comment="").to_csv(base + ".csv", index=False)
        key["labelers"][lab] = {"name": LB.LABELERS[lab], "order_seed": s, "file": base + ".xlsx", "map": {
            r.item_id: {"internal_id": r.internal_id, "task": r.task, "variant": r.variant, "call_id": r.call_id,
                        "judge": None if pd.isna(r.judge) else int(r.judge), "stratum": r.stratum,
                        "placeholder_free": None if r.task != "proxy_mention" else bool(r.placeholder_free),
                        "constructed": r.stratum == "m1b:constructed", "constructed_id": r.constructed_id if isinstance(r.constructed_id, str) else None,
                        "reused_v1": r.reused_v1 if isinstance(r.reused_v1, str) else None} for r in it.itertuples()}}
    kp = LB._write_private(run_id, f"labeling_{RELEASE}_key.json", key)
    sp = LB._write_private(run_id, f"labeling_{RELEASE}_selection.json", {**log, "strata": items[["internal_id", "task", "variant", "stratum"]].to_dict("records")})
    with open(os.path.join(outdir, "LABELING_METADATA.json"), "w") as f:
        json.dump({"run": RELEASE, "rubric_version": RUBRIC_VERSION, "rubric_sha256": rsha, "inputs_sha256": {"rubric": rsha, "supplement": csha},
                   "order_seeds": {f"labeler_{k}": v for k, v in seeds.items()}, "n_items": len(items),
                   "per_task": items.task.value_counts().to_dict(), "key_location": "Developer-only private store (not in this folder)"}, f, indent=1)
    LB.check_no_leak(outdir); LB.check_no_disclosure(outdir, rtext); check_exported(outdir)
    return {"outdir": outdir, "key": kp, "selection": sp, "log": log}

def check_exported(outdir):
    """Re-reads the exported CSV/XLSX items: no gazetteer place, closed-list tokens only (template brackets allowed), salutations."""
    for lab in ("A", "B"):
        d = pd.read_csv(os.path.join(outdir, f"labeler_{lab}.csv"), dtype=str, keep_default_na=False)
        for t in d.text: assert not find_places(t), t[:80]
        for t, task in zip(d.text, d.task):
            assert not re.search(r"\[(TRACT|COUNTY)\]", t)
            if task == "hedging": assert t.split("\n", 1)[0] == "Dear Applicant,"
