"""Observable cohort flow and analysis-unit/data checks; no patient rows in logs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from medresearch.study_contract import analysis_columns


def select_cohort(frame: pd.DataFrame, criteria: list[dict]) -> tuple[pd.Series, list[dict]]:
    mask = pd.Series(True, index=frame.index)
    flow = [{"step": "source", "n": len(frame)}]
    for i, criterion in enumerate(criteria):
        c, op, value = criterion["column"], criterion["op"], criterion["value"]
        if c not in frame:
            raise ValueError("Cohort column is missing")
        column = frame[c]
        if op == "in":
            if not isinstance(value, list):
                raise ValueError("The in operator requires a list")
            included = column.isin(value)
        else:
            if isinstance(value, list):
                raise ValueError("Scalar cohort comparison cannot use a list")
            included = {"eq": column.eq, "ne": column.ne, "gt": column.gt,
                        "ge": column.ge, "lt": column.lt, "le": column.le}[op](value)
        mask &= included.fillna(False) & column.notna()
        flow.append({"step": f"criterion_{i+1}", "n": int(mask.sum())})
    return mask, flow


def check_quality(frame: pd.DataFrame, spec: dict, *, truncated: bool = False) -> dict:
    quality, analysis = spec["quality"], spec["analysis"]
    required = set(quality["keys"] + analysis_columns(analysis))
    required |= set(quality.get("ranges", {})) | set(quality.get("allowed_values", {}))
    missing_columns = sorted(required-set(frame.columns))
    issues, warnings = [], []
    if truncated:
        issues.append("truncated_source")
    if not frame.columns.is_unique:
        issues.append("duplicate_column_names")
    if missing_columns:
        return {"passed": False, "issues": issues+["missing_required_columns"],
                "missing_columns": missing_columns, "rows": len(frame), "warnings": warnings}
    if len(frame) < quality["min_rows"]:
        issues.append("insufficient_rows")
    if frame[quality["keys"]].isna().any().any():
        issues.append("missing_analysis_unit_key")
    duplicated = int(frame.duplicated(quality["keys"]).sum())
    if duplicated:
        issues.append("analysis_unit_cardinality_violation")
    missingness = {c: float(frame[c].isna().mean()) for c in sorted(required)}
    if any(v > quality["max_missing"] for v in missingness.values()):
        issues.append("missingness_threshold_exceeded")
    violations = {}
    for c, bounds in quality.get("ranges", {}).items():
        if not pd.api.types.is_numeric_dtype(frame[c]):
            issues.append("range_column_not_numeric")
            continue
        count = int((frame[c].notna() & ~frame[c].between(*bounds)).sum())
        if count:
            violations[c] = count
    if violations:
        issues.append("out_of_range_values")
    invalid_codes = {c: int((frame[c].notna() & ~frame[c].isin(allowed)).sum())
                     for c, allowed in quality.get("allowed_values", {}).items()}
    if any(invalid_codes.values()):
        issues.append("invalid_categorical_codes")
    if any(not np.isfinite(frame[c].dropna()).all() for c in required if pd.api.types.is_numeric_dtype(frame[c])):
        issues.append("nonfinite_values")
    if any(missingness.values()):
        warnings.append("missing_data_requires_strategy_review")
    return {"passed": not issues, "issues": sorted(set(issues)), "warnings": warnings,
            "rows": len(frame), "duplicate_units": duplicated, "missingness": missingness,
            "range_violations": violations, "invalid_code_counts": invalid_codes}
