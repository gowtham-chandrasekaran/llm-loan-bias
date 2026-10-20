"""Live-client request payloads (OpenAI/Anthropic/Google; setups 1-3 sync + batch round 1/2; judge), built offline with
tests/fixtures/gen_request_payloads.py (fake SDKs, no network).
- request_payloads_08082d6.json: golden payloads before 15.9.1 (smoke 2 / 08082d6; reproduced byte-identically from 499230e).
- request_payloads_15_9_1.json:  golden payloads after 15.9.1 (forced round-1 tool call in setup 2).
HEAD must reproduce the 15.9.1 fixture byte-for-byte, and the two fixtures may differ ONLY in the setup-2 round-1 payloads,
and there only by the forced tool_choice (OpenAI / Anthropic) or tool_config mode ANY (Gemini)."""
import copy, json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
OLD, NEW = os.path.join(FIX, "request_payloads_08082d6.json"), os.path.join(FIX, "request_payloads_15_9_1.json")
VENDORS, PIDS = ("openai", "anthropic", "google"), ("P000", "P001")
FORCED = {"openai": {"type": "function", "name": "score_application"},
          "anthropic": {"type": "tool", "name": "score_application"},
          "google": {"function_calling_config": {"mode": "ANY", "allowed_function_names": ["score_application"]}}}

def _strip_class(o):
    if isinstance(o, dict): return {k: _strip_class(v) for k, v in o.items() if k != "__class__"}
    if isinstance(o, list): return [_strip_class(v) for v in o]
    return o

def _unforce(vendor, payload):
    """Remove (and return) the 15.9.1 forced-tool fragment from a round-1 payload."""
    p = copy.deepcopy(payload)
    if vendor == "google": return p, p["config"].pop("tool_config")
    return p, p.pop("tool_choice")

def test_payloads_match_15_9_1(tmp_path):
    out = tmp_path / "head.json"
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    subprocess.run([sys.executable, os.path.join(FIX, "gen_request_payloads.py"), os.path.join(ROOT, "src"),
                    "configs/minipilot_haiku_batch.yaml", str(out)], check=True, cwd=ROOT, env=env, capture_output=True)
    gold = json.load(open(NEW)); head = json.load(open(out))
    assert len(gold) == 46 and sorted(gold) == sorted(head)
    assert {k for k in gold if gold[k] != head[k]} == set()
    assert open(out, "rb").read() == open(NEW, "rb").read()

def test_15_9_1_changes_only_setup2_round1():
    old, new = json.load(open(OLD)), json.load(open(NEW))
    assert sorted(old) == sorted(new) and len(old) == 46
    expect = {f"{p}|{v}|s2|{pid}" for p in ("sync", "batch|r1") for v in VENDORS for pid in PIDS}
    changed = {k for k in old if json.dumps(old[k], sort_keys=True) != json.dumps(new[k], sort_keys=True)}
    assert changed == expect                                     # setups 1 and 3, batch round 2 and the judge: byte-identical
    for k in set(old) - expect: assert json.dumps(old[k], sort_keys=True) == json.dumps(new[k], sort_keys=True)
    for v in VENDORS:
        for pid in PIDS:
            b_old, b_new = old[f"batch|r1|{v}|s2|{pid}"], new[f"batch|r1|{v}|s2|{pid}"]
            rest, forced = _unforce(v, b_new)
            assert forced == FORCED[v] and rest == b_old                 # batch round 1: only the forced fragment added
            s_old, s_new = old[f"sync|{v}|s2|{pid}"], new[f"sync|{v}|s2|{pid}"]
            assert len(s_old) == len(s_new) == 2 and s_old[1] == s_new[1]   # sync round 2 unchanged
            rest, forced = _unforce(v, s_new[0])
            assert _strip_class(forced) == FORCED[v] and rest == s_old[0]   # sync round 1: only the forced fragment added
            r2 = new[f"batch|r2|{v}|s2|{pid}"]                          # round 2 is never forced
            assert "tool_choice" not in r2 and "tool_choice" not in s_new[1]
            if v == "google":
                for c in (r2["config"], s_new[1]["config"]):
                    assert _strip_class(c["tool_config"]) == {"function_calling_config": {"mode": "NONE"}} and c["response_json_schema"]
            if v == "openai":                                            # strict tool + parallel_tool_calls false kept on both rounds
                for b in (b_new, r2, s_new[0], s_new[1]):
                    assert b["parallel_tool_calls"] is False and b["tools"][0]["strict"] is True
            if v == "anthropic":                                         # thinking stays disabled with the forced tool
                for b in (b_new, r2, s_new[0], s_new[1]): assert b["thinking"] == {"type": "disabled"}
