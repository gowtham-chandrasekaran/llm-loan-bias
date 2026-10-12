import os, sys
import pandas as pd, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from llmbias import name_sources as ns
from llmbias.analysis import _name_source_term, mixed_logit

def test_registry_and_gate():
    assert ns.SOURCES["crabtree2023"]["status"] == "active"
    assert all(k in m for m in ns.SOURCES.values() for k in ("status", "citation", "url", "recognition", "first_name_alone", "class_measures"))
    with pytest.raises(ValueError): ns.load("wba2025")          # candidate: not integrated

@pytest.mark.skipif(not os.path.exists(os.path.join(ns.RAW, "crabtree")), reason="raw Crabtree data not downloaded")
def test_crabtree_loader_schema():
    g = ns.load("crabtree2023")
    assert {"name", "first", "race", "race_recognition", "name_source", "recognition_basis"} <= set(g.columns)
    assert (g.name_source == "crabtree2023").all()

def _df(sources):
    rows = []
    for i, src in enumerate(sources):
        for v, dec in (("name_w", "approve"), ("name_b", "deny" if i % 2 else "approve")):
            for r in range(3):
                rows.append(dict(setup=1, variant=v, decision=dec if r else ("deny" if dec == "approve" else "approve"), model="m", effort="none",
                                 proxy_state="42", profile_id=f"p{i}", name_source=src))
    return pd.DataFrame(rows)

def test_name_source_covariate_added_only_with_multiple_levels():
    d1, use1 = _name_source_term(_df(["crabtree2023"] * 8)); assert not use1
    d2, use2 = _name_source_term(_df(["crabtree2023", "wba2025"] * 4)); assert use2 and set(d2.name_source) == {"crabtree2023", "wba2025"}
    mixed = _df(["crabtree2023"] * 4); mixed.loc[mixed.variant == "name_b", "name_source"] = "wba2025"
    d3, _ = _name_source_term(mixed); assert set(d3.name_source) == {"mixed"}
    assert "C(name_source)" in mixed_logit(_df(["crabtree2023", "wba2025"] * 4), ("name_w", "name_b"))["formula"]

from llmbias.analysis import cluster_robust_mean, name_sign_flip_perm, name_level

def test_name_level_inference_and_lono():
    import numpy as np
    rows = []
    for k in range(6):
        for prof in range(4):
            for v, dec in (("base", "approve"), ("name_b", "deny" if (prof + k) % 4 else "approve")):
                for r in range(3):
                    rows.append(dict(vendor="mock", model="m", effort="none", setup=1, profile_id=f"p{k}_{prof}", variant=v, decision=dec,
                                     name_pair_id=f"N{k}", name_pair_strict_155=k < 2, applicant_name=f"B{k}" if v == "name_b" else f"W{k}"))
    df = pd.DataFrame(rows)
    R = name_level(df, holm_p=0.01, n_perm=1000)
    assert R["full_cluster_robust"]["G"] == 6 and 0 < R["full_cluster_robust"]["est"] <= 1
    assert len(R["per_name"]) == 6 and len(R["lono"]) == 6 and R["perm_name_level"]["exact"]
    assert R["lono_all_pass"] and R["robust_confirmed"]
    S = name_level(df, strict_only=True, n_perm=1000); assert S["full_cluster_robust"]["G"] == 2
    assert name_sign_flip_perm([1, 1, -1, -1], ["a", "a", "b", "b"])["p"] == 1.0
    assert abs(cluster_robust_mean([1, 0, 1, 0], ["a", "a", "b", "b"])["est"] - 0.5) < 1e-9

def test_15_7_pair_gap_covariates():
    from llmbias.analysis import _pair_gap_covs
    n = pd.read_csv(os.path.join(ns.ROOT, "data", "processed", "name_pairs.csv"))
    d, use = _pair_gap_covs(pd.DataFrame({"name_pair_id": n.name_pair_id}))
    assert use and (d.class_gap_abs >= 0).all() and d.class_gap_abs.notna().all()
    miss = n.class_w.isna() | n.class_b.isna()
    assert (d.class_missing.values == miss.astype(int).values).all() and (d.class_gap_abs[miss.values] == 0).all()
