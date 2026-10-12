"""Pre-registered pilot outputs (feasibility only; pilot excluded from confirmatory analysis)."""
import json, sys, collections
import pandas as pd
sys.path.insert(0, "src")
from llmbias.analysis import load_calls
rd = sys.argv[1]
R = json.load(open(f"{rd}/analysis.json"))
raw = pd.read_json(f"{rd}/calls.jsonl", lines=True)
df = load_calls(rd)
df["completed"] = raw.error.isna().values
df["parse_ok"] = df.parsed.apply(lambda p: isinstance(p, dict) and bool(p.get("parse_ok")))
g = df.groupby(["vendor", "setup"])
tab = pd.DataFrame({"planned": g.size(), "completed": g.completed.sum(), "completion_rate": g.completed.mean(),
                    "parse_ok": g.parse_ok.sum(), "parse_rate_of_planned": g.parse_ok.mean(),
                    "parse_rate_of_completed": df[df.completed].groupby(["vendor", "setup"]).parse_ok.mean()}).reset_index()
s2 = df[(df.setup == 2) & df.completed].copy()
s2["override"] = s2.decision.notna() & s2.tool_decision.notna() & (s2.decision != s2.tool_decision)
t2 = s2.groupby("vendor").agg(n=("call_id", "size"), tool_called_rate=("tool_called", "mean"),
                              override_rate_vs_tool=("override", "mean")).reset_index()
called = s2[s2.tool_called.astype(bool)]
t2 = t2.merge(called.groupby("vendor").override.mean().rename("override_rate_when_tool_called").reset_index(), on="vendor", how="left")
c = collections.defaultdict(float)
for l in open(f"{rd}/spend_ledger.jsonl"):
    j = json.loads(l); c[j["vendor"]] += j["cost"]
rt = df[df.completed].groupby(["vendor", "setup"]).reasoning_tokens.apply(lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) > 0).sum())).rename("calls_with_reasoning_tokens").reset_index()
trig = {}
for p in ("name", "zip"):
    m = R[f"M3_{p}"]
    trig[p] = {"n_pairs": m["n_pairs"], "flip_rate": m["flip_rate"], "control_flip_rate": m["control_flip_rate"], "net_flip_rate": m["net_flip_rate"],
               "newcombe_95ci": [m["newcombe"]["lo"], m["newcombe"]["hi"]], "method": m["trigger_method"],
               "trigger_fallback(upper<0.10)": bool(m["trigger_upper"] < 0.10)}
out = {"run_id": R["run_id"], "label": R["label"], "completion_parse": tab.round(4).to_dict("records"), "setup2_tool": t2.round(4).to_dict("records"),
       "fallback_trigger_setup1_pooled_models": trig, "spend_usd_by_vendor": {k: round(v, 4) for k, v in c.items()}, "spend_total_usd": round(sum(c.values()), 4),
       "reasoning_token_check": rt.to_dict("records"), "overall_parse_rate": R["parse_rate"]}
json.dump(out, open(f"{rd}/pilot_summary.json", "w"), indent=1, default=float)
md = [f"# Pilot summary {R['run_id']}", "", R["label"], "", "## Completion and parse", tab.round(3).to_markdown(index=False), "",
      "## Setup 2 (AI + tool)", t2.round(3).to_markdown(index=False), "", "## Fallback trigger (pre-reg 15.3 #5; setup 1, majority-of-3, net of control)",
      pd.DataFrame(trig).T.to_markdown(), "", "## Spend (USD, batch prices)", json.dumps(out["spend_usd_by_vendor"]) + f"  total {out['spend_total_usd']}", "",
      "## Calls with nonzero reasoning tokens", rt.to_markdown(index=False)]
open(f"{rd}/pilot_summary.md", "w").write("\n".join(md)); print("\n".join(md))
