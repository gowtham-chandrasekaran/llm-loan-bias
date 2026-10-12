"""Batch-mode runner (pre-reg 15.3 #1): vendor batch APIs, polling, resumable state, chunk-level budget hard stop.
  python -m llmbias.batch --config configs/pilot_batch.yaml [--allow-paid]     # start
  python -m llmbias.batch --resume runs/<run_id> [--allow-paid]                # resume after interruption
  python -m llmbias.batch --config configs/pilot_batch.yaml --plan-only        # projected cost + trimming, no calls
Each request is single-turn except setup 2: round 1 may return a tool call -> tool executed locally -> round 2.
State lives in runs/<id>/batch_state.json and is rewritten atomically after every transition.
Live batch submission is UNTESTED (no paid batch was authorized); the mock backend exercises the full state machine."""
import argparse, datetime as dt, hashlib, json, os, time, uuid
import numpy as np, pandas as pd, yaml
from .budget import BudgetGuard, BudgetExceeded, load_prices, call_cost, estimate
from .build_data import P
from .clients import CallResult, MockClient
from .runner import build_record, finish_record, load_items, git_commit, _clean
from .prompts import PROMPT_VERSION
from .scoring import TOOL_SPEC, score_application

def tool_exec(args):
    r = score_application(args); return {"score": r.score, "decision": r.decision, "reason_codes": r.reason_codes}

# ----------------------------------------------------------------------------- backends
class Backend:
    """submit(items)->batch_id ; status(id)->'running'|'done'|'failed' ; results(id)->{cid: native} ;
    parse(native)->dict(text, reasoning, reasoning_kind, in, out, rt, tool_requests=[(call_id,name,args)], assistant=..., model_version)
    next_history(item, parsed, tool_outputs)->history for round 2."""
    def body(self, item): raise NotImplementedError

class MockBackend(Backend):
    STORE = {}   # process-global, mimics a server that keeps results across client restarts (resume tests)
    def __init__(self): self.store = MockBackend.STORE
    def submit(self, vendor, model, items):
        bid = "mock_" + uuid.uuid4().hex[:10]; out = {}
        for it in items:
            m = MockClient(model, it["effort"], bias=it.get("bias"))
            if it["round"] == 1 and it["tools"]:
                r = m._complete(it["system"], it["user"], TOOL_SPEC, tool_exec)
                out[it["cid"]] = {"r": r.__dict__, "tool_request": r.tool_calls[0]["args"] if r.tool_calls else None}
            elif it["round"] == 2:
                out[it["cid"]] = {"r": it["history"]["pending_final"], "tool_request": None}
            else:
                out[it["cid"]] = {"r": m._complete(it["system"], it["user"], None, None).__dict__, "tool_request": None}
        self.store[bid] = out; return bid
    def status(self, bid): return "done"
    def results(self, bid): return self.store.pop(bid)
    def parse(self, nat):
        r = nat["r"]; tr = nat["tool_request"]
        return dict(text="" if tr else r["text"], reasoning=r["reasoning"], reasoning_kind=r["reasoning_kind"], inp=r["input_tokens"] if not tr else r["input_tokens"] // 2,
                    out=r["output_tokens"] if not tr else 20, rt=r["reasoning_tokens"], model_version=r["model_version"],
                    tool_requests=[("mockcall", "score_application", tr)] if tr else [], assistant={"pending_final": r})
    def next_history(self, it, parsed, outputs): return parsed["assistant"]

class OpenAIBackend(Backend):
    def __init__(self):
        import openai; self.c = openai.OpenAI()
    def body(self, it):
        b = {"model": it["model"], "instructions": it["system"], "max_output_tokens": it["max_out"],
             "input": [{"role": "user", "content": it["user"]}] + (it["history"] or [])}
        if it["effort"]: b["reasoning"] = {"effort": it["effort"]} if it["effort"] == "none" else {"effort": it["effort"], "summary": "auto"}
        if it["tools"]: b["tools"] = [{"type": "function", "name": TOOL_SPEC["name"], "description": TOOL_SPEC["description"], "parameters": TOOL_SPEC["parameters"]}]
        return b
    def submit(self, vendor, model, items):
        lines = "\n".join(json.dumps({"custom_id": it["cid"], "method": "POST", "url": "/v1/responses", "body": self.body(it)}) for it in items)
        f = self.c.files.create(file=("batch.jsonl", lines.encode()), purpose="batch")
        return self.c.batches.create(input_file_id=f.id, endpoint="/v1/responses", completion_window="24h").id
    def status(self, bid):
        s = self.c.batches.retrieve(bid).status
        return "done" if s == "completed" else ("failed" if s in ("failed", "expired", "cancelled") else "running")
    def results(self, bid):
        b = self.c.batches.retrieve(bid); out = {}
        for fid in [b.output_file_id, b.error_file_id]:
            if not fid: continue
            for line in self.c.files.content(fid).text.splitlines():
                r = json.loads(line); out[r["custom_id"]] = r
        return out
    def parse(self, nat):
        resp = (nat.get("response") or {}); body = resp.get("body") or {}
        if resp.get("status_code") != 200: return dict(error=json.dumps(nat.get("error") or body)[:500])
        text = "".join(c.get("text", "") for o in body.get("output", []) if o.get("type") == "message" for c in o.get("content", []) if c.get("type") == "output_text")
        summ = [s.get("text", "") for o in body.get("output", []) if o.get("type") == "reasoning" for s in (o.get("summary") or [])]
        fc = [(o["call_id"], o["name"], json.loads(o["arguments"])) for o in body.get("output", []) if o.get("type") == "function_call"]
        u = body.get("usage", {})
        return dict(text=text, reasoning="\n".join(summ) or None, reasoning_kind="summary" if summ else None, inp=u.get("input_tokens", 0),
                    out=u.get("output_tokens", 0), rt=(u.get("output_tokens_details") or {}).get("reasoning_tokens", 0), model_version=body.get("model"),
                    tool_requests=fc, assistant=body.get("output", []))
    def next_history(self, it, parsed, outputs):
        return (it["history"] or []) + parsed["assistant"] + [{"type": "function_call_output", "call_id": cid, "output": json.dumps(res)} for cid, res in outputs]

class AnthropicBackend(Backend):
    def __init__(self):
        import anthropic; self.c = anthropic.Anthropic()
    def body(self, it):
        b = {"model": it["model"], "system": it["system"], "max_tokens": it["max_out"], "messages": [{"role": "user", "content": it["user"]}] + (it["history"] or [])}
        if it["effort"] == "none": b["thinking"] = {"type": "disabled"}
        elif it["effort"]:
            bud = {"low": 1024, "medium": 4096, "high": 8192}[it["effort"]]; b["thinking"] = {"type": "enabled", "budget_tokens": bud}; b["max_tokens"] += bud
        if it["tools"]: b["tools"] = [{"name": TOOL_SPEC["name"], "description": TOOL_SPEC["description"], "input_schema": TOOL_SPEC["parameters"]}]
        return b
    def submit(self, vendor, model, items):
        return self.c.messages.batches.create(requests=[{"custom_id": it["cid"], "params": self.body(it)} for it in items]).id
    def status(self, bid):
        b = self.c.messages.batches.retrieve(bid); return "done" if b.processing_status == "ended" else "running"
    def results(self, bid):
        return {r.custom_id: r.result.model_dump() for r in self.c.messages.batches.results(bid)}
    def parse(self, nat):
        if nat.get("type") != "succeeded": return dict(error=json.dumps(nat, default=str)[:500])
        m = nat["message"]; content = m["content"]
        th = [b["thinking"] for b in content if b["type"] == "thinking"]
        u = m["usage"]
        return dict(text="".join(b["text"] for b in content if b["type"] == "text"), reasoning="\n".join(th) or None, reasoning_kind="thinking" if th else None,
                    inp=u.get("input_tokens", 0), out=u.get("output_tokens", 0), rt=(u.get("output_tokens_details") or {}).get("thinking_tokens"),
                    model_version=m.get("model"), tool_requests=[(b["id"], b["name"], b["input"]) for b in content if b["type"] == "tool_use"],
                    assistant=[{k: v for k, v in b.items() if k in ("type", "text", "id", "name", "input", "thinking", "signature")} for b in content])
    def next_history(self, it, parsed, outputs):
        return (it["history"] or []) + [{"role": "assistant", "content": parsed["assistant"]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": cid, "content": json.dumps(res)} for cid, res in outputs]}]

class GoogleBackend(Backend):
    def __init__(self):
        from google import genai; self.c = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    def body(self, it):
        cfg = {"system_instruction": it["system"], "max_output_tokens": it["max_out"]}
        if it["effort"]:
            bud = {"none": 0, "low": 1024, "medium": 4096, "high": 8192}[it["effort"]]
            cfg["thinking_config"] = {"thinking_budget": bud, "include_thoughts": bud > 0}
        if it["tools"]: cfg["tools"] = [{"function_declarations": [{"name": TOOL_SPEC["name"], "description": TOOL_SPEC["description"], "parameters": TOOL_SPEC["parameters"]}]}]
        return {"contents": [{"role": "user", "parts": [{"text": it["user"]}]}] + (it["history"] or []), "config": cfg}
    def submit(self, vendor, model, items):
        job = self.c.batches.create(model=model, src=[self.body(it) for it in items], config={"display_name": f"llmbias-{uuid.uuid4().hex[:8]}"})
        self._order = getattr(self, "_order", {}); self._order[job.name] = [it["cid"] for it in items]
        return job.name
    def status(self, bid):
        s = self.c.batches.get(name=bid).state.name
        return "done" if s == "JOB_STATE_SUCCEEDED" else ("failed" if s in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED") else "running")
    def results(self, bid, order=None):
        job = self.c.batches.get(name=bid); order = order or self._order[bid]
        return {cid: (r.response.model_dump(mode="json") if r.response else {"error": str(r.error)}) for cid, r in zip(order, job.dest.inlined_responses)}
    def parse(self, nat):
        if "error" in nat and not nat.get("candidates"): return dict(error=str(nat["error"])[:500])
        parts = ((nat.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        u = nat.get("usage_metadata") or {}; rt = u.get("thoughts_token_count") or 0
        return dict(text="".join(p.get("text") or "" for p in parts if not p.get("thought")), reasoning="\n".join(p["text"] for p in parts if p.get("thought") and p.get("text")) or None,
                    reasoning_kind="thought_summary" if any(p.get("thought") for p in parts) else None, inp=u.get("prompt_token_count") or 0,
                    out=(u.get("candidates_token_count") or 0) + rt, rt=rt, model_version=nat.get("model_version"),
                    tool_requests=[(p["function_call"].get("id") or p["function_call"]["name"], p["function_call"]["name"], p["function_call"].get("args") or {}) for p in parts if p.get("function_call")],
                    assistant={"role": "model", "parts": parts})
    def next_history(self, it, parsed, outputs):
        return (it["history"] or []) + [parsed["assistant"], {"role": "user", "parts": [{"function_response": {"name": "score_application", "response": res}} for _, res in outputs]}]

BACKENDS = {"mock": MockBackend, "openai": OpenAIBackend, "anthropic": AnthropicBackend, "google": GoogleBackend}

# ----------------------------------------------------------------------------- planning (projected cost + pre-seeded trimming)
def plan_trim(cfg, prices, items):
    """If projected batch cost > cap, drop setup-2 PROFILES in a pre-seeded random order until under cap. Never cuts repeats."""
    profiles = sorted(items.profile_id.unique()); nv = len(cfg["variants"]); setups = cfg["setups"]
    order = list(np.random.default_rng(cfg.get("trim_seed", 20261012)).permutation(profiles))
    keep2 = list(profiles); dropped = []
    rows = lambda k: estimate(cfg, prices, len(profiles), nv, batch=True, n_profiles_by_setup={2: k})
    proj = lambda k: sum(r["usd"] for r in rows(k))
    def by_vendor(k):
        d = {}
        for r in rows(k): d[r["vendor"]] = d.get(r["vendor"], 0) + r["usd"]
        return d
    vcap = cfg.get("per_vendor_budget_usd", {})
    over = lambda k: proj(k) > cfg["budget_usd"] or any(v > vcap.get(ven, cfg["budget_usd"]) for ven, v in by_vendor(k).items())
    while 2 in setups and over(len(keep2)) and keep2:   # drop the same profiles for every vendor (keeps vendors comparable)
        p = order.pop(0); keep2.remove(p); dropped.append(p)
    k = len(keep2)
    return {"projected_usd": round(proj(k), 3), "projected_by_vendor": {v: round(x, 3) for v, x in by_vendor(k).items()},
            "setup2_profiles_kept": keep2, "setup2_profiles_dropped": dropped, "cap": cfg["budget_usd"], "per_vendor_cap": vcap, "under_cap": not over(k)}

# ----------------------------------------------------------------------------- engine
class BatchRun:
    def __init__(self, rd, cfg, allow_paid):
        self.rd, self.cfg = rd, cfg; self.sp = os.path.join(rd, "batch_state.json")
        paid = {m["vendor"] for m in cfg["models"]} - {"mock"}
        if paid and not allow_paid: raise SystemExit("config contains paid vendors; pass --allow-paid")
        self.prices = load_prices(P("configs", "prices.yaml"))
        self.guard = BudgetGuard(cfg["budget_usd"], cfg.get("per_vendor_budget_usd", {}), self.prices, os.path.join(rd, "spend_ledger.jsonl"))
        self.backends = {}
        self.state = json.load(open(self.sp)) if os.path.exists(self.sp) else None

    def save(self):
        tmp = self.sp + ".tmp"; json.dump(self.state, open(tmp, "w"), default=str); os.replace(tmp, self.sp)

    def be(self, vendor):
        if vendor not in self.backends: self.backends[vendor] = BACKENDS[vendor]()
        return self.backends[vendor]

    def init(self, items, meta):
        cfg = self.cfg; trim = plan_trim(cfg, self.prices, items); reqs = {}; order = 0
        for setup in cfg["setups"]:
            for m in cfg["models"]:
                for rep in range(cfg["repeats"]):
                    for _, it in items.iterrows():
                        app = _clean(it.to_dict())
                        if setup == 2 and app["profile_id"] not in trim["setup2_profiles_kept"]: continue
                        rec, sysm, user = build_record(cfg, meta["run_id"], meta["excluded_from_confirmatory"], m, setup, app, rep)
                        cid = "r" + hashlib.sha256(rec["call_id"].encode()).hexdigest()[:30]
                        rec["batch"] = True; rec["custom_id"] = cid
                        reqs[cid] = {"cid": cid, "order": order, "vendor": m["vendor"], "model": m["model"], "effort": m.get("effort"), "bias": m.get("bias"),
                                     "system": sysm, "user": user, "tools": setup == 2, "max_out": cfg["max_output_tokens"], "round": 1, "history": None,
                                     "status": "pending", "usage": [0, 0, 0], "tool_calls": [], "rec": rec}
                        order += 1
        self.state = {"meta": meta, "trim": trim, "requests": reqs, "batches": {}}; self.save()

    def _worst(self, r):
        hist = len(json.dumps(r["history"])) if r["history"] else 0
        est_in = (len(r["system"]) + len(r["user"]) + hist) // 3 + (600 if r["tools"] else 0)
        return call_cost(self.prices, r["vendor"], r["model"], est_in, r["max_out"], batch=True)

    def _inflight(self, vendor=None):
        b = [x for x in self.state["batches"].values() if x["status"] == "submitted" and (vendor is None or x["vendor"] == vendor)]
        return sum(x["worst"] for x in b)

    def submit_pending(self):
        R = self.state["requests"]; chunk = self.cfg.get("batch_chunk", 500); stopped = None
        groups = {}
        for r in sorted(R.values(), key=lambda r: r["order"]):
            if r["status"] == "pending": groups.setdefault((r["vendor"], r["model"], r["round"]), []).append(r)
        for (vendor, model, rnd), rs in groups.items():
            i = 0
            while i < len(rs):
                part, worst = [], 0.0
                for r in rs[i:i + chunk]:
                    w = self._worst(r)
                    try:
                        self.guard.check_amount(vendor, worst + w, self._inflight(), self._inflight(vendor))
                    except BudgetExceeded as e:
                        stopped = str(e); break
                    part.append(r); worst += w
                if part:
                    bid = self.be(vendor).submit(vendor, model, part)
                    self.state["batches"][bid] = {"vendor": vendor, "model": model, "round": rnd, "ids": [r["cid"] for r in part], "status": "submitted",
                                                  "worst": worst, "submitted": dt.datetime.now().astimezone().isoformat()}
                    for r in part: r["status"] = "submitted"; r["batch_id"] = bid
                    self.save(); i += len(part)
                if stopped:
                    self.state["budget_stop"] = stopped; self.save(); return stopped
        return stopped

    def collect(self):
        for bid, b in self.state["batches"].items():
            if b["status"] != "submitted": continue
            be = self.be(b["vendor"]); st = be.status(bid)
            if st == "running": continue
            res = be.results(bid, b["ids"]) if b["vendor"] == "google" else (be.results(bid) if st == "done" else {})
            for cid in b["ids"]:
                r = self.state["requests"][cid]; nat = res.get(cid)
                if nat is None:
                    r["status"] = "failed"; r["error"] = f"batch {st}: no result"; continue
                p = be.parse(nat)
                if p.get("error"):
                    r["status"] = "failed"; r["error"] = p["error"]; continue
                rt = p.get("rt"); r["usage"][0] += p["inp"]; r["usage"][1] += p["out"]
                r["usage"][2] = None if rt is None or r["usage"][2] is None else r["usage"][2] + rt
                self.guard.record(b["vendor"], b["model"], p["inp"], p["out"], cid, batch=True)
                if p["tool_requests"] and r["round"] == 1:
                    outs = [(tc_id, tool_exec(args)) for tc_id, name, args in p["tool_requests"]]
                    r["tool_calls"] += [{"name": n, "args": a, "result": o} for (_, n, a), (_, o) in zip(p["tool_requests"], outs)]
                    r["history"] = be.next_history(r, p, outs); r["round"] = 2; r["status"] = "pending"
                    r["reasoning_r1"] = p["reasoning"]
                else:
                    reasoning = "\n".join(x for x in [r.get("reasoning_r1"), p["reasoning"]] if x) or None
                    r["final"] = dict(text=p["text"], reasoning=reasoning, reasoning_kind=p["reasoning_kind"], model_version=p["model_version"]); r["status"] = "done"
            b["status"] = "settled" if st == "done" else "failed"; self.save()

    def write_calls(self):
        with open(os.path.join(self.rd, "calls.jsonl"), "w") as f:
            for r in sorted(self.state["requests"].values(), key=lambda r: r["order"]):
                rec = dict(r["rec"])
                if r["status"] == "done":
                    fr = r["final"]; u = r["usage"]
                    res = CallResult(fr["text"], fr["reasoning"], fr["reasoning_kind"], u[0], u[1], u[2], r["tool_calls"], fr["model_version"])
                    finish_record(rec, res, call_cost(self.prices, r["vendor"], r["model"], u[0], u[1], batch=True))
                else:
                    rec["error"] = r.get("error") or ("budget_stop" if self.state.get("budget_stop") else f"status={r['status']}")
                f.write(json.dumps(_clean(rec), default=str, allow_nan=False) + "\n")

    def loop(self, poll_s=60, max_wait_s=26 * 3600):
        t0 = time.time()
        while True:
            stop = self.submit_pending(); self.collect()
            R = self.state["requests"].values()
            active = [r for r in R if r["status"] in ("submitted",) or (r["status"] == "pending" and not stop)]
            if not active or time.time() - t0 > max_wait_s: break
            if any(r["status"] == "submitted" for r in R): time.sleep(poll_s)
        self.write_calls()
        m = self.state["meta"]; m.update(finished=dt.datetime.now().astimezone().isoformat(), budget_stop=self.state.get("budget_stop"),
                                         spent_usd=round(self.guard.spent, 6), spent_by_vendor=self.guard.by_vendor, trim=self.state["trim"],
                                         status_counts=pd.Series([r["status"] for r in R]).value_counts().to_dict())
        json.dump(m, open(os.path.join(self.rd, "run_meta.json"), "w"), indent=2, default=str); self.save()
        return self.rd

def start(cfg_path, allow_paid=False, out_root=None, limit=None, poll_s=None):
    cfg = yaml.safe_load(open(cfg_path)); items = load_items(cfg)
    if limit: items = items[items.profile_id.isin(items.profile_id.unique()[:limit])]
    chash = hashlib.sha256(open(cfg_path, "rb").read()).hexdigest()[:8]
    run_id = f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{chash}-{uuid.uuid4().hex[:6]}-batch"
    rd = os.path.join(out_root or P("runs"), run_id); os.makedirs(rd)
    meta = {"run_id": run_id, "config_path": cfg_path, "config": cfg, "git_commit": git_commit(), "prompt_version": PROMPT_VERSION, "mode": "batch",
            "started": dt.datetime.now().astimezone().isoformat(), "pilot": bool(cfg.get("pilot")), "excluded_from_confirmatory": bool(cfg.get("pilot")), "n_items": len(items)}
    json.dump(meta, open(os.path.join(rd, "run_meta.json"), "w"), indent=2, default=str)
    br = BatchRun(rd, cfg, allow_paid); br.init(items, meta)
    return br.loop(poll_s if poll_s is not None else cfg.get("poll_seconds", 60))

def resume(rd, allow_paid=False, poll_s=None):
    st = json.load(open(os.path.join(rd, "batch_state.json"))); br = BatchRun(rd, st["meta"]["config"], allow_paid)
    return br.loop(poll_s if poll_s is not None else st["meta"]["config"].get("poll_seconds", 60))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); ap.add_argument("--resume"); ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--plan-only", action="store_true"); ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.plan_only:
        cfg = yaml.safe_load(open(a.config)); items = load_items(cfg); pr = load_prices(P("configs", "prices.yaml"))
        t = plan_trim(cfg, pr, items); k2 = len(t["setup2_profiles_kept"])
        df = pd.DataFrame(estimate(cfg, pr, items.profile_id.nunique(), len(cfg["variants"]), batch=True, n_profiles_by_setup={2: k2}))
        print(df.to_string(index=False)); print(df.groupby("vendor").usd.sum().round(3).to_dict()); print("TOTAL", round(df.usd.sum(), 3))
        print(json.dumps({k: v for k, v in t.items() if k != "setup2_profiles_kept"}, indent=1)); raise SystemExit
    print(resume(a.resume, a.allow_paid) if a.resume else start(a.config, a.allow_paid, limit=a.limit))
