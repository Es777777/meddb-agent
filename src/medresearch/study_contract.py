"""Versioned multi-dataset study protocol; v1 SQL proposals remain compatible."""
from __future__ import annotations

import copy
from typing import Any

from medresearch.contracts import digest

DATASETS = {
    "mimiciv": {"schemas": ["mimiciv_hosp", "mimiciv_icu"], "survey": False,
                "units": ["patient", "admission", "icu_stay", "event"]},
    **{name: {"schemas": [name], "survey": False, "units": ["patient", "tumor"]}
       for name in ("seer8", "seer12", "seer17")},
    "nhanes": {"schemas": ["nhanes"], "survey": True, "units": ["participant"]},
    "brfss": {"schemas": ["brfss"], "survey": True, "units": ["response"]},
}
METHODS = ["describe", "linear", "logistic", "kaplan_meier", "cox", "competing_risk",
           "survey_mean", "survey_linear", "survey_logistic", "predict", "aipw"]


def obj(properties, required=()):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(required)}


def string():
    return {"type": "string", "minLength": 1}


def strings():
    return {"type": "array", "items": string(), "uniqueItems": True}


def study_schema() -> dict[str, Any]:
    analysis = {key: string() for key in ("outcome", "exposure", "duration", "event", "group",
                "weight", "strata", "psu", "split_column", "cluster_key", "estimand")}
    analysis.update(method={"enum": METHODS}, covariates=strings(), categorical=strings(),
        missing={"enum": ["complete_case", "median"]}, seed={"type": "integer", "minimum": 0},
        test_fraction={"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 0.5},
        split={"enum": ["group", "temporal"]}, causal_assumptions=strings(),
        propensity_clip={"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 0.2},
        folds={"type": "integer", "minimum": 2, "maximum": 10},
        outcome_type={"enum": ["binary", "continuous"]},
        confidence={"type": "number", "minimum": 0.8, "maximum": 0.99})
    criterion = obj({"column": string(), "op": {"enum": ["eq", "ne", "gt", "ge", "lt", "le", "in"]},
                     "value": {"type": ["string", "number", "boolean", "array"]}}, ["column", "op", "value"])
    source_common = {"kind": {"enum": ["file"]}, "path": string()}
    return obj({
        "schema_version": {"const": 2}, "study_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"},
        "question": string(), "dataset": obj({"name": {"enum": list(DATASETS)}, "version": string()}, ["name", "version"]),
        "analysis_unit": string(), "population": {"enum": ["sample", "population"]},
        "design": obj({key: string() for key in ("index_event", "time_window", "denominator",
            "exposure_window", "followup", "censoring", "estimand")},
            ["index_event", "time_window", "denominator", "exposure_window", "followup", "censoring", "estimand"]),
        "data": {"oneOf": [obj(source_common, source_common), obj({"kind": {"const": "postgres"},
            "config": string(), "sql": string(), "params": {"type": "object"},
            "output_columns": strings()}, ["kind", "config", "sql", "params", "output_columns"])]},
        "cohort": {"type": "array", "items": criterion},
        "analysis": obj(analysis, ["method"]),
        "quality": obj({"keys": strings(), "min_rows": {"type": "integer", "minimum": 5},
            "max_missing": {"type": "number", "minimum": 0, "maximum": 1},
            "ranges": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}},
            "allowed_values": {"type": "object", "additionalProperties": {"type": "array"}}},
            ["keys", "min_rows", "max_missing"]),
        "definitions": {"type": "array", "items": obj({"column": string(), "concept": string(),
            "unit": string(), "operational_definition": string(), "source_id": string(),
            "reviewed": {"type": "boolean"}, "availability": {"enum": ["baseline", "post_index", "unknown"]}},
            ["column", "concept", "unit", "operational_definition", "source_id", "reviewed", "availability"])},
        "evidence": {"type": "array", "items": obj({"id": string(), "title": string(), "url": string(),
            "pmid": string(), "year": {"type": "integer"}, "verification": {"enum": ["curated", "pubmed", "unverified"]}},
            ["id", "title", "url", "verification"])},
        "sensitivity": {"type": "array", "items": obj({"name": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+$"},
            "analysis_overrides": obj({key: analysis[key] for key in ("covariates", "missing", "propensity_clip")}),
            "cohort": {"type": "array", "items": criterion}}, ["name", "analysis_overrides", "cohort"])},
    }, ["schema_version", "study_id", "question", "dataset", "analysis_unit", "population", "design", "data",
        "cohort", "analysis", "quality", "definitions", "evidence", "sensitivity"])


def analysis_columns(analysis: dict) -> list[str]:
    names = [analysis[key] for key in ("outcome", "exposure", "duration", "event", "group",
             "weight", "strata", "psu", "split_column", "cluster_key") if key in analysis]
    return list(dict.fromkeys(names + analysis.get("covariates", [])))


def validate_study(value: Any) -> dict:
    from jsonschema import Draft202012Validator
    errors = sorted(Draft202012Validator(study_schema()).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        # Paths help authors repair the protocol; avoid echoing clinical values.
        raise ValueError("Invalid study protocol at " + "/".join(map(str, errors[0].path)))
    spec = copy.deepcopy(value)
    digest(spec)
    if spec["data"]["kind"] == "postgres" and any(isinstance(v, (dict, list)) for v in spec["data"]["params"].values()):
        raise ValueError("Cohort SQL parameters must be scalar values")
    dataset = DATASETS[spec["dataset"]["name"]]
    if spec["analysis_unit"] not in dataset["units"]:
        raise ValueError("Analysis unit is not valid for this dataset")
    if not spec["quality"]["keys"]:
        raise ValueError("Declare analysis-unit keys for cardinality checks")
    a = spec["analysis"]
    method = a["method"]
    required = {"describe": ["outcome"], "linear": ["outcome", "exposure"],
        "logistic": ["outcome", "exposure"], "kaplan_meier": ["duration", "event"],
        "cox": ["duration", "event", "exposure"], "competing_risk": ["duration", "event"],
        "survey_mean": ["outcome", "weight", "strata", "psu"],
        "survey_linear": ["outcome", "exposure", "weight", "strata", "psu"],
        "survey_logistic": ["outcome", "exposure", "weight", "strata", "psu"],
        "predict": ["outcome", "covariates", "cluster_key", "split"],
        "aipw": ["outcome", "exposure", "covariates", "cluster_key", "causal_assumptions", "outcome_type"]}[method]
    if any(key not in a or not a[key] for key in required):
        raise ValueError("Analysis method is missing required variable roles")
    if spec["dataset"]["name"] == "mimiciv" and spec["analysis_unit"] in {"admission", "icu_stay"} and method in {"linear", "logistic", "cox"} and "cluster_key" not in a:
        raise ValueError("Repeated MIMIC admissions/stays require a declared patient cluster key")
    if a.get("split") == "temporal" and "split_column" not in a:
        raise ValueError("Temporal prediction requires a split_column")
    if method.startswith("survey_") and not dataset["survey"]:
        raise ValueError("Survey methods require a survey dataset")
    if spec["population"] == "population" and dataset["survey"] and not method.startswith("survey_"):
        raise ValueError("Population inference requires a survey method and design variables")
    if a.get("missing", "complete_case") == "median" and method not in {"linear", "logistic", "predict", "aipw"}:
        raise ValueError("Median imputation is unsupported for this analysis method")
    if method == "aipw" and not {"exchangeability", "positivity", "consistency", "no_interference"} <= set(a["causal_assumptions"]):
        raise ValueError("AIPW requires explicit causal identification assumptions")
    features = a.get("covariates", []) + ([a["exposure"]] if "exposure" in a else [])
    if set(features) & {a.get("outcome"), a.get("duration"), a.get("event"), a.get("cluster_key"), a.get("split_column")}:
        raise ValueError("Outcome, identifiers and split variables cannot be model features")
    if not set(a.get("categorical", [])) <= set(features):
        raise ValueError("Categorical columns must be declared model features")
    definitions = {item["column"]: item for item in spec["definitions"]}
    if len(definitions) != len(spec["definitions"]):
        raise ValueError("Duplicate variable definitions")
    if not set(analysis_columns(a)) <= set(definitions):
        raise ValueError("Every analysis variable requires an operational definition")
    evidence = {item["id"] for item in spec["evidence"]}
    if len(evidence) != len(spec["evidence"]) or any(item["source_id"] not in evidence for item in definitions.values()):
        raise ValueError("Definition sources must resolve to unique evidence entries")
    if method in {"predict", "aipw"} and any(definitions[c]["availability"] != "baseline" for c in features):
        raise ValueError("Prediction/causal features must be declared available at baseline")
    if len({x["name"] for x in spec["sensitivity"]}) != len(spec["sensitivity"]):
        raise ValueError("Sensitivity names must be unique")
    for name, bounds in spec["quality"].get("ranges", {}).items():
        if bounds[0] > bounds[1]:
            raise ValueError("Invalid quality range")
    for sensitivity in spec["sensitivity"]:
        variant = copy.deepcopy(spec)
        variant["analysis"].update(sensitivity["analysis_overrides"])
        variant["cohort"] += sensitivity["cohort"]
        variant["sensitivity"] = []
        validate_study(variant)
    return spec
