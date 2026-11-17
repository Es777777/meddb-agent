"""Deterministic synthetic end-to-end acceptance examples, never medical evidence."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from medresearch.runtime import atomic_json
from medresearch.study_contract import analysis_columns
from medresearch.workflow import StudyRunner, protected_output, validate_across_runs


def synthetic_data(seed: int = 47, n: int = 480) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    treatment = rng.binomial(1, 1/(1+np.exp(-.5*x)))
    outcome = 1.2*treatment+.7*x+rng.normal(size=n)
    binary = rng.binomial(1, 1/(1+np.exp(-(-.6+.5*treatment+.4*x))))
    failure = rng.exponential(scale=10/np.exp(.35*treatment+.2*x))
    censor = rng.exponential(scale=18, size=n)
    event = (failure <= censor).astype(int)
    competing = np.where((event == 1) & (rng.uniform(size=n) < .25), 2, event)
    return pd.DataFrame({"record_id": np.arange(n), "person_id": np.arange(n)//2,
        "x": x, "treatment": treatment, "outcome": outcome, "binary": binary,
        "duration": np.minimum(failure, censor), "event": event, "competing": competing,
        "weight": rng.uniform(.5, 2, size=n), "strata": np.arange(n)%4,
        "psu": (np.arange(n)//4)%8, "time_order": np.arange(n)})


def example_spec(method: str, path: str, *, dataset: str = "mimiciv", study_id: str | None = None) -> dict:
    a = {"method": method, "seed": 47, "missing": "complete_case", "confidence": .95}
    if method in {"describe", "linear", "survey_mean", "survey_linear", "aipw"}:
        a["outcome"] = "outcome"
    elif method in {"logistic", "survey_logistic", "predict"}:
        a["outcome"] = "binary"
    if method in {"linear", "logistic", "cox", "survey_linear", "survey_logistic", "aipw"}:
        a.update(exposure="treatment", covariates=["x"])
    if method in {"kaplan_meier", "cox", "competing_risk"}:
        a.update(duration="duration", event="competing" if method == "competing_risk" else "event")
    if method.startswith("survey_"):
        a.update(weight="weight", strata="strata", psu="psu")
    if method == "predict":
        a.update(covariates=["x", "treatment"], cluster_key="person_id", split="group", missing="median")
    if method == "aipw":
        a.update(cluster_key="person_id", outcome_type="continuous", folds=3,
            causal_assumptions=["exchangeability", "positivity", "consistency", "no_interference"],
            propensity_clip=.01, estimand="ATE in the synthetic source sample")
    if dataset == "mimiciv" and method in {"linear", "logistic", "cox"}:
        a["cluster_key"] = "person_id"
    definitions = [{"column": column, "concept": column, "unit": "synthetic units",
        "operational_definition": "Synthetic generated variable: "+column,
        "source_id": "synthetic-design", "reviewed": True,
        "availability": "post_index" if column in {"outcome", "binary", "duration", "event", "competing"} else "baseline"}
        for column in analysis_columns(a)]
    unit = "participant" if dataset == "nhanes" else "response" if dataset == "brfss" else "tumor" if dataset.startswith("seer") else "admission"
    return {"schema_version": 2, "study_id": study_id or "synthetic-"+method,
        "question": "Synthetic acceptance example for "+method+"; not medical evidence",
        "dataset": {"name": dataset, "version": "synthetic-fixture-v1"}, "analysis_unit": unit,
        "population": "population" if method.startswith("survey_") else "sample",
        "design": {"index_event": "synthetic baseline", "time_window": "complete generated sample",
            "denominator": "eligible synthetic records", "exposure_window": "at synthetic baseline",
            "followup": "generated duration when applicable", "censoring": "independent synthetic censoring when applicable",
            "estimand": "prespecified synthetic acceptance statistic for "+method},
        "data": {"kind": "file", "path": path}, "cohort": [], "analysis": a,
        "quality": {"keys": ["record_id"], "min_rows": 30, "max_missing": .2,
            "ranges": {"x": [-10, 10]}, "allowed_values": {"treatment": [0, 1]}},
        "definitions": definitions,
        "evidence": [{"id": "synthetic-design", "title": "Deterministic synthetic fixture; no clinical claim",
            "url": "https://example.org/synthetic-study", "verification": "curated"}],
        "sensitivity": [{"name": "restricted-x", "analysis_overrides": {},
            "cohort": [{"column": "x", "op": "ge", "value": -2}, {"column": "x", "op": "le", "value": 2}]}]}


def run_demo(output: Path) -> dict:
    protected_output(output)
    if output.exists():
        raise ValueError("Demo output already exists; choose a new directory")
    output.mkdir(parents=True)
    path = output.resolve()/"synthetic-input.csv"
    synthetic_data().to_csv(path, index=False)
    scenarios = [("describe", "nhanes"), ("linear", "mimiciv"), ("logistic", "seer8"),
        ("kaplan_meier", "seer12"), ("cox", "seer17"), ("competing_risk", "seer17"),
        ("survey_mean", "nhanes"), ("survey_linear", "brfss"), ("survey_logistic", "nhanes"),
        ("predict", "mimiciv"), ("aipw", "mimiciv")]
    records = []
    for method, dataset in scenarios:
        spec = example_spec(method, str(path), dataset=dataset)
        atomic_json(output/(method+".study.json"), spec)
        state = StudyRunner(output/method).run(spec, base=output)
        records.append({"method": method, "dataset": dataset, "status": state["status"],
                        "error_type": state.get("error_type"), "stages": state["stages"]})
    # A second independent synthetic sample demonstrates evidence comparison.
    second_path = output.resolve()/"synthetic-replication.csv"
    synthetic_data(seed=73).to_csv(second_path, index=False)
    replication = example_spec("linear", str(second_path), study_id="synthetic-independent-replication")
    replication["sensitivity"] = []
    second = StudyRunner(output/"replication").run(replication, base=output)
    comparison_status = "unavailable"
    if second["status"] in {"completed", "needs_review"} and records[1]["status"] in {"completed", "needs_review"}:
        validate_across_runs([output/"linear", output/"replication"], output/"comparison")
        comparison_status = "completed"
    summary = {"data": "synthetic only", "real_benchmark_executed": False, "scenarios": records,
               "comparison": comparison_status,
               "passed": all(x["status"] in {"completed", "needs_review"} for x in records) and comparison_status == "completed"}
    atomic_json(output/"demo-summary.json", summary)
    return summary
