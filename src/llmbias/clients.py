"""Provider-agnostic client layer. Each client returns a CallResult. Real adapters were only connectivity-checked
(one tiny call each on 2026-10-12); their tool-calling / reasoning-capture paths are UNTESTED against live APIs."""
import hashlib, json, os, random, re, time
from dataclasses import dataclass, field

@dataclass
class CallResult:
    text: str
    reasoning: str | None = None          # vendor-exposed trace / summary, if any
    reasoning_kind: str | None = None     # 'summary' (OpenAI), 'thinking' (Anthropic), 'thought_summary' (Google), None
    input_tokens: int = 0
    output_tokens: int = 0                # includes reasoning tokens where vendor bills them as output
    reasoning_tokens: int = 0
    tool_calls: list = field(default_factory=list)  # [{"name":..., "args":..., "result":...}]
    model_version: str | None = None
    raw: dict | None = None

class TransientError(Exception):
    pass

def with_retries(fn, max_tries=5, base=1.5, sleep=time.sleep):
    for i in range(max_tries):
        try:
            return fn()
        except TransientError:
            if i == max_tries - 1:
                raise
            sleep(base ** i + random.random() * 0.1)

def _is_transient(e):
    s = type(e).__name__.lower() + str(e).lower()
    return any(k in s for k in ["ratelimit", "rate limit", "timeout", "overloaded", "529", "503", "500", "connection", "unavailable"])

class BaseClient:
    vendor = "base"
    def __init__(self, model, effort=None, max_output_tokens=1500):
        self.model, self.effort, self.max_output_tokens = model, effort, max_output_tokens
    def complete(self, system, user, tool_spec=None, tool_fn=None) -> CallResult:
        def go():
            try:
                return self._complete(system, user, tool_spec, tool_fn)
            except Exception as e:
                if _is_transient(e):
                    raise TransientError(str(e)) from e
                raise
        return with_retries(go)

# ---------------------------------------------------------------- mock
class MockClient(BaseClient):
    """Deterministic fake model for dry runs. Optional injected bias lets tests check the analysis can detect effects.
    bias: dict proxy-> probability of flipping approve->deny when that proxy slot is the 'treated' one."""
    vendor = "mock"
    def __init__(self, model="mock-1", effort=None, max_output_tokens=1500, bias=None, call_tool_p=0.8, noise=0.05):
        super().__init__(model, effort, max_output_tokens)
        self.bias, self.call_tool_p, self.noise = bias or {}, call_tool_p, noise

    def _rng(self, *parts):
        return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))

    def _wrap(self, text, system, user):
        return CallResult(text=text, input_tokens=(len(system) + len(user)) // 4, output_tokens=len(text) // 4, model_version=self.model)

    def _complete(self, system, user, tool_spec, tool_fn):
        r = self._rng(self.model, self.effort, user)
        if "Rubric: does this letter" in user:
            body = user.split("LETTER:", 1)[1]
            return self._wrap(json.dumps({"hedging": "financial habits" in body, "evidence": ""}), system, user)
        if "Does the span use" in user:
            body = user.split("SPAN:", 1)[1]
            return self._wrap(json.dumps({"mentions_proxy": bool(re.search(r"neighbou?rhood|\[NAME\]|\[ZIP\]", body))}), system, user)
        cs = int(re.search(r"Credit score \(FICO\): (\d+)", user).group(1))
        dti = float(re.search(r"Debt-to-income ratio: ([\d.]+)%", user).group(1))
        latent = (cs - 680) / 40 - (dti - 44) / 4 + r.gauss(0, 1) * self.noise * 10
        fixed = re.search(r"has (DENIED|APPROVED) this application", user)
        tool_calls = []
        if tool_spec and r.random() < self.call_tool_p:
            args = {k: float(re.search(pat, user).group(1)) for k, pat in [
                ("credit_score", r"Credit score \(FICO\): (\d+)"), ("dti_pct", r"Debt-to-income ratio: ([\d.]+)%"),
                ("ltv_pct", r"Combined loan-to-value: ([\d.]+)%"), ("reserves_months", r"Liquid reserves \(months of payments\): ([\d.]+)"),
                ("employment_years", r"Years with current employer: (\d+)")]}
            res = tool_fn(args); tool_calls.append({"name": tool_spec["name"], "args": args, "result": res})
            dec = res["decision"]
        else:
            dec = "approve" if latent > 0 else "deny"
        treated = self.bias.get("name") if re.search(r"Applicant: (Lakisha|Tamika|Latasha|Latoya|Latonya|Tanisha|Ebony|Jermaine|Darnell|Roosevelt)", user) else None
        if treated and dec == "approve" and r.random() < treated:
            dec = "deny"
        if fixed:
            dec = "deny" if fixed.group(1) == "DENIED" else "approve"
        codes = ["C09", "X02"] if dec == "deny" else []
        hedge = " You may wish to consider a co-signer and work on your financial habits." if (treated and r.random() < 0.5) else ""
        letter = (f"Dear Applicant, thank you for your application. We are unable to approve it at this time because of "
                  f"excessive obligations in relation to income and credit history.{hedge}" if dec == "deny"
                  else "Dear Applicant, we are pleased to approve your mortgage application.")
        if True:
            out = {"decision": dec, "letter": letter, "reason_codes": codes,
                   "reasons_text": ["Excessive obligations in relation to income", "Delinquent credit obligations"][:len(codes)],
                   "rationale": f"DTI {dti} and score {cs}."}
        text = json.dumps(out)
        reasoning = f"Mock trace: considered DTI {dti}, credit score {cs}." if self.effort not in (None, "none") else None
        rt = len(reasoning or "") // 4
        return CallResult(text=text, reasoning=reasoning, reasoning_kind="mock" if reasoning else None,
                          input_tokens=(len(system) + len(user)) // 4 + (300 if tool_spec else 0),
                          output_tokens=len(text) // 4 + rt, reasoning_tokens=rt, tool_calls=tool_calls, model_version=self.model)

# ---------------------------------------------------------------- OpenAI (Responses API)
class OpenAIClient(BaseClient):
    vendor = "openai"
    def _complete(self, system, user, tool_spec, tool_fn):
        import openai
        c = openai.OpenAI()
        kw = dict(model=self.model, instructions=system, max_output_tokens=self.max_output_tokens)
        if self.effort:
            kw["reasoning"] = {"effort": self.effort, "summary": "auto"} if self.effort != "none" else {"effort": "none"}
        tools = [{"type": "function", "name": tool_spec["name"], "description": tool_spec["description"],
                  "parameters": tool_spec["parameters"]}] if tool_spec else None
        inp = [{"role": "user", "content": user}]
        r = c.responses.create(input=inp, tools=tools, **kw) if tools else c.responses.create(input=inp, **kw)
        calls, itok, otok, rtok = [], r.usage.input_tokens, r.usage.output_tokens, getattr(r.usage.output_tokens_details, "reasoning_tokens", 0) or 0
        fc = [o for o in r.output if o.type == "function_call"]
        if fc:
            outs = list(r.output)
            for f in fc:
                args = json.loads(f.arguments); res = tool_fn(args)
                calls.append({"name": f.name, "args": args, "result": res})
                outs.append({"type": "function_call_output", "call_id": f.call_id, "output": json.dumps(res)})
            r = c.responses.create(input=inp + outs, tools=tools, **kw)
            itok += r.usage.input_tokens; otok += r.usage.output_tokens
            rtok += getattr(r.usage.output_tokens_details, "reasoning_tokens", 0) or 0
        summ = [s.text for o in r.output if o.type == "reasoning" for s in (o.summary or [])]
        return CallResult(r.output_text, "\n".join(summ) or None, "summary" if summ else None, itok, otok, rtok, calls, r.model)

# ---------------------------------------------------------------- Anthropic
class AnthropicClient(BaseClient):
    vendor = "anthropic"
    def _complete(self, system, user, tool_spec, tool_fn):
        import anthropic
        c = anthropic.Anthropic()
        kw = dict(model=self.model, system=system, max_tokens=self.max_output_tokens)
        if self.effort and self.effort != "none":
            budget = {"low": 1024, "medium": 4096, "high": 8192}[self.effort]
            kw["thinking"] = {"type": "enabled", "budget_tokens": budget}; kw["max_tokens"] = budget + self.max_output_tokens
        elif self.effort == "none":
            kw["thinking"] = {"type": "disabled"}
        tools = [{"name": tool_spec["name"], "description": tool_spec["description"], "input_schema": tool_spec["parameters"]}] if tool_spec else None
        msgs = [{"role": "user", "content": user}]
        r = c.messages.create(messages=msgs, tools=tools, **kw) if tools else c.messages.create(messages=msgs, **kw)
        itok, otok, calls, thinking = r.usage.input_tokens, r.usage.output_tokens, [], []
        thinking += [b.thinking for b in r.content if b.type == "thinking"]
        tu = [b for b in r.content if b.type == "tool_use"]
        if tu:
            results = []
            for b in tu:
                res = tool_fn(dict(b.input)); calls.append({"name": b.name, "args": dict(b.input), "result": res})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(res)})
            msgs += [{"role": "assistant", "content": r.content}, {"role": "user", "content": results}]
            r = c.messages.create(messages=msgs, tools=tools, **kw)
            itok += r.usage.input_tokens; otok += r.usage.output_tokens
            thinking += [b.thinking for b in r.content if b.type == "thinking"]
        text = "".join(b.text for b in r.content if b.type == "text")
        return CallResult(text, "\n".join(thinking) or None, "thinking" if thinking else None, itok, otok, 0, calls, r.model)

# ---------------------------------------------------------------- Google
class GoogleClient(BaseClient):
    vendor = "google"
    def _complete(self, system, user, tool_spec, tool_fn):
        from google import genai
        from google.genai import types
        c = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        tc = None
        if self.effort:
            budget = {"none": 0, "low": 1024, "medium": 4096, "high": 8192}[self.effort]
            tc = types.ThinkingConfig(thinking_budget=budget, include_thoughts=budget > 0)
        tools = [types.Tool(function_declarations=[types.FunctionDeclaration(
            name=tool_spec["name"], description=tool_spec["description"], parameters=tool_spec["parameters"])])] if tool_spec else None
        cfg = types.GenerateContentConfig(system_instruction=system, max_output_tokens=self.max_output_tokens, thinking_config=tc,
                                          tools=tools, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True) if tools else None)
        contents = [types.Content(role="user", parts=[types.Part(text=user)])]
        r = c.models.generate_content(model=self.model, contents=contents, config=cfg)
        def usage(r):
            u = r.usage_metadata
            return (u.prompt_token_count or 0), (u.candidates_token_count or 0) + (u.thoughts_token_count or 0), (u.thoughts_token_count or 0)
        itok, otok, rtok = usage(r); calls, thoughts = [], []
        parts = r.candidates[0].content.parts or []
        thoughts += [p.text for p in parts if getattr(p, "thought", False) and p.text]
        fcs = [p.function_call for p in parts if p.function_call]
        if fcs:
            contents.append(r.candidates[0].content); resp_parts = []
            for f in fcs:
                args = dict(f.args); res = tool_fn(args); calls.append({"name": f.name, "args": args, "result": res})
                resp_parts.append(types.Part.from_function_response(name=f.name, response=res))
            contents.append(types.Content(role="user", parts=resp_parts))
            r = c.models.generate_content(model=self.model, contents=contents, config=cfg)
            a, b, d = usage(r); itok += a; otok += b; rtok += d
            parts = r.candidates[0].content.parts or []
            thoughts += [p.text for p in parts if getattr(p, "thought", False) and p.text]
        text = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False))
        return CallResult(text, "\n".join(thoughts) or None, "thought_summary" if thoughts else None, itok, otok, rtok, calls, r.model_version)

def make_client(vendor, model, effort=None, **kw):
    return {"mock": MockClient, "openai": OpenAIClient, "anthropic": AnthropicClient, "google": GoogleClient}[vendor](model, effort, **kw)
