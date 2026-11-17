from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def canonical_cell(value: Any) -> tuple[str, str]:
    if value is None or value == "":
        return ("null", "")
    if isinstance(value, bool):
        return ("bool", "true" if value else "false")
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return ("bool", text.lower())
    if NUMBER_RE.fullmatch(text):
        try:
            number = Decimal(text)
            normalized = number.normalize()
            return ("number", format(normalized, "f"))
        except InvalidOperation:
            pass
    if text.startswith(("[", "{")):
        try:
            nested = json.loads(text)
            return ("json", json.dumps(nested, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        except json.JSONDecodeError:
            pass
    return ("text", text)


def canonical_rows(columns: list[str], rows: list[dict[str, Any]]) -> list[list[tuple[str, str]]]:
    return [[canonical_cell(row.get(column)) for column in columns] for row in rows]


def rows_hash(rows: list[list[tuple[str, str]]], *, ordered: bool) -> str:
    values = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows]
    if not ordered:
        values.sort()
    return sha256_bytes(("\n".join(values) + "\n").encode("utf-8"))


def cells_tolerantly_equal(left: tuple[str, str], right: tuple[str, str]) -> bool:
    if left[0] == right[0] == "number":
        left_number = Decimal(left[1])
        right_number = Decimal(right[1])
        tolerance = Decimal("1e-9") * max(Decimal(1), abs(left_number), abs(right_number))
        return abs(left_number - right_number) <= tolerance
    return left == right


def row_tolerantly_equal(left: list[tuple[str, str]], right: list[tuple[str, str]]) -> bool:
    return len(left) == len(right) and all(
        cells_tolerantly_equal(left_cell, right_cell)
        for left_cell, right_cell in zip(left, right, strict=True)
    )


def rows_tolerantly_equal(
    left: list[list[tuple[str, str]]],
    right: list[list[tuple[str, str]]],
    *,
    ordered: bool,
) -> bool:
    if len(left) != len(right):
        return False
    if ordered:
        return all(row_tolerantly_equal(a, b) for a, b in zip(left, right, strict=True))
    unmatched = list(right)
    for left_row in left:
        for index, right_row in enumerate(unmatched):
            if row_tolerantly_equal(left_row, right_row):
                unmatched.pop(index)
                break
        else:
            return False
    return True


def load_reference(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        return columns, list(reader)


def task_key(sql_file: Path, sql_root: Path) -> str:
    rel = sql_file.relative_to(sql_root)
    return "validation/" + rel.parent.as_posix().replace("_level_queries", "")


def select_pilot(sql_root: Path) -> list[Path]:
    selected = []
    for domain in sorted(path for path in sql_root.iterdir() if path.is_dir()):
        for difficulty in sorted(path for path in domain.iterdir() if path.is_dir()):
            task_dirs = sorted(path for path in difficulty.iterdir() if path.is_dir())
            if not task_dirs:
                raise ValueError(f"No tasks in stratum: {difficulty}")
            matches = sorted(task_dirs[0].glob("sql_*.sql"))
            if len(matches) != 1:
                raise ValueError(f"Expected one SQL file in {task_dirs[0]}")
            selected.append(matches[0])
    if len(selected) != 18:
        raise ValueError(f"Expected 18 pilot tasks (6 domains x 3 difficulty strata), found {len(selected)}")
    return selected


def select_explain_pass(sql_root: Path, explain_report: Path) -> list[Path]:
    report = load_json(explain_report.resolve())
    passed = {
        item["task_key"]
        for item in report["tasks"]
        if item.get("status") == "explain_pass"
    }
    selected = [
        path
        for path in sorted(sql_root.rglob("sql_*.sql"))
        if task_key(path, sql_root) in passed
    ]
    if len(selected) != len(passed):
        raise ValueError(
            f"EXPLAIN report has {len(passed)} passed task keys but {len(selected)} SQL files were resolved"
        )
    return selected


def locate_reference(sql_file: Path, sql_root: Path, source_root: Path) -> Path:
    rel = sql_file.relative_to(sql_root)
    task_dir = source_root / rel.parent
    matches = sorted(task_dir.glob("result_*.csv"))
    if len(matches) != 1:
        raise ValueError(f"Expected one reference result in {task_dir}")
    return matches[0]


def run_target(
    *,
    cli: Path,
    meddb_root: Path,
    config_path: Path,
    export_root: Path,
    sql_file: Path,
    output_name: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [
                str(cli),
                "--config", str(config_path),
                "query",
                "--sql-file", str(sql_file),
                "--yes",
                "--format", "json",
                "--output", output_name,
            ],
            cwd=meddb_root,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=None,
            errors="replace",
            timeout=40,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "process_timeout",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "diagnostic": "Runner timeout after 40 seconds",
        }
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    combined = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode != 0:
        return {
            "status": "cli_error",
            "elapsed_ms": elapsed_ms,
            "diagnostic": combined[-2000:],
        }
    output_path = (export_root / output_name).resolve()
    if not output_path.exists():
        return {
            "status": "missing_output",
            "elapsed_ms": elapsed_ms,
            "diagnostic": combined[-2000:],
        }
    result = load_json(output_path)
    return {
        "status": "executed",
        "elapsed_ms": elapsed_ms,
        "db_elapsed_ms": result.get("elapsed_ms"),
        "returned_rows": result.get("returned_rows"),
        "truncated": result.get("truncated"),
        "columns": result.get("columns", []),
        "rows": result.get("rows", []),
        "output_path": str(output_path),
        "output_sha256": sha256_file(output_path),
    }


def compare(reference_columns: list[str], reference_rows: list[dict[str, Any]], actual: dict[str, Any]) -> dict[str, Any]:
    if actual.get("truncated"):
        return {"comparable": False, "reason": "truncated_output", "unordered_value_match": False}
    if actual.get("status") != "executed":
        return {
            "comparable": False,
            "columns_match": False,
            "row_count_match": False,
            "ordered_value_match": False,
            "unordered_value_match": False,
        }
    actual_columns = list(actual["columns"])
    actual_rows = list(actual["rows"])
    reference_canonical = canonical_rows(reference_columns, reference_rows)
    actual_canonical = canonical_rows(actual_columns, actual_rows)
    columns_match = reference_columns == actual_columns
    column_count_match = len(reference_columns) == len(actual_columns)
    row_count_match = len(reference_rows) == len(actual_rows)
    if columns_match:
        reference_ordered_hash = rows_hash(reference_canonical, ordered=True)
        actual_ordered_hash = rows_hash(actual_canonical, ordered=True)
        reference_unordered_hash = rows_hash(reference_canonical, ordered=False)
        actual_unordered_hash = rows_hash(actual_canonical, ordered=False)
    else:
        reference_ordered_hash = rows_hash(reference_canonical, ordered=True)
        actual_ordered_hash = rows_hash(actual_canonical, ordered=True)
        reference_unordered_hash = rows_hash(reference_canonical, ordered=False)
        actual_unordered_hash = rows_hash(actual_canonical, ordered=False)
    return {
        "comparable": True,
        "columns_match": columns_match,
        "column_count_match": column_count_match,
        "row_count_match": row_count_match,
        "ordered_value_match": columns_match and row_count_match and reference_ordered_hash == actual_ordered_hash,
        "unordered_value_match": columns_match and row_count_match and reference_unordered_hash == actual_unordered_hash,
        "ordered_numeric_tolerant_match": columns_match and row_count_match and rows_tolerantly_equal(reference_canonical, actual_canonical, ordered=True),
        "unordered_numeric_tolerant_match": columns_match and row_count_match and rows_tolerantly_equal(reference_canonical, actual_canonical, ordered=False),
        "ordered_value_only_match": column_count_match and row_count_match and reference_ordered_hash == actual_ordered_hash,
        "unordered_value_only_match": column_count_match and row_count_match and reference_unordered_hash == actual_unordered_hash,
        "ordered_numeric_tolerant_value_only_match": column_count_match and row_count_match and rows_tolerantly_equal(reference_canonical, actual_canonical, ordered=True),
        "unordered_numeric_tolerant_value_only_match": column_count_match and row_count_match and rows_tolerantly_equal(reference_canonical, actual_canonical, ordered=False),
        "reference_ordered_hash": reference_ordered_hash,
        "actual_ordered_hash": actual_ordered_hash,
        "reference_unordered_hash": reference_unordered_hash,
        "actual_unordered_hash": actual_unordered_hash,
    }


def compare_targets(official: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    if official.get("truncated") or local.get("truncated"):
        return {"comparable": False, "reason": "truncated_output", "unordered_value_match": False}
    if official.get("status") != "executed" or local.get("status") != "executed":
        return {"comparable": False, "ordered_value_match": False, "unordered_value_match": False}
    official_columns = list(official["columns"])
    local_columns = list(local["columns"])
    official_rows = canonical_rows(official_columns, list(official["rows"]))
    local_rows = canonical_rows(local_columns, list(local["rows"]))
    columns_match = official_columns == local_columns
    column_count_match = len(official_columns) == len(local_columns)
    row_count_match = len(official_rows) == len(local_rows)
    return {
        "comparable": True,
        "columns_match": columns_match,
        "column_count_match": column_count_match,
        "row_count_match": row_count_match,
        "ordered_value_match": columns_match and row_count_match and rows_hash(official_rows, ordered=True) == rows_hash(local_rows, ordered=True),
        "unordered_value_match": columns_match and row_count_match and rows_hash(official_rows, ordered=False) == rows_hash(local_rows, ordered=False),
        "ordered_numeric_tolerant_match": columns_match and row_count_match and rows_tolerantly_equal(official_rows, local_rows, ordered=True),
        "unordered_numeric_tolerant_match": columns_match and row_count_match and rows_tolerantly_equal(official_rows, local_rows, ordered=False),
        "ordered_value_only_match": column_count_match and row_count_match and rows_hash(official_rows, ordered=True) == rows_hash(local_rows, ordered=True),
        "unordered_value_only_match": column_count_match and row_count_match and rows_hash(official_rows, ordered=False) == rows_hash(local_rows, ordered=False),
        "ordered_numeric_tolerant_value_only_match": column_count_match and row_count_match and rows_tolerantly_equal(official_rows, local_rows, ordered=True),
        "unordered_numeric_tolerant_value_only_match": column_count_match and row_count_match and rows_tolerantly_equal(official_rows, local_rows, ordered=False),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a frozen CLINSQL validation selection through MedDB CLI.")
    parser.add_argument("--sql-root", type=Path, required=True)
    parser.add_argument("--source-validation-root", type=Path, required=True)
    parser.add_argument("--meddb-root", type=Path, required=True)
    parser.add_argument("--official-config", type=Path, required=True)
    parser.add_argument("--local-config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--selection", choices=("pilot", "all-explain-pass"), default="pilot")
    parser.add_argument("--explain-report", type=Path)
    args = parser.parse_args()

    if os.environ.get("MEDDB_CODEX_PROFILE") != "no-mcp" or os.environ.get("MEDDB_CODEX_SAFE") != "1":
        raise RuntimeError("Refusing to run without the no-mcp safe environment")
    sql_root = args.sql_root.resolve()
    source_root = args.source_validation_root.resolve()
    if sql_root.name.lower() != "validation" or source_root.name.lower() != "validation":
        raise ValueError("This runner intentionally accepts only validation split roots")

    official_config = load_json(args.official_config.resolve())
    local_config = load_json(args.local_config.resolve())
    official_export_root = Path(official_config["paths"]["exports"]).resolve()
    local_export_root = Path(local_config["paths"]["exports"]).resolve()
    cli = args.meddb_root.resolve() / "bin" / "meddb.cmd"
    if args.selection == "pilot":
        selected = select_pilot(sql_root)
        selection_rule = (
            "For each of 6 domain x 3 difficulty strata, choose the lexicographically "
            "smallest task directory before execution."
        )
    else:
        if args.explain_report is None:
            raise ValueError("--explain-report is required for all-explain-pass selection")
        selected = select_explain_pass(sql_root, args.explain_report)
        selection_rule = "All validation tasks with explain_pass in the frozen EXPLAIN gate report."
    selection_manifest = [
        {
            "task_key": task_key(path, sql_root),
            "sql_relative_path": path.relative_to(sql_root).as_posix(),
            "sql_sha256": sha256_file(path),
        }
        for path in selected
    ]
    selection_hash = sha256_bytes(
        json.dumps(selection_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )

    records = []
    counts: Counter[str] = Counter()
    wall_started = time.perf_counter()
    for index, sql_file in enumerate(selected, start=1):
        key = task_key(sql_file, sql_root)
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", key) + ".json"
        reference_path = locate_reference(sql_file, sql_root, source_root)
        reference_columns, reference_rows = load_reference(reference_path)
        official = run_target(
            cli=cli,
            meddb_root=args.meddb_root.resolve(),
            config_path=args.official_config.resolve(),
            export_root=official_export_root,
            sql_file=sql_file,
            output_name=slug,
        )
        local = run_target(
            cli=cli,
            meddb_root=args.meddb_root.resolve(),
            config_path=args.local_config.resolve(),
            export_root=local_export_root,
            sql_file=sql_file,
            output_name=slug,
        )
        official_comparison = compare(reference_columns, reference_rows, official)
        local_comparison = compare(reference_columns, reference_rows, local)
        paired = compare_targets(official, local)
        counts[f"official:{official['status']}"] += 1
        counts[f"local:{local['status']}"] += 1
        if official_comparison["unordered_value_match"]:
            counts["official:reference_strict_match"] += 1
        if official_comparison.get("unordered_numeric_tolerant_match"):
            counts["official:reference_numeric_tolerant_match"] += 1
        if local_comparison["unordered_value_match"]:
            counts["local:reference_strict_match"] += 1
        if local_comparison.get("unordered_numeric_tolerant_match"):
            counts["local:reference_numeric_tolerant_match"] += 1
        if paired["unordered_value_match"]:
            counts["paired:strict_match"] += 1
        if paired.get("unordered_numeric_tolerant_match"):
            counts["paired:numeric_tolerant_match"] += 1
        # Store no result rows in the report; raw aggregate outputs remain in
        # the controlled export directories and are referenced by hash/path.
        official.pop("rows", None)
        local.pop("rows", None)
        records.append({
            "task_key": key,
            "sql_relative_path": sql_file.relative_to(sql_root).as_posix(),
            "reference_relative_path": reference_path.relative_to(source_root).as_posix(),
            "reference_sha256": sha256_file(reference_path),
            "reference_rows": len(reference_rows),
            "official": official,
            "local": local,
            "official_vs_reference": official_comparison,
            "local_vs_reference": local_comparison,
            "official_vs_local": paired,
        })
        print(
            f"progress={index}/{len(selected)} task={key} official={official['status']} local={local['status']} "
            f"O_ref={official_comparison['unordered_value_match']} L_ref={local_comparison['unordered_value_match']} "
            f"O_L={paired['unordered_value_match']}",
            flush=True,
        )

    payload = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "CLINSQL PostgreSQL port validation pilot"
            if args.selection == "pilot"
            else "CLINSQL PostgreSQL port validation full EXPLAIN-pass set"
        ),
        "selection_rule": selection_rule,
        "selection_manifest_sha256": selection_hash,
        "selection_manifest": selection_manifest,
        "guardrails": {
            "test_split_opened": False,
            "task_specific_sql_repairs": 0,
            "meddb_codex_profile": os.environ.get("MEDDB_CODEX_PROFILE"),
            "meddb_codex_safe": os.environ.get("MEDDB_CODEX_SAFE"),
            "database_role": "meddb_mimic_reader",
        },
        "summary": {
            "total_tasks": len(records),
            "execution_units": len(records) * 2,
            "counts": dict(sorted(counts.items())),
            "wall_time_seconds": round(time.perf_counter() - wall_started, 3),
        },
        "tasks": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
