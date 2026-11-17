#!/usr/bin/env python3
"""Recompute CLINSQL model-run comparisons from frozen exports without values."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("clinsql_compare", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def actual_from_record(record: dict[str, Any]) -> dict[str, Any]:
    execution = record["execution"]
    if execution.get("status") != "executed":
        return {"status": execution.get("status")}
    path = Path(execution["output_path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "status": "executed",
        "columns": payload.get("columns", []),
        "rows": payload.get("rows", []),
    }


def reference_path(validation_root: Path, key: str) -> Path:
    _, domain, difficulty, task_id = key.split("/")
    matches = list((validation_root / domain / f"{difficulty}_level_queries" / task_id).glob("result_*.csv"))
    if len(matches) != 1:
        raise ValueError(key)
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--compare-helper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.report.resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    helper = load_module(args.compare_helper.resolve())
    counts: Counter[str] = Counter()
    output_tasks = []
    for task in source["tasks"]:
        official = actual_from_record(task["official"])
        local = actual_from_record(task["local"])
        ref_path = reference_path(args.validation_root.resolve(), task["task_key"])
        ref_columns, ref_rows = helper.load_reference(ref_path)
        comparisons = {
            "official_vs_reference": helper.compare(ref_columns, ref_rows, official),
            "local_vs_reference": helper.compare(ref_columns, ref_rows, local),
            "official_vs_local": helper.compare_targets(official, local),
        }
        for target in ("official", "local"):
            comparison = comparisons[f"{target}_vs_reference"]
            for metric in (
                "unordered_value_match",
                "unordered_numeric_tolerant_match",
                "unordered_value_only_match",
                "unordered_numeric_tolerant_value_only_match",
            ):
                if comparison.get(metric):
                    counts[f"{target}:{metric}"] += 1
        for metric in (
            "unordered_value_match",
            "unordered_numeric_tolerant_match",
            "unordered_value_only_match",
            "unordered_numeric_tolerant_value_only_match",
        ):
            if comparisons["official_vs_local"].get(metric):
                counts[f"paired:{metric}"] += 1
        output_tasks.append({
            "task_key": task["task_key"],
            "comparisons": comparisons,
        })

    output = {
        "source_report": str(source_path),
        "source_report_sha256": sha256_file(source_path),
        "counts": dict(sorted(counts.items())),
        "tasks": output_tasks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
