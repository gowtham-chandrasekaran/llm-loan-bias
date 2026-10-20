"""Live-client request payloads (OpenAI/Anthropic/Google; setups 1-3 sync + batch round 1/2; judge) at HEAD
must be byte-identical to golden fixtures generated from 08082d6 (smoke 2) with tests/fixtures/gen_request_payloads.py."""
import json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_payloads_match_08082d6(tmp_path):
    out = tmp_path / "head.json"
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    subprocess.run([sys.executable, os.path.join(ROOT, "tests", "fixtures", "gen_request_payloads.py"), os.path.join(ROOT, "src"),
                    "configs/minipilot_haiku_batch.yaml", str(out)], check=True, cwd=ROOT, env=env, capture_output=True)
    gold = json.load(open(os.path.join(ROOT, "tests", "fixtures", "request_payloads_08082d6.json"))); head = json.load(open(out))
    assert len(gold) == 46 and sorted(gold) == sorted(head)
    assert {k for k in gold if gold[k] != head[k]} == set()
    assert open(out, "rb").read() == open(os.path.join(ROOT, "tests", "fixtures", "request_payloads_08082d6.json"), "rb").read()
