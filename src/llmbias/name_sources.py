"""Registry of perception-scored name sources (Reviewer conditions, 2026-10-12).

Only sources with status == "active" may feed the name pools. Candidate sources are documented and have a loader
so they can be evaluated, but build_data refuses them until the Researcher/Reviewer approve integration
(set status to "active" AND list the source in configs/data.yaml names.sources).

Each loader returns one row per rated name in a common schema:
  name, first, last, race (W/B), gender (F/M), race_recognition (0-1, forced-choice congruent share),
  n_ratings, perceived_income, perceived_edu, recognition_basis ("full_name" | "first_name_alone"), name_source
"""
import os
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RAW = os.path.join(ROOT, "data", "raw")

SOURCES = {
    "crabtree2023": dict(
        status="active",
        citation="Crabtree, Kim, Gaddis, Holbein, Guage & Marx (2023). Validated names for experimental studies on race and ethnicity. Scientific Data 10:130.",
        url="https://doi.org/10.7910/DVN/LP4EAR", license="CC0 1.0",
        recognition="forced-choice race identification; share of raters choosing the intended race",
        first_name_alone=False,  # rated as full first+last names
        class_measures=["perceived income (ordinal)", "perceived education (ordinal)"],
    ),
    "wba2025": dict(
        status="candidate",
        citation="Vaseiezadeh, Campbell, Gaddis & Onwuachi-Willig (2025). Beyond the Name: Selecting White, Black, and African Immigrant Names for Experimental Studies. Replication material, OSF.",
        url="https://osf.io/xfmnh/ (file Data/wba.csv, https://osf.io/download/7yn9d/)", license="none stated on OSF node (ask authors / cite)",
        recognition="forced-choice 7-category race item ('What race or ethnicity do you think most people would believe a person with this name is?'); share choosing intended race",
        first_name_alone=True,
        class_measures=["perceived income (3 bands, midpoint $)", "perceived education (5-level)"],
    ),
    "elder_hayes2023": dict(
        status="active",  # integrated via amendment 15.6 (names_v3.py), own thresholds, not the 0.70 cutoff
        citation="Elder & Hayes (2023). Signaling Race, Ethnicity, and Gender with Names. Journal of Politics 85(2).",
        url="https://doi.org/10.7910/DVN/47CZDX", license="CC0 1.0",
        recognition="1-5 Likert 'how likely is [name] to be [race]', each race rated separately; NOT a forced-choice share",
        first_name_alone=True,
        class_measures=["working/middle/upper class (1-5), 336 names only"],
    ),
    "gaddis2017": dict(
        status="rejected",
        citation="Gaddis (2017). How Black Are Lakisha and Jamal? Sociological Science 4:469-489.",
        url="https://doi.org/10.15195/v4.a19", license="CC BY (article); no name-level data file found",
        recognition="forced-choice congruent perception; first name alone and with surnames",
        first_name_alone=True,
        class_measures=["none perceived; mother's education from NY birth records (objective)"],
    ),
}


def load_crabtree2023():
    s = pd.concat([pd.read_csv(os.path.join(RAW, "crabtree", f"study-{i}-names.csv")) for i in (1, 2, 3)])
    g = s.groupby(["name", "first", "last", "identity"]).agg(
        n_ratings=("correct", "size"), race_recognition=("correct", "mean"),
        perceived_income=("income.ord", "mean"), perceived_edu=("education.ord", "mean")).reset_index()
    g = g[g.identity.isin(["White", "Black or African American"])].copy()
    g["race"] = g.identity.map({"White": "W", "Black or African American": "B"})
    g["recognition_basis"] = "full_name"; g["name_source"] = "crabtree2023"
    return g.drop(columns="identity")


def load_wba2025(path=None):
    d = pd.read_csv(path or os.path.join(RAW, "candidates", "gaddis_wba", "wba.csv"))
    d = d[(d.true_white == 1) | (d.true_black == 1)].copy()
    d["race"] = d.true_white.map({1: "W", 0: "B"})
    d["hit"] = ((d.race == "W") & (d.white_p == 1)) | ((d.race == "B") & (d.black_p == 1))
    g = d.groupby(["name", "race"]).agg(n_ratings=("hit", "size"), race_recognition=("hit", "mean"), true_female=("true_female", "first"),
                                         perceived_income=("income_pm", "mean"), perceived_edu=("edu_p", "mean")).reset_index()
    g["first"] = g.name; g["last"] = None; g["gender"] = g.true_female.map({1: "F", 0: "M"})
    g["recognition_basis"] = "first_name_alone"; g["name_source"] = "wba2025"
    return g.drop(columns="true_female")


LOADERS = {"crabtree2023": load_crabtree2023, "wba2025": load_wba2025}


def load(source, allow_candidate=False):
    meta = SOURCES[source]
    if meta["status"] != "active" and not (allow_candidate and meta["status"] == "candidate"):
        raise ValueError(f"name source {source!r} has status {meta['status']!r}; not approved for the name pools")
    return LOADERS[source]()


def qualifying_counts(g, thr=0.70):
    """Distinct first names per race x gender at race_recognition >= thr (the Reviewer's comparability check)."""
    q = g[g.race_recognition >= thr]
    return q.groupby(["race", "gender"])["first"].nunique().to_dict()
