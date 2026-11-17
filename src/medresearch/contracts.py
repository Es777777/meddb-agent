from __future__ import annotations

import hashlib
import json
import math
from typing import Any


SPEC_FIELDS = (
    "task_id", "question", "dataset", "dataset_version", "analysis_unit",
    "objective", "cohort", "exposure", "outcome", "index_event", "time_window",
    "denominator", "statistic",
)
LIST_FIELDS = ("covariates", "output_columns", "assumptions", "definition_sources")


def digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proposal_schema() -> dict[str, Any]:
    spec = {name: {"type": "string", "minLength": 1} for name in SPEC_FIELDS}
    spec.update({name: {"type": "array", "items": {"type": "string"}} for name in LIST_FIELDS})
    spec["dataset"] = {"type": "string", "enum": ["mimiciv"]}
    spec["analysis_unit"] = {"type": "string", "enum": ["patient", "admission", "icu_stay", "event"]}
    spec["objective"] = {"type": "string", "enum": ["descriptive"]}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["status", "spec", "sql", "params_json", "limitations"],
        "properties": {
            "status": {"type": "string", "enum": ["ready", "needs_clarification", "unsupported"]},
            "spec": {"type": "object", "additionalProperties": False,
                     "required": list(spec), "properties": spec},
            "sql": {"type": "string"}, "params_json": {"type": "string"},
            "limitations": {"type": "array", "items": {"type": "string"}},
        },
    }


def validate_proposal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"status", "spec", "sql", "params_json", "limitations"}:
        raise ValueError("Invalid proposal fields")
    if value["status"] not in {"ready", "needs_clarification", "unsupported"}:
        raise ValueError("Invalid proposal status")
    spec = value["spec"]
    if not isinstance(spec, dict) or set(spec) != set(SPEC_FIELDS + LIST_FIELDS):
        raise ValueError("Invalid ResearchSpec fields")
    for name in SPEC_FIELDS:
        if not isinstance(spec[name], str) or not spec[name].strip():
            raise ValueError(f"ResearchSpec requires non-empty {name}")
    for name in LIST_FIELDS:
        if not isinstance(spec[name], list) or any(not isinstance(x, str) for x in spec[name]):
            raise ValueError(f"ResearchSpec requires string list: {name}")
    if spec["dataset"] != "mimiciv" or spec["analysis_unit"] not in {"patient", "admission", "icu_stay", "event"}:
        raise ValueError("Unsupported dataset or analysis unit")
    if spec["objective"] != "descriptive":
        raise ValueError("This release supports descriptive SQL research only")
    if spec["dataset_version"] != "3.1":
        raise ValueError("This release targets declared MIMIC-IV version 3.1")
    if not isinstance(value["sql"], str) or not isinstance(value["params_json"], str):
        raise ValueError("SQL and params_json must be strings")
    if not isinstance(value["limitations"], list) or any(not isinstance(x, str) for x in value["limitations"]):
        raise ValueError("limitations must be a string list")
    params = json.loads(value["params_json"], parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(params, dict) or any(not isinstance(k, str) for k in params):
        raise ValueError("params_json must encode an object")
    if any(isinstance(v, (dict, list)) for v in params.values()):
        raise ValueError("SQL parameters must be scalar values")
    if any(isinstance(v, float) and not math.isfinite(v) for v in params.values()):
        raise ValueError("SQL parameters must be finite")
    if value["status"] == "ready" and (not value["sql"].strip() or not spec["output_columns"]):
        raise ValueError("Ready proposals require SQL and output columns")
    if value["status"] != "ready" and value["sql"].strip():
        raise ValueError("Non-ready proposals must not contain executable SQL")
    digest(value)  # Also rejects non-finite numeric parameters in externally supplied JSON.
    return value
