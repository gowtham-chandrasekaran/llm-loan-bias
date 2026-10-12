"""Export blinded items for human labeling and compute Cohen's kappa vs judge.
  python -m llmbias.labels export --run runs/<id> --n 120
  python -m llmbias.labels kappa  --run runs/<id> --labels runs/<id>/labels_for_gowtham.csv"""
import argparse, json, os, random
import pandas as pd

def export(run_dir, n=120, seed=1):
    J = [json.loads(l) for l in open(os.path.join(run_dir, "judgments.jsonl"))]
    rng = random.Random(seed); rng.shuffle(J)
    rows = [{"item_id": j["item_id"], "task": "hedging", "text": j["letter_redacted"], "human_label": ""} for j in J[:n]]
    spans = [(j["item_id"], i, s) for j in J for i, s in enumerate(j["spans"])]
    rng.shuffle(spans)
    rows += [{"item_id": f"{iid}#s{i}", "task": "mentions_proxy", "text": s["span_redacted"], "human_label": ""} for iid, i, s in spans[:max(0, n // 2)]]
    df = pd.DataFrame(rows).sample(frac=1, random_state=seed)   # no variant, model or judge label exported
    p = os.path.join(run_dir, "labels_for_gowtham.csv"); df.to_csv(p, index=False); return p, len(df)

def cohen_kappa(a, b):
    a, b = list(a), list(b); n = len(a); cats = sorted(set(a) | set(b), key=str)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)

def kappa(run_dir, labels_csv):
    J = {j["item_id"]: j for j in (json.loads(l) for l in open(os.path.join(run_dir, "judgments.jsonl")))}
    L = pd.read_csv(labels_csv, dtype=str).dropna(subset=["human_label"])
    res = {}
    for task, g in L.groupby("task"):
        jl, hl = [], []
        for _, r in g.iterrows():
            if task == "hedging": jv = J[r.item_id]["hedging"]
            else:
                iid, si = r.item_id.split("#s"); jv = J[iid]["spans"][int(si)]["mentions_proxy"]
            jl.append(str(bool(jv)).lower()); hl.append(r.human_label.strip().lower())
        k = cohen_kappa(jl, hl); res[task] = {"n": len(jl), "kappa": round(k, 3), "passes_0.7": k >= 0.7}
    return res

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("--run", required=True); ap.add_argument("--n", type=int, default=120); ap.add_argument("--labels")
    a = ap.parse_args(); print(export(a.run, a.n) if a.cmd == "export" else kappa(a.run, a.labels))
