"""Judge stage: blinded hedging rubric on letters (M1b) and blinded mention judgment on detected spans (M4).
Usage: python -m llmbias.judge --run runs/<id> [--allow-paid]"""
import argparse, hashlib, json, os, random, uuid, yaml
from .blind import redact, detect_proxy_spans
from .budget import BudgetGuard, load_prices
from .build_data import P
from .clients import make_client
from .prompts import judge_hedging, judge_mention

def _pj(text):
    try: return json.loads(text)
    except Exception:
        import re; m = re.search(r"\{.*\}", text or "", re.S)
        try: return json.loads(m.group(0)) if m else {}
        except Exception: return {}

def run_judge(run_dir, allow_paid=False, setups=(1, 2, 3)):
    meta = json.load(open(os.path.join(run_dir, "run_meta.json"))); cfg = meta["config"]; j = cfg["judge"]
    if j["vendor"] != "mock" and not allow_paid: raise SystemExit("judge is a paid vendor; pass --allow-paid")
    client = make_client(j["vendor"], j["model"], j.get("effort"), max_output_tokens=300)
    guard = BudgetGuard(cfg["budget_usd"], cfg.get("per_vendor_budget_usd", {}), load_prices(P("configs", "prices.yaml")), os.path.join(run_dir, "spend_ledger.jsonl"))
    out = open(os.path.join(run_dir, "judgments.jsonl"), "w"); key = open(os.path.join(run_dir, "blind_key.jsonl"), "w")
    for line in open(os.path.join(run_dir, "calls.jsonl")):
        r = json.loads(line)
        if r.get("setup") not in setups or not r.get("parsed", {}).get("parse_ok"): continue
        letter = r["parsed"].get("letter") or ""
        texts = {"letter": letter, "rationale": r["parsed"].get("rationale") or "", "trace": r.get("reasoning") or ""}
        item_id = uuid.uuid4().hex[:12]
        key.write(json.dumps({"item_id": item_id, "call_id": r["call_id"], "variant": r["variant"]}) + "\n")
        red = redact(letter, r.get("applicant_name"), r.get("property_zip"))
        sysm, user = judge_hedging(red)
        guard.check(j["vendor"], j["model"], len(user) // 3, 300)
        res = client.complete(sysm, user); guard.record(j["vendor"], j["model"], res.input_tokens, res.output_tokens, item_id)
        rec = {"item_id": item_id, "call_id": r["call_id"], "letter_redacted": red, "hedging": _pj(res.text).get("hedging"), "spans": []}
        for src, txt in texts.items():   # M4: detection on ORIGINAL text; judgment on redacted span
            for sp in detect_proxy_spans(txt, r.get("applicant_name"), r.get("property_zip")):
                rs = redact(sp["span"], r.get("applicant_name"), r.get("property_zip"))
                sm, u = judge_mention(rs); guard.check(j["vendor"], j["model"], len(u) // 3, 300)
                rr = client.complete(sm, u); guard.record(j["vendor"], j["model"], rr.input_tokens, rr.output_tokens, item_id)
                rec["spans"].append({"source": src, "triggers": sp["triggers"], "span_redacted": rs, "mentions_proxy": _pj(rr.text).get("mentions_proxy")})
        rec["mentions_proxy_any"] = any(s["mentions_proxy"] for s in rec["spans"])
        out.write(json.dumps(rec) + "\n")
    out.close(); key.close()
    return os.path.join(run_dir, "judgments.jsonl")

def _judge_items(run_dir, setups):
    """Yield (call_rec, item_id, [(kind, key, system, user, extra)]) for every parsed call. Detection on ORIGINAL text, judging on redacted."""
    for line in open(os.path.join(run_dir, "calls.jsonl")):
        r = json.loads(line)
        if r.get("setup") not in setups or not (r.get("parsed") or {}).get("parse_ok"): continue
        letter = r["parsed"].get("letter") or ""
        texts = {"letter": letter, "rationale": r["parsed"].get("rationale") or "", "trace": r.get("reasoning") or ""}
        item_id = hashlib.sha256(r["call_id"].encode()).hexdigest()[:12]
        red = redact(letter, r.get("applicant_name"), r.get("property_zip"))
        jobs = [("hedging", "h", *judge_hedging(red), {"letter_redacted": red})]
        k = 0
        for src, txt in texts.items():
            for sp in detect_proxy_spans(txt, r.get("applicant_name"), r.get("property_zip")):
                rs = redact(sp["span"], r.get("applicant_name"), r.get("property_zip"))
                jobs.append(("span", f"s{k}", *judge_mention(rs), {"source": src, "triggers": sp["triggers"], "span_redacted": rs})); k += 1
        yield r, item_id, jobs

def run_judge_batch(run_dir, allow_paid=False, setups=(1, 2, 3), poll_s=None):
    """Pre-reg 15.3 #1: judge calls also go through the vendor batch API (same budget ledger, chunk-level hard stop). Resumable."""
    from .batch import BatchRun
    meta = json.load(open(os.path.join(run_dir, "run_meta.json"))); cfg = meta["config"]; j = cfg["judge"]
    br = BatchRun(run_dir, cfg, allow_paid, state_name="judge_batch_state.json", vendors=[j["vendor"]])
    items = list(_judge_items(run_dir, setups))
    if br.state is None:
        reqs, order = {}, 0
        for r, item_id, jobs in items:
            for kind, key, sysm, user, extra in jobs:
                cid = f"j{item_id}{key}"
                reqs[cid] = {"cid": cid, "order": order, "vendor": j["vendor"], "model": j["model"], "effort": j.get("effort"), "bias": None,
                             "system": sysm, "user": user, "tools": False, "max_out": cfg.get("judge_max_output_tokens", 300), "round": 1,
                             "history": None, "status": "pending", "usage": [0, 0, 0], "tool_calls": [], "rec": {}}
                order += 1
        br.init_requests(reqs, {"run_id": meta["run_id"], "kind": "judge", "config": cfg})
    br.loop(poll_s if poll_s is not None else cfg.get("poll_seconds", 60), write=False)
    R = br.state["requests"]; out = open(os.path.join(run_dir, "judgments.jsonl"), "w"); key = open(os.path.join(run_dir, "blind_key.jsonl"), "w")
    for r, item_id, jobs in items:
        key.write(json.dumps({"item_id": item_id, "call_id": r["call_id"], "variant": r["variant"]}) + "\n")
        rec = {"item_id": item_id, "call_id": r["call_id"], "spans": [], "judge_mode": "batch"}
        for kind, k, sysm, user, extra in jobs:
            q = R[f"j{item_id}{k}"]; ans = _pj(q["final"]["text"]) if q["status"] == "done" else {}
            if kind == "hedging": rec.update(letter_redacted=extra["letter_redacted"], hedging=ans.get("hedging"), hedging_status=q["status"])
            else: rec["spans"].append({**extra, "mentions_proxy": ans.get("mentions_proxy"), "status": q["status"]})
        rec["mentions_proxy_any"] = any(s["mentions_proxy"] for s in rec["spans"])
        out.write(json.dumps(rec) + "\n")
    out.close(); key.close()
    return os.path.join(run_dir, "judgments.jsonl"), br.state.get("budget_stop")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--run", required=True); ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--sync", action="store_true", help="debug only; the pilot of record uses batch (15.3 #1)")
    a = ap.parse_args(); print(run_judge(a.run, a.allow_paid) if a.sync else run_judge_batch(a.run, a.allow_paid))
