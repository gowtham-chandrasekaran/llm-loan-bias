"""Batch-mode runner (pre-reg 15.3 #1): vendor batch APIs, polling, resumable state, chunk-level budget hard stop.
  python -m llmbias.batch --config configs/pilot_batch.yaml [--allow-paid]     # start
  python -m llmbias.batch --resume runs/<run_id> [--allow-paid]                # resume after interruption
  python -m llmbias.batch --config configs/pilot_batch.yaml --plan-only        # projected cost + trimming, no calls
Each request is single-turn except setup 2: round 1 is forced to call score_application (15.9.1) -> tool executed locally
-> round 2 (not forced). A setup-2 tool-rule violation on any attempt (or a blank letter after the single retry) stops the run
(compliance_stop): in-flight batches are cancelled and drained, their worst case reserved in the ledgers until collected.
State lives in runs/<id>/batch_state.json and is rewritten atomically after every transition.
Live batch submission is UNTESTED (no paid batch was authorized); the mock backend exercises the full state machine."""
import argparse, datetime as dt, hashlib, json, os, time, uuid
import numpy as np, pandas as pd, yaml
from .budget import BudgetGuard, BudgetExceeded, load_prices, call_cost, estimate, call_worst, call_worst_cap_basis, token_basis, phase_projection, PHASE_CAPS
from .build_data import P
from .clients import CallResult, MockClient, ToolArgumentError, exec_tool, round2_tool_call, thought_signature_digests
from .runner import build_record, finish_record, config_guard, load_items, git_commit, _clean, retest_cap_meta
from .prompts import PROMPT_VERSION
from .scoring import TOOL_SPEC, score_application
from .output_schema import (openai_function_tool, openai_text_format, anthropic_output_config, google_schema_config, google_apply_schema,
                            GOOGLE_TOOLS_SCHEMA_MODE, validate_output, retry_reason, MAX_ATTEMPTS, openai_tool_choice, anthropic_tool_choice,
                            google_forced_tool_config, s2_compliance, tool_rule, error_counts)

def tool_exec(args):
    r = score_application(args); return {"score": r.score, "decision": r.decision, "reason_codes": r.reason_codes}

# ----------------------------------------------------------------------------- backends
class Backend:
    """submit(items)->batch_id ; status(id)->'running'|'done'|'cancelled'|'failed' ; results(id)->{cid: native} (partial for
    a cancelled batch) ; cancel(id) (Reviewer cond. 5: called on in-flight batches at a compliance stop) ;
    parse(native)->dict(text, reasoning, reasoning_kind, in, out, rt, tool_requests=[(call_id,name,args)], assistant=..., model_version)
    next_history(item, parsed, tool_outputs)->history for round 2."""
    def body(self, item): raise NotImplementedError

class MockBackend(Backend):
    STORE = {}   # process-global, mimics a server that keeps results across client restarts (resume tests)
    DELAY = {}   # model -> polls a new batch stays 'running' (tests: batches still in flight at a compliance stop)
    POLLS, CANCELLED = {}, set()
    EXPIRE = set()   # models whose batches end 'expired' with the first half of the results (vendors bill the completed part)
    def __init__(self): self.store = MockBackend.STORE
    @classmethod
    def reset(cls): cls.STORE.clear(); cls.DELAY.clear(); cls.POLLS.clear(); cls.CANCELLED.clear(); cls.EXPIRE.clear(); cls.MODEL.clear()
    MODEL = {}
    def cancel(self, bid): MockBackend.CANCELLED.add(bid)
    def submit(self, vendor, model, items):
        bid = "mock_" + uuid.uuid4().hex[:10]; out = {}; MockBackend.POLLS[bid] = MockBackend.DELAY.get(model, 0); MockBackend.MODEL[bid] = model
        for it in items:
            m = MockClient(model, it["effort"], bias=it.get("bias"), fail=it.get("fail"), emulate=it.get("emulate"), pc_compliance=it.get("pc_compliance", 0.9))
            m.attempt = it.get("attempt", 1); m.setup = it.get("setup")
            emu = it.get("emulate")   # build the real vendor request body offline and check the schema mode is present
            if emu: self.bodies = getattr(self, "bodies", []) + [(it["cid"], it["round"], it.get("attempt", 1), BACKENDS[emu].body(None, {**it, "history": None}))]
            if it["round"] == 1 and it["tools"]:
                try:
                    r = m._complete(it["system"], it["user"], TOOL_SPEC, tool_exec)
                    out[it["cid"]] = {"r": r.__dict__, "tool_request": r.tool_calls[0]["args"] if r.tool_calls else None}
                except ToolArgumentError as e:          # mock fault bad_tool_args: the vendor returns unparseable arguments
                    u = e.usage; out[it["cid"]] = {"r": {"text": "", "reasoning": None, "reasoning_kind": None, "input_tokens": 2 * u["input_tokens"],
                                                         "output_tokens": u["output_tokens"], "reasoning_tokens": 0, "model_version": u["model_version"]},
                                                   "tool_request": e.calls[-1]["args"]}
            elif it["round"] == 2:
                out[it["cid"]] = {"r": it["history"]["pending_final"], "tool_request": None}
                if m._cur().get("kind") == "round2_tool" and m._fires(it["user"]): out[it["cid"]]["round2_tool_request"] = {"credit_score": 700.0}
            else:
                out[it["cid"]] = {"r": m._complete(it["system"], it["user"], None, None).__dict__, "tool_request": None}
        self.store[bid] = out; return bid
    def status(self, bid):
        if bid in MockBackend.CANCELLED: return "cancelled"
        if MockBackend.POLLS.get(bid, 0) > 0: MockBackend.POLLS[bid] -= 1; return "running"
        return "expired" if MockBackend.MODEL.get(bid) in MockBackend.EXPIRE else "done"
    def results(self, bid):                               # re-readable, like the vendors' result files (resume after a crash in collect)
        out = self.store[bid]
        if bid in MockBackend.CANCELLED or MockBackend.MODEL.get(bid) in MockBackend.EXPIRE:
            return dict(list(out.items())[: len(out) // 2])   # cancelled / expired: the first half finished (and is billed)
        return out
    def parse(self, nat):
        r = nat["r"]; tr = nat["tool_request"]
        return dict(refusal=None if tr else r.get("refusal"), truncated=bool(r.get("truncated")) and not tr, text="" if tr else r["text"], reasoning=r["reasoning"], reasoning_kind=r["reasoning_kind"], inp=r["input_tokens"] if not tr else r["input_tokens"] // 2,
                    out=r["output_tokens"] if not tr else 20, rt=r["reasoning_tokens"], model_version=r["model_version"],
                    tool_requests=[("mockcall", "score_application", tr)] if tr else ([("mockcall2", "score_application", nat["round2_tool_request"])] if nat.get("round2_tool_request") else []),
                    assistant={"pending_final": r})
    def next_history(self, it, parsed, outputs): return parsed["assistant"]

class OpenAIBackend(Backend):
    def __init__(self):
        import openai; self.c = openai.OpenAI()
    def body(self, it):
        b = {"model": it["model"], "instructions": it["system"], "max_output_tokens": it["max_out"],
             "input": [{"role": "user", "content": it["user"]}] + (it["history"] or [])}
        if it["effort"]: b["reasoning"] = {"effort": it["effort"]} if it["effort"] == "none" else {"effort": it["effort"], "summary": "auto"}
        if it["tools"]: b["tools"] = [openai_function_tool(TOOL_SPEC)]; b["parallel_tool_calls"] = False
        if it["tools"] and it["round"] == 1: b["tool_choice"] = openai_tool_choice(TOOL_SPEC["name"])   # 15.9.1: round 1 forced, round 2 not
        if it.get("setup"): b["text"] = openai_text_format(it["setup"])
        return b
    def submit(self, vendor, model, items):
        lines = "\n".join(json.dumps({"custom_id": it["cid"], "method": "POST", "url": "/v1/responses", "body": self.body(it)}) for it in items)
        f = self.c.files.create(file=("batch.jsonl", lines.encode()), purpose="batch")
        return self.c.batches.create(input_file_id=f.id, endpoint="/v1/responses", completion_window="24h").id
    def status(self, bid):
        s = self.c.batches.retrieve(bid).status
        return "done" if s == "completed" else ({"cancelled": "cancelled", "expired": "expired", "failed": "failed"}.get(s, "running"))
    def cancel(self, bid): self.c.batches.cancel(bid)      # 'cancelling' -> 'cancelled'; finished requests are in output_file_id
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
        fc = [(o["call_id"], o["name"], o["arguments"]) for o in body.get("output", []) if o.get("type") == "function_call"]   # parsed by exec_tool
        u = body.get("usage", {})
        trunc = body.get("status") == "incomplete" and (body.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
        refusal = [c.get("refusal") for o in body.get("output", []) if o.get("type") == "message" for c in o.get("content", []) if c.get("type") == "refusal"]
        return dict(truncated=trunc, refusal=refusal[0] if refusal else None, text=text, reasoning="\n".join(summ) or None, reasoning_kind="summary" if summ else None, inp=u.get("input_tokens", 0),
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
            raise ValueError(f"Anthropic effort {it['effort']!r} not allowed (15.9 (j)); only 'none' (thinking disabled)")
        if it["tools"]: b["tools"] = [{"name": TOOL_SPEC["name"], "description": TOOL_SPEC["description"], "input_schema": TOOL_SPEC["parameters"]}]
        if it["tools"] and it["round"] == 1: b["tool_choice"] = anthropic_tool_choice(TOOL_SPEC["name"])   # 15.9.1 (thinking stays disabled)
        if it.get("setup"): b["output_config"] = anthropic_output_config(it["setup"])
        return b
    def submit(self, vendor, model, items):
        return self.c.messages.batches.create(requests=[{"custom_id": it["cid"], "params": self.body(it)} for it in items]).id
    def status(self, bid):
        b = self.c.messages.batches.retrieve(bid); return "done" if b.processing_status == "ended" else "running"
    def cancel(self, bid): self.c.messages.batches.cancel(bid)   # 'canceling' -> 'ended'; unprocessed requests come back as 'canceled'
    def results(self, bid):
        return {r.custom_id: r.result.model_dump() for r in self.c.messages.batches.results(bid)}
    def parse(self, nat):
        if nat.get("type") != "succeeded": return dict(error=json.dumps(nat, default=str)[:500])
        m = nat["message"]; content = m["content"]
        th = [b["thinking"] for b in content if b["type"] == "thinking"]
        u = m["usage"]
        return dict(truncated=m.get("stop_reason") == "max_tokens", refusal="refusal" if m.get("stop_reason") == "refusal" else None,
                    text="".join(b["text"] for b in content if b["type"] == "text"), reasoning="\n".join(th) or None, reasoning_kind="thinking" if th else None,
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
        if it["tools"] and it["round"] == 1: cfg["tool_config"] = google_forced_tool_config(TOOL_SPEC["name"])   # 15.9.1: mode ANY on round 1
        if it.get("setup") and google_apply_schema(it["round"], it["tools"], it.get("google_mode", GOOGLE_TOOLS_SCHEMA_MODE)):
            cfg.update(google_schema_config(it["setup"]))
            if it["tools"] and it["round"] == 2 and it.get("google_mode", GOOGLE_TOOLS_SCHEMA_MODE) == "final_turn":
                cfg["tool_config"] = {"function_calling_config": {"mode": "NONE"}}   # schema-only final turn after the tool result
        return {"contents": [{"role": "user", "parts": [{"text": it["user"]}]}] + (it["history"] or []), "config": cfg}
    def submit(self, vendor, model, items):
        job = self.c.batches.create(model=model, src=[self.body(it) for it in items], config={"display_name": f"llmbias-{uuid.uuid4().hex[:8]}"})
        self._order = getattr(self, "_order", {}); self._order[job.name] = [it["cid"] for it in items]
        return job.name
    def status(self, bid):
        s = self.c.batches.get(name=bid).state.name
        return "done" if s == "JOB_STATE_SUCCEEDED" else ({"JOB_STATE_CANCELLED": "cancelled", "JOB_STATE_EXPIRED": "expired", "JOB_STATE_FAILED": "failed"}.get(s, "running"))
    def cancel(self, bid): self.c.batches.cancel(name=bid)
    def results(self, bid, order=None):
        job = self.c.batches.get(name=bid); order = order or self._order[bid]
        resp = (job.dest.inlined_responses if job.dest else None) or []      # a cancelled job may have no (or partial) output
        return {cid: (r.response.model_dump(mode="json") if r.response else {"error": str(r.error)}) for cid, r in zip(order, resp)}
    def parse(self, nat):
        if "error" in nat and not nat.get("candidates"): return dict(error=str(nat["error"])[:500])
        parts = ((nat.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        u = nat.get("usage_metadata") or {}; rt = u.get("thoughts_token_count") or 0
        fr = ((nat.get("candidates") or [{}])[0].get("finish_reason"))
        safety = next((x for x in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY") if str(fr).endswith(x)), None)
        blk = (nat.get("prompt_feedback") or {}).get("block_reason")
        return dict(refusal=f"finish_reason={safety}" if safety else (f"prompt_blocked={blk}" if blk else None), truncated=str(fr).endswith("MAX_TOKENS"), text="".join(p.get("text") or "" for p in parts if not p.get("thought")), reasoning="\n".join(p["text"] for p in parts if p.get("thought") and p.get("text")) or None,
                    reasoning_kind="thought_summary" if any(p.get("thought") for p in parts) else None, inp=u.get("prompt_token_count") or 0,
                    out=(u.get("candidates_token_count") or 0) + rt, rt=rt, model_version=nat.get("model_version"),
                    usage_raw=dict(u), signature_parts=parts,
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
    # K2: when the cap basis covers every vendor, project on it (worst = p95 x1.25 etc., same as PHASE_CAPS) and trim
    # against the effective caps = min(config, phase); otherwise (mock) fall back to the legacy est_tokens estimate.
    tb = token_basis()["tokens"]; ms = cfg["models"] + ([cfg["judge"]] if cfg.get("judge") else [])
    basis = all(m["vendor"] in tb for m in ms); reps = cfg.get("repeats", 1)
    def by_vendor(k, case="worst"):
        if basis:
            n = {s: (k if s == 2 else len(profiles)) * nv * reps for s in setups}
            return phase_projection(cfg["models"], cfg.get("judge"), n, prices, case=case)
        d = {}
        for r in estimate(cfg, prices, len(profiles), nv, batch=True, n_profiles_by_setup={2: k}): d[r["vendor"]] = d.get(r["vendor"], 0) + r["usd"]
        return d
    proj = lambda k: sum(by_vendor(k).values())
    ph = PHASE_CAPS.get(cfg.get("phase")) or {}
    tcap = min(cfg["budget_usd"], ph.get("total", float("inf")))
    vcap = {v: min(cfg.get("per_vendor_budget_usd", {}).get(v, float("inf")), ph.get(v, float("inf"))) for v in set(cfg.get("per_vendor_budget_usd", {})) | (set(ph) - {"total"})}
    over = lambda k: proj(k) > tcap + 1e-9 or any(v > vcap.get(ven, tcap) + 1e-9 for ven, v in by_vendor(k).items())
    while 2 in setups and over(len(keep2)) and keep2:   # drop the same profiles for every vendor (keeps vendors comparable)
        p = order.pop(0); keep2.remove(p); dropped.append(p)
    k = len(keep2)
    return {"projected_usd": round(proj(k), 3), "projected_by_vendor": {v: round(x, 3) for v, x in by_vendor(k).items()},
            "expected_by_vendor": {v: round(x, 3) for v, x in by_vendor(k, "expected").items()} if basis else None,
            "basis": "p95x1.25 (configs/token_basis.yaml)" if basis else "legacy est_tokens",
            "vendor_balance_remaining": _balances_left(), "balance_limited": {v: round(x, 3) for v, x in by_vendor(k).items() if v in _balances_left() and x > _balances_left()[v]},
            "setup2_profiles_kept": keep2, "setup2_profiles_dropped": dropped, "cap": tcap, "per_vendor_cap": vcap, "under_cap": not over(k)}

def _balances_left():
    """Remaining prepaid balance per vendor (VENDOR_BALANCE_USD - cumulative ledger). Not a trim target: the guard stops
    that vendor cleanly at its balance (vendor-scope budget_stop) and the other vendors continue (K2/K3)."""
    from .budget import VENDOR_BALANCE_USD, cumulative_spend
    cum = cumulative_spend()
    return {v: round(b - cum.get(v, 0.0), 4) for v, b in VENDOR_BALANCE_USD.items()}

# ----------------------------------------------------------------------------- engine
ENDED_STATUS = {"done": "settled", "cancelled": "cancelled", "expired": "expired"}
RESULTS_FETCH_TRIES = 3

class BatchRun:
    def __init__(self, rd, cfg, allow_paid, state_name="batch_state.json", vendors=None):
        self.rd, self.cfg = rd, cfg; self.sp = os.path.join(rd, state_name)
        paid = set(vendors or [m["vendor"] for m in cfg["models"]]) - {"mock"}
        if paid and not allow_paid: raise SystemExit("config contains paid vendors; pass --allow-paid")
        self.prices = load_prices(P("configs", "prices.yaml"))
        self.guard = BudgetGuard(cfg["budget_usd"], cfg.get("per_vendor_budget_usd", {}), self.prices, os.path.join(rd, "spend_ledger.jsonl"), phase=cfg.get("phase"))
        self.backends = {}
        self.state = json.load(open(self.sp)) if os.path.exists(self.sp) else None
        self.jp = self.sp + ".collect_journal.jsonl"
        self._replay()

    # ---- incremental collect progress (ledger idempotency fix) --------------------------------------------------------
    # collect() appends one journal line per processed result (the request's new state); the full state file is saved
    # once per batch. After a crash partway through a batch, __init__ replays the journal onto the saved state, records
    # which results of that batch are already applied (batches[bid]["collected"]), saves atomically and only then deletes
    # the journal, so a resume neither redoes those results nor reverts later state. Ledger rows are keyed on
    # (custom_id, attempt, round, batch_id), so a result whose spend was recorded just before the crash is not billed twice.
    def _replay(self):
        if self.state is None or not os.path.exists(self.jp): return
        n = 0
        for line in open(self.jp):
            try: e = json.loads(line)
            except ValueError: break                          # torn last line from the crash: that result is simply redone
            self.state["requests"][e["cid"]] = e["req"]
            b = self.state["batches"][e["bid"]]; b.setdefault("collected", [])
            if e["cid"] not in b["collected"]: b["collected"].append(e["cid"])
            if e.get("compliance_stop") and not self.state.get("compliance_stop"): self.state["compliance_stop"] = e["compliance_stop"]
            n += 1
        self.state.setdefault("journal_replays", []).append({"at": dt.datetime.now().astimezone().isoformat(), "entries": n})
        self.save(); os.remove(self.jp)

    def _journal(self, bid, cid, r):
        with open(self.jp, "a") as f:
            f.write(json.dumps({"bid": bid, "cid": cid, "req": r, "compliance_stop": self.state.get("compliance_stop")}, default=str) + "\n")

    def save(self):
        tmp = self.sp + ".tmp"; json.dump(self.state, open(tmp, "w"), default=str); os.replace(tmp, self.sp)

    def be(self, vendor):
        if vendor not in self.backends: self.backends[vendor] = BACKENDS[vendor]()
        return self.backends[vendor]

    def init(self, items, meta):
        cfg = self.cfg; trim = plan_trim(cfg, self.prices, items); reqs = {}; order = 0
        if cfg.get("require_all_profiles") and trim["setup2_profiles_dropped"]:   # 15.9.1 re-test: the replacement needs the full grid
            raise SystemExit(f"REFUSING: require_all_profiles but the plan would drop setup-2 profiles {trim['setup2_profiles_dropped']} "
                             f"(projected {trim['projected_by_vendor']} vs caps {trim['per_vendor_cap']})")
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
                                     "status": "pending", "usage": [0, 0, 0], "tool_calls": [], "rec": rec,
                                     "setup": setup, "attempt": 1, "fail": m.get("fail"), "emulate": m.get("emulate"),
                                     "google_mode": cfg.get("google_tools_schema_mode", GOOGLE_TOOLS_SCHEMA_MODE)}
                        order += 1
        self.state = {"meta": meta, "trim": trim, "requests": reqs, "batches": {}}; self.save()

    def _worst(self, r):
        """K2: reserve on the cap basis (p95 x1.25, Google x2; setup 2 = both rounds, max(pilot basis, S2_CORE_P95)). Round 2 reserves what is left of
        the per-call basis after round 1's actual cost, floored at 25% of it. No basis (mock/judge) -> old char estimate."""
        hist = len(json.dumps(r["history"])) if r["history"] else 0
        est_in = (len(r["system"]) + len(r["user"]) + hist) // 3 + (600 if r["tools"] else 0)
        w = call_worst_cap_basis(self.prices, r["vendor"], r["model"], r.get("setup"), est_in, r["max_out"], batch=True)   # fix 6: cap basis
        if r.get("round", 1) == 2 and (token_basis()["tokens"].get(r["vendor"]) or {}).get(r.get("setup")):
            w = max(w - r.get("r1_cost", 0.0), 0.25 * w)
        return w

    def _inflight(self, vendor=None):
        b = [x for x in self.state["batches"].values() if x["status"] == "submitted" and (vendor is None or x["vendor"] == vendor)]
        return sum(x["worst"] for x in b)

    def submit_pending(self):
        """K2: a cap hit PAUSES (vendor scope: that vendor only; total scope: everything) and leaves requests pending.
        They are resubmitted on later passes as in-flight reservations settle at actual cost. Returns the pause map;
        loop() makes it a final budget_stop only once nothing is in flight."""
        R = self.state["requests"]; chunk = self.cfg.get("batch_chunk", 500); paused = {}
        if self.state.get("compliance_stop"): return paused          # 15.9.1: nothing new is submitted after a compliance stop
        groups = {}
        for r in sorted(R.values(), key=lambda r: r["order"]):
            if r["status"] == "pending": groups.setdefault((r["vendor"], r["model"], r["round"], r.get("attempt", 1)), []).append(r)
        for (vendor, model, rnd, att), rs in groups.items():
            if "*" in paused or vendor in paused: continue
            i = 0; stopped = None
            while i < len(rs):
                part, worst = [], 0.0
                for r in rs[i:i + chunk]:
                    w = self._worst(r)
                    try:
                        self.guard.check_amount(vendor, worst + w, self._inflight(), self._inflight(vendor))
                    except BudgetExceeded as e:
                        stopped = str(e); paused["*" if e.scope == "total" else vendor] = stopped; break
                    part.append(r); worst += w
                if part:
                    bid = self.be(vendor).submit(vendor, model, part)
                    self.state["batches"][bid] = {"vendor": vendor, "model": model, "round": rnd, "attempt": att, "followup_retry": att > 1, "ids": [r["cid"] for r in part], "status": "submitted",
                                                  "worst": worst, "submitted": dt.datetime.now().astimezone().isoformat()}
                    for r in part: r["status"] = "submitted"; r["batch_id"] = bid
                    self.save(); i += len(part)
                if stopped: break
        self.state["paused"] = paused; self.save()
        return paused

    def collect(self):
        for bid, b in self.state["batches"].items():
            if b["status"] == "unfetched": self._refetch(bid, b); continue
            if b["status"] != "submitted": continue
            be = self.be(b["vendor"]); st = be.status(bid)
            if st == "running": continue
            # s2-retest fix 4: results are fetched for every ended batch (done / cancelled / expired / failed): vendors bill
            # the completed part of an expired or cancelled batch. A failed fetch is retried on later polls; after
            # RESULTS_FETCH_TRIES on a non-done batch it settles without results and KEEPS any reserve (never undercounts).
            try:
                res = be.results(bid, b["ids"]) if b["vendor"] == "google" else be.results(bid)
                b.pop("results_error", None)
            except Exception as e:
                b["results_error"] = f"{type(e).__name__}: {str(e)[:300]}"; b["results_fetch_tries"] = b.get("results_fetch_tries", 0) + 1
                if st == "done" or b["results_fetch_tries"] < RESULTS_FETCH_TRIES: self.save(); continue
                res = None
            done = set(b.get("collected") or [])
            for cid in b["ids"]:
                if cid in done: continue                          # applied before a crash (journal replay)
                self._collect_one(be, bid, b, st, cid, (res or {}).get(cid))
                self._journal(bid, cid, self.state["requests"][cid])
            if res is not None:                                   # actuals booked above -> release (only where held)
                rel = self.guard.release_all(b["vendor"], b["model"], bid)
                if rel: b["released"] = rel
                b["status"] = ENDED_STATUS.get(st, "failed")
            else:
                # s2-retest condition 2: the batch ended (expired / failed / cancelled) and its results could not be fetched
                # RESULTS_FETCH_TRIES times, so whatever the vendor billed for its completed part is not in the ledgers. If no
                # stop reserve already covers it, book a keyed worst-case placeholder (b["worst"], the amount checked against
                # the caps at submit) in both ledgers, so the ledgers can only overcount. The batch becomes 'unfetched': later
                # passes and resumes keep trying the fetch, and only a fetch that bills the actuals releases the hold(s).
                for cid in b["ids"]:
                    rq = self.state["requests"][cid]
                    if rq["status"] == "failed" and (rq.get("error") or "").endswith("no result"):
                        rq["unfetched"] = bid; rq["error"] = f"{rq.get('error')} (results unfetched after {RESULTS_FETCH_TRIES} tries; worst-case hold booked)"
                if b.get("reserved") is not None or any(self.guard.has_reserve(bid).values()): b["reserve_kept"] = "results unavailable"
                else: b["placeholder"] = self.guard.placeholder(b["vendor"], b["model"], b["worst"], bid)
                b["ended_status"] = st; b["status"] = "unfetched"
            b.pop("collected", None); self.save()
            if os.path.exists(self.jp): os.remove(self.jp)

    def _refetch(self, bid, b):
        """Retry the results fetch of an 'unfetched' batch. On success, bill the requests that had no result, then release
        its placeholder and/or stop reserve (keyed, per ledger, only where held). On failure: keep the hold(s), try later."""
        be = self.be(b["vendor"]); st = b["ended_status"]
        try:
            res = be.results(bid, b["ids"]) if b["vendor"] == "google" else be.results(bid)
        except Exception as e:
            b["results_error"] = f"{type(e).__name__}: {str(e)[:300]}"; b["results_fetch_tries"] = b.get("results_fetch_tries", 0) + 1
            self.save(); return
        b.pop("results_error", None); done = set(b.get("collected") or [])
        for cid in b["ids"]:
            r = self.state["requests"][cid]
            if cid in done or r.get("unfetched") != bid: continue
            r.pop("unfetched"); r.pop("error", None); r["status"] = "submitted"
            self._collect_one(be, bid, b, st, cid, (res or {}).get(cid))
            self._journal(bid, cid, r)
        rel = self.guard.release_all(b["vendor"], b["model"], bid)
        if rel: b["released"] = rel
        b["refetched"] = dt.datetime.now().astimezone().isoformat(); b.pop("reserve_kept", None)
        b["status"] = ENDED_STATUS.get(st, "failed"); b.pop("collected", None); self.save()
        if os.path.exists(self.jp): os.remove(self.jp)

    def _collect_one(self, be, bid, b, st, cid, nat):
        r = self.state["requests"][cid]
        # requests a compliance-stop cancel kept from running are not API errors: tagged compliance_stop (cond. b counts)
        # (fix 5: only for a cancel that was accepted and actually stopped this request: batch 'cancelled', or Anthropic 'canceled')
        cx = "compliance_stop: cancelled batch: " if b.get("cancel_requested") and (st == "cancelled" or (isinstance(nat, dict) and nat.get("type") == "canceled")) else ""
        if nat is None:
            r["status"] = "failed"; r["error"] = f"{cx}batch {st}: no result"; return
        self._raw(cid, r.get("attempt", 1), r["round"], bid, nat)
        p = be.parse(nat)
        if p.get("error"):
            r["status"] = "failed"; r["error"] = cx + p["error"]; return
        rt = p.get("rt"); r["usage"][0] += p["inp"]; r["usage"][1] += p["out"]
        r["usage"][2] = None if rt is None or r["usage"][2] is None else r["usage"][2] + rt
        r.setdefault("reasoning_tokens_by_round", []).append(rt)            # per round; usage[2] is the sum (None if any None)
        if "usage_raw" in p:                                                # Gemini: full usage_metadata + signature digests (never raw)
            r.setdefault("usage_metadata", []).append(p["usage_raw"])
            r.setdefault("thought_signatures", []).extend(thought_signature_digests(p.get("signature_parts"), r["round"]))
        self.guard.record(b["vendor"], b["model"], p["inp"], p["out"], cid, batch=True,
                          key={"attempt": r.get("attempt", 1), "round": r["round"], "batch_id": bid})   # idempotent
        bad = False
        if p["tool_requests"] and r["round"] == 1:
            outs = []
            for tc_id, name, raw in p["tool_requests"]:
                try:
                    args, res = exec_tool(tool_exec, name, raw); outs.append((tc_id, res)); r["tool_calls"].append({"name": name, "args": args, "result": res})
                except ToolArgumentError as e:          # Reviewer cond. (b): bad forced-call arguments -> tool_args_invalid stop
                    r["tool_calls"] += e.calls; bad = True
            if not bad:
                r["history"] = be.next_history(r, p, outs); r["round"] = 2; r["status"] = "pending"
                r["r1_cost"] = call_cost(self.prices, r["vendor"], r["model"], p["inp"], p["out"], batch=True)
                r["reasoning_r1"] = p["reasoning"]
                return
        if r["round"] == 2 and p["tool_requests"]:      # Reviewer cond. (a): round-2 tool requests count toward multiple_tool_calls
            r["tool_calls"] += [round2_tool_call(n, a) for _, n, a in p["tool_requests"]]
        reasoning = "\n".join(x for x in [r.get("reasoning_r1"), p["reasoning"]] if x) or None
        r["final"] = dict(text=p["text"], reasoning=reasoning, reasoning_kind=p["reasoning_kind"], model_version=p["model_version"],
                          truncated=bool(p.get("truncated")), refusal=p.get("refusal")); r["status"] = "done"
        self._maybe_retry(r)

    def _raw(self, cid, attempt, rnd, bid, nat):
        d = os.path.join(self.rd, "raw"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "batch_responses.jsonl"), "a") as f:
            f.write(json.dumps({"cid": cid, "attempt": attempt, "round": rnd, "batch_id": bid, "native": nat}, default=str) + "\n")

    def _maybe_retry(self, r):
        """15.9 single retry: requeue once (attempt 2) as a follow-up batch; budget is checked in submit_pending before it is sent."""
        if not r.get("setup"): return                         # judge / non-decision batches: no decision schema, no retry
        rec = r["rec"]
        v = validate_output(r["final"]["text"], r["setup"], rules=rec.get("validation_rules"), tool_decision=rec.get("tool_decision") if r["setup"] == 3 else None)
        if r["setup"] == 2:   # Reviewer cond. 1: tool rule on EVERY attempt; a violation stops the run with no retry
            tr = tool_rule(r["tool_calls"])
            if r.get("attempt", 1) == 1: r["first_attempt_forced_call_ok"] = tr is None
            if tr: self._compliance_stop(r, tr); return
        reason = retry_reason(r["final"]["truncated"], v, r["final"].get("refusal"))
        if reason is None or r.get("attempt", 1) >= MAX_ATTEMPTS:
            nc = s2_compliance({"setup": r["setup"], "tool_calls": r["tool_calls"], "parsed": v, "response": r["final"]["text"]})   # final, after the retry
            if nc: self._compliance_stop(r, nc)
            return
        assert "first_attempt" not in r, "call already retried"
        r["first_attempt"] = {"retry_reason": reason, "original_error": {"max_tokens": "max_tokens", "refusal": f"refusal: {r['final'].get('refusal')}"}.get(reason) or "; ".join(v["validation_errors"]),
                              "response": r["final"]["text"], "usage": list(r["usage"]), "tool_calls": r["tool_calls"],
                              "reasoning_tokens_by_round": r.get("reasoning_tokens_by_round")}
        r.update(attempt=2, round=1, history=None, tool_calls=[], status="pending", final=None, reasoning_r1=None, reasoning_tokens_by_round=[])
        r.pop("usage_metadata", None); r.pop("thought_signatures", None)

    def _compliance_stop(self, r, reason):
        if not self.state.get("compliance_stop"):
            self.state["compliance_stop"] = {"reason": reason, "cid": r["cid"], "vendor": r["vendor"], "model": r["model"], "attempt": r.get("attempt", 1)}

    def _reserve_amount(self, b):
        """In-flight worst case of a batch at the stop: its submit-time worst, or its requests re-priced on the cap basis
        (fix 6), whichever is larger (batches submitted by older code carry a pilot-basis worst)."""
        R = self.state["requests"]
        return max(b["worst"], sum(self._worst(R[c]) for c in b["ids"] if R[c]["status"] == "submitted"))

    def stop_and_drain(self, poll_s, max_wait_s):
        """Reviewer cond. (5) on 91baba3, s2-retest fixes 3 and 5. At a compliance stop:
        1. plan: the in-flight batches, their reserve amounts and the drain deadline (stop time + drain_max_wait_s) are
           persisted (compliance_stop.inflight_at_stop / stop_ts / drain_deadline_ts) before any ledger row is written;
        2. reserve: per planned batch still in flight, the keyed reserve row goes to the run AND cumulative ledgers, and only
           then is b["reserved"] persisted. A batch that settled before its reserve was booked gets none (and collect only
           releases where a ledger holds the reserve), so every crash point nets to the actual spend;
        3. cancel: in-flight batches are cancelled; a cancel that raises keeps cancel_error, is retried on resume, and does
           not tag the batch's requests 'compliance_stop: cancelled batch';
        4. drain: collect until nothing is in flight or the persisted deadline passes. collect books actuals then releases.
        Resumable: the plan, deadline, reserves and cancels are all idempotent."""
        cs = self.state["compliance_stop"]; B = self.state["batches"]
        if "inflight_at_stop" not in cs:
            plan = {bid: round(self._reserve_amount(b), 8) for bid, b in B.items() if b["status"] == "submitted"}
            by = {}
            for bid, amt in plan.items(): by[B[bid]["vendor"]] = round(by.get(B[bid]["vendor"], 0.0) + amt, 8)
            now = time.time(); wait = float(self.cfg.get("drain_max_wait_s", max_wait_s))
            cs.update(inflight_at_stop={"worst_usd": round(sum(plan.values()), 8), "by_vendor": by, "batches": plan, "n_batches": len(plan),
                                        "basis": "max(pilot basis, S2_CORE_P95) for setup 2 (call_worst_cap_basis)"},
                      stop_ts=now, drain_max_wait_s=wait, drain_deadline_ts=now + wait)
            self.save()
        for bid, amt in cs["inflight_at_stop"]["batches"].items():
            b = B[bid]
            if b.get("reserved") is not None: continue
            if b["status"] != "submitted": b["reserve_skipped"] = "settled before its reserve was booked"; continue
            self.guard.reserve(b["vendor"], b["model"], amt, bid)          # both ledgers, keyed (idempotent)
            b["reserved"] = amt; self.save()                                # flag only after both rows exist
        for bid, b in B.items():
            if b["status"] == "submitted" and not b.get("cancel_requested"):
                try:
                    self.be(b["vendor"]).cancel(bid); b["cancel_requested"] = dt.datetime.now().astimezone().isoformat(); b.pop("cancel_error", None)
                except Exception as e:
                    b["cancel_error"] = f"{type(e).__name__}: {str(e)[:300]}"; b["cancel_attempts"] = b.get("cancel_attempts", 0) + 1
        self.save()
        while any(b["status"] == "submitted" for b in B.values()):
            self.collect()
            if not any(b["status"] == "submitted" for b in B.values()) or time.time() >= cs["drain_deadline_ts"]: break
            time.sleep(poll_s)
        left = [bid for bid, b in B.items() if b["status"] == "submitted"]
        kept = [bid for bid, b in B.items() if b.get("reserve_kept")]
        cs["drain"] = {"complete": not left, "still_in_flight": left, "timed_out": bool(left) and time.time() >= cs["drain_deadline_ts"],
                       "reserve_outstanding_usd": round(sum(B[x].get("reserved") or 0.0 for x in left + kept), 8),
                       "reserve_kept_no_results": kept, "finished": dt.datetime.now().astimezone().isoformat()}
        self.save()

    def write_calls(self):
        with open(os.path.join(self.rd, "calls.jsonl"), "w") as f:
            for r in sorted(self.state["requests"].values(), key=lambda r: r["order"]):
                rec = dict(r["rec"])
                if r["status"] == "done":
                    fr = r["final"]; u = r["usage"]
                    res = CallResult(fr["text"], fr["reasoning"], fr["reasoning_kind"], u[0], u[1], u[2], r["tool_calls"], fr["model_version"])
                    res.truncated = fr.get("truncated", False); res.refusal = fr.get("refusal")
                    finish_record(rec, res, call_cost(self.prices, r["vendor"], r["model"], u[0], u[1], batch=True))
                    rec["attempt"] = r.get("attempt", 1)
                    for k in ("reasoning_tokens_by_round", "usage_metadata", "thought_signatures"):
                        if r.get(k) is not None: rec[k] = r[k]
                    if r.get("setup") == 2:
                        rec["s2_compliance"] = s2_compliance(rec)
                        if "first_attempt_forced_call_ok" in r: rec["first_attempt_forced_call_ok"] = r["first_attempt_forced_call_ok"]
                    fa = r.get("first_attempt")
                    if fa:
                        u1 = fa["usage"]; rec.update(retry_reason=fa["retry_reason"], original_error=fa["original_error"], attempt1_response=fa["response"],
                                                    attempt1_tool_calls=fa["tool_calls"], attempt1_cost_usd=call_cost(self.prices, r["vendor"], r["model"], u1[0], u1[1], batch=True))
                else:
                    stops = self.state.get("vendor_budget_stops") or {}
                    why = stops.get(r["vendor"]) or stops.get("*")
                    cst = self.state.get("compliance_stop")
                    rec["error"] = r.get("error") or (f"compliance_stop: {cst['reason']} ({cst['cid']})" if cst and r["status"] in ("pending", "submitted") else None) or (f"budget_stop: {why}" if (r["status"] == "pending" and why) else ("budget_stop" if self.state.get("budget_stop") and r["status"] == "pending" else f"status={r['status']}"))
                    rec["attempt"] = r.get("attempt", 1)
                    if r.get("first_attempt"): rec.update(retry_reason=r["first_attempt"]["retry_reason"], original_error=r["first_attempt"]["original_error"],
                                                          attempt1_response=r["first_attempt"]["response"])
                f.write(json.dumps(_clean(rec), default=str, allow_nan=False) + "\n")

    def init_requests(self, reqs, meta):
        self.state = {"meta": meta, "trim": None, "requests": reqs, "batches": {}}; self.save()

    def loop(self, poll_s=60, max_wait_s=26 * 3600, write=True):
        t0 = time.time()
        while True:
            paused = self.submit_pending(); self.collect()
            R = self.state["requests"].values()
            if self.state.get("compliance_stop"):                       # 15.9.1 item 3: stop the run
                self.stop_and_drain(poll_s, max_wait_s); break
            inflight = any(r["status"] == "submitted" for r in R)
            pending = [r for r in R if r["status"] == "pending"]
            # K2: pending stays eligible while anything is in flight (settling frees reservation -> resubmit next pass);
            # if a pass freed nothing and nothing is in flight, retry submit once immediately (round-2/retry requeues).
            if pending and not inflight:
                paused = self.submit_pending(); R = self.state["requests"].values()
                inflight = any(r["status"] == "submitted" for r in R); pending = [r for r in R if r["status"] == "pending"]
            if (not pending and not inflight) or (pending and not inflight and paused) or time.time() - t0 > max_wait_s: break
            if any(r["status"] == "submitted" for r in R): time.sleep(poll_s)
        stops = self.state.get("paused") or {}
        if any(r["status"] == "pending" for r in R) and stops:     # final: nothing in flight, caps still block -> budget_stop
            self.state["budget_stop"] = "; ".join(f"{k}: {v}" for k, v in stops.items()); self.state["vendor_budget_stops"] = stops
        if not write:
            self.save(); return self.rd
        self.write_calls()
        cs = self.state.get("compliance_stop") or {}
        m = self.state["meta"]; m.update(finished=dt.datetime.now().astimezone().isoformat(), budget_stop=self.state.get("budget_stop"), compliance_stop=self.state.get("compliance_stop"),
                                         inflight_worst_at_stop_usd=(cs.get("inflight_at_stop") or {}).get("worst_usd"),
                                         error_counts=error_counts([json.loads(l) for l in open(os.path.join(self.rd, "calls.jsonl"))]),
                                         spent_usd=round(self.guard.spent, 6), spent_by_vendor=self.guard.by_vendor, trim=self.state["trim"],
                                         status_counts=pd.Series([r["status"] for r in R]).value_counts().to_dict(),
                                         unfetched_batches={k: {"vendor": b["vendor"], "ended_status": b.get("ended_status"), "placeholder_usd": b.get("placeholder"),
                                                                "reserve_kept": b.get("reserve_kept"), "results_error": b.get("results_error")}
                                                            for k, b in self.state["batches"].items() if b["status"] == "unfetched"})
        json.dump(m, open(os.path.join(self.rd, "run_meta.json"), "w"), indent=2, default=str); self.save()
        return self.rd

def start(cfg_path, allow_paid=False, out_root=None, limit=None, poll_s=None, phase=None):
    cfg = yaml.safe_load(open(cfg_path))
    if phase: cfg["phase"] = phase                         # CLI --phase overrides the config
    config_guard(cfg, allow_paid); items = load_items(cfg)
    if limit: items = items[items.profile_id.isin(items.profile_id.unique()[:limit])]
    chash = hashlib.sha256(open(cfg_path, "rb").read()).hexdigest()[:8]
    run_id = f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{chash}-{uuid.uuid4().hex[:6]}-batch"
    rd = os.path.join(out_root or P("runs"), run_id); os.makedirs(rd)
    meta = {"run_id": run_id, "config_path": cfg_path, "config": cfg, "git_commit": git_commit(), "prompt_version": PROMPT_VERSION, "phase": cfg.get("phase"), "mode": "batch",
            "started": dt.datetime.now().astimezone().isoformat(), "pilot": bool(cfg.get("pilot")), "excluded_from_confirmatory": bool(cfg.get("pilot")), "n_items": len(items)}
    if cfg.get("probe"): meta.update(probe=cfg["probe"], excluded_from_analysis=True)
    if cfg.get("phase") == "minipilot_s2_retest": meta["cap_basis"] = retest_cap_meta(cfg)   # Reviewer cond. (2)
    json.dump(meta, open(os.path.join(rd, "run_meta.json"), "w"), indent=2, default=str)
    br = BatchRun(rd, cfg, allow_paid); br.init(items, meta)
    return br.loop(poll_s if poll_s is not None else cfg.get("poll_seconds", 60))

def resume(rd, allow_paid=False, poll_s=None):
    st = json.load(open(os.path.join(rd, "batch_state.json"))); config_guard(st["meta"]["config"], allow_paid); br = BatchRun(rd, st["meta"]["config"], allow_paid)
    return br.loop(poll_s if poll_s is not None else st["meta"]["config"].get("poll_seconds", 60))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); ap.add_argument("--resume"); ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--plan-only", action="store_true"); ap.add_argument("--limit", type=int)
    ap.add_argument("--phase", help="budget phase (smoke|minipilot|main); overrides the config's phase")
    a = ap.parse_args()
    if a.plan_only:
        cfg = yaml.safe_load(open(a.config)); items = load_items(cfg); pr = load_prices(P("configs", "prices.yaml"))
        t = plan_trim(cfg, pr, items); k2 = len(t["setup2_profiles_kept"])
        df = pd.DataFrame(estimate(cfg, pr, items.profile_id.nunique(), len(cfg["variants"]), batch=True, n_profiles_by_setup={2: k2}))
        print(df.to_string(index=False)); print(df.groupby("vendor").usd.sum().round(3).to_dict()); print("TOTAL", round(df.usd.sum(), 3))
        print(json.dumps({k: v for k, v in t.items() if k != "setup2_profiles_kept"}, indent=1)); raise SystemExit
    print(resume(a.resume, a.allow_paid) if a.resume else start(a.config, a.allow_paid, limit=a.limit, phase=a.phase))
