#!/usr/bin/env python3
"""Materialize MedDB model runs in CLINSQL's official scorer directory layout."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def task_dir(validation_root: Path, key: str) -> tuple[Path, str, str, str]:
    split, domain, difficulty, task_id = key.split("/")
    if split != "validation":
        raise ValueError("This exporter is validation-only")
    difficulty_dir = f"{difficulty}_level_queries"
    return validation_root / domain / difficulty_dir / task_id, domain, difficulty_dir, task_id


def first(path: Path, pattern: str) -> Path:
    matches = list(path.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} in {path}")
    return matches[0]


def write_result_csv(export_json: Path, destination: Path) -> dict[str, Any]:
    payload = json.loads(export_json.read_text(encoding="utf-8"))
    columns = list(payload.get("columns", []))
    rows = list(payload.get("rows", []))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return {
        "rows": len(rows),
        "columns": len(columns),
        "source_export_sha256": sha256_file(export_json),
        "result_csv_sha256": sha256_file(destination),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--inference-root", type=Path, required=True)
    parser.add_argument("--model-prefix", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    report_path = args.report.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest: dict[str, Any] = {
        "source_report": str(report_path),
        "source_report_sha256": sha256_file(report_path),
        "models": {},
    }
    for target in ("official", "local"):
        model_name = f"{args.model_prefix}-{target}"
        model_records = []
        for task in report["tasks"]:
            source_dir, domain, difficulty_dir, task_id = task_dir(
                args.validation_root.resolve(), task["task_key"]
            )
            destination = (
                args.inference_root.resolve()
                / model_name
                / "validation"
                / domain
                / difficulty_dir
                / task_id
            )
            destination.mkdir(parents=True, exist_ok=True)
            query_source = first(source_dir, "query_*.txt")
            shutil.copy2(query_source, destination / f"query_{task_id}.txt")
            shutil.copy2(source_dir / "sql_rubric_tree.json", destination / "sql_rubric_tree.json")
            shutil.copy2(source_dir / "results_rubric_tree.json", destination / "results_rubric_tree.json")

            generated = task[target]["generation"]
            execution = task[target]["execution"]
            record: dict[str, Any] = {
                "task_key": task["task_key"],
                "generation_status": generated.get("status"),
                "execution_status": execution.get("status"),
            }
            if generated.get("sql_path"):
                sql_source = Path(generated["sql_path"])
                sql_destination = destination / f"sql_{task_id}.sql"
                shutil.copy2(sql_source, sql_destination)
                record["sql_sha256"] = sha256_file(sql_destination)
            if execution.get("status") == "executed":
                record["result"] = write_result_csv(
                    Path(execution["output_path"]), destination / f"result_{task_id}.csv"
                )
            (destination / "execution_summary.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            model_records.append(record)
        manifest["models"][model_name] = model_records

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "models": list(manifest["models"]),
        "samples_per_model": len(report["tasks"]),
        "manifest_sha256": sha256_file(args.manifest),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
