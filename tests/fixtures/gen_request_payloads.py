"""Build the exact request payloads the live clients send (sync clients via fake SDKs, batch bodies via Backend.body) for
core-run calls: OpenAI / Anthropic / Google x setups 1-3 (approve + deny profile, base variant) and the judge (hedging + mention).
No network. Usage: python gen_request_payloads.py <src_dir> <config.yaml> <out.json>   (also imported by the test at HEAD)."""
import json, os, sys, types as T

class _Stop(Exception): pass

def _ser(o):
    if isinstance(o, (str, int, float, bool)) or o is None: return o
    if isinstance(o, dict): return {str(k): _ser(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_ser(v) for v in o]
    return {"__class__": type(o).__name__, **{k: _ser(v) for k, v in vars(o).items()}}

class O:
    def __init__(self, **kw): self.__dict__.update(kw)

def _install_fakes(log):
    args = {"credit_score": 700, "dti_pct": 40.0, "ltv_pct": 80.0, "reserves_months": 3.0, "employment_years": 5}
    final = json.dumps({"decision": "deny", "letter": " ".join(["word"] * 25), "reason_codes": ["C09"], "reasons_text": ["x"], "rationale": "r"})
    # OpenAI
    oa = T.ModuleType("openai")
    class Responses:
        def create(self, **kw):
            log.append(("openai", _ser(kw))); n = sum(1 for v, _ in log if v == "openai")
            u = O(input_tokens=10, output_tokens=5, output_tokens_details=O(reasoning_tokens=0))
            if kw.get("tools") and n % 2 == 1:
                return O(usage=u, output=[O(type="function_call", arguments=json.dumps(args), call_id="c1", name="score_application")], output_text="", status="completed", incomplete_details=None, model="m")
            return O(usage=u, output=[O(type="message", content=[O(type="output_text", text=final)])], output_text=final, status="completed", incomplete_details=None, model="m")
    oa.OpenAI = lambda *a, **k: O(responses=Responses())
    # Anthropic
    an = T.ModuleType("anthropic")
    class Messages:
        def create(self, **kw):
            log.append(("anthropic", _ser(kw))); n = sum(1 for v, _ in log if v == "anthropic")
            u = O(input_tokens=10, output_tokens=5)
            if kw.get("tools") and n % 2 == 1:
                return O(usage=u, content=[O(type="tool_use", id="t1", name="score_application", input=args)], stop_reason="tool_use", model="m")
            return O(usage=u, content=[O(type="text", text=final)], stop_reason="end_turn", model="m")
    an.Anthropic = lambda *a, **k: O(messages=Messages())
    # Google
    ty = T.ModuleType("google.genai.types")
    for n in ("ThinkingConfig", "Tool", "FunctionDeclaration", "GenerateContentConfig", "AutomaticFunctionCallingConfig", "ToolConfig", "FunctionCallingConfig", "Content"):
        setattr(ty, n, type(n, (O,), {}))
    class Part(O):
        @classmethod
        def from_function_response(cls, name, response): return cls(function_response={"name": name, "response": response}, text=None, function_call=None)
    ty.Part = Part
    class Models:
        def generate_content(self, model, contents, config):
            log.append(("google", _ser({"model": model, "contents": contents, "config": config}))); n = sum(1 for v, _ in log if v == "google")
            usage = O(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0)
            if getattr(config, "tools", None) and n % 2 == 1 and not getattr(config, "tool_config", None):
                parts = [O(text=None, thought=False, function_call=O(name="score_application", args=args))]
            else: parts = [O(text=final, thought=False, function_call=None)]
            return O(candidates=[O(content=O(role="model", parts=parts), finish_reason=O(name="STOP"))], usage_metadata=usage, model_version="m")
    ge = T.ModuleType("google.genai"); ge.types = ty; ge.Client = lambda api_key=None: O(models=Models())
    g = T.ModuleType("google"); g.genai = ge
    saved = {k: sys.modules.get(k) for k in ("openai", "anthropic", "google", "google.genai", "google.genai.types")}
    sys.modules.update({"openai": oa, "anthropic": an, "google": g, "google.genai": ge, "google.genai.types": ty})
    return saved

def build(config_path):
    import yaml
    from llmbias.runner import build_record, load_items, tool_fn_factory, _clean
    from llmbias.clients import make_client
    from llmbias import batch as B
    from llmbias.scoring import TOOL_SPEC, score_application
    from llmbias.prompts import judge_hedging, judge_mention
    cfg = yaml.safe_load(open(config_path)); items = load_items(cfg); base = items[items.variant == "base"].sort_values("profile_id")
    apps = []
    for dec in ("approve", "deny"):
        apps.append(next(_clean(a) for a in base.to_dict("records") if score_application(a).decision == dec))
    out = {}; log = []
    os.environ.setdefault("GOOGLE_API_KEY", "offline-fake")
    saved = _install_fakes(log)
    try:
        for m in cfg["models"]:
            for setup in (1, 2, 3):
                for app in apps:
                    rec, sysm, user = build_record(cfg, "golden", True, m, setup, app, 0)
                    key = f"{m['vendor']}|s{setup}|{app['profile_id']}"
                    log.clear()
                    make_client(m["vendor"], m["model"], m.get("effort"), max_output_tokens=cfg["max_output_tokens"]).complete(
                        sysm, user, TOOL_SPEC if setup == 2 else None, tool_fn_factory() if setup == 2 else None, setup=setup)
                    out["sync|" + key] = [p for _, p in log]
                    it = {"model": m["model"], "system": sysm, "user": user, "max_out": cfg["max_output_tokens"], "effort": m.get("effort"),
                          "tools": setup == 2, "history": None, "round": 1, "setup": setup, "google_mode": cfg.get("google_tools_schema_mode", "final_turn")}
                    out["batch|r1|" + key] = _ser(B.BACKENDS[m["vendor"]].body(None, it))
                    if setup == 2: out["batch|r2|" + key] = _ser(B.BACKENDS[m["vendor"]].body(None, {**it, "round": 2}))
        j = cfg["judge"]; letter = "Dear Applicant, we are unable to approve your application due to your debt-to-income ratio. We doubt you will qualify."
        for name, (sm, um) in (("hedging", judge_hedging(letter)), ("mention", judge_mention("the neighborhood near [ZIP] was considered"))):
            log.clear(); make_client(j["vendor"], j["model"], j.get("effort"), max_output_tokens=300).complete(sm, um)
            out[f"sync|judge|{name}"] = [p for _, p in log]
            out[f"batch|judge|{name}"] = _ser(B.BACKENDS[j["vendor"]].body(None, {"model": j["model"], "system": sm, "user": um, "max_out": 300,
                                                                            "effort": j.get("effort"), "tools": False, "history": None, "round": 1}))
    finally:
        for k, v in saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v
    return out

if __name__ == "__main__":
    src, cfgp, outp = sys.argv[1:4]; sys.path.insert(0, src)
    os.chdir(os.path.dirname(os.path.abspath(src)))
    res = build(os.path.abspath(cfgp) if os.path.isabs(cfgp) else cfgp)
    json.dump(res, open(outp, "w"), indent=0, sort_keys=True)
    print(len(res), "payload keys")
