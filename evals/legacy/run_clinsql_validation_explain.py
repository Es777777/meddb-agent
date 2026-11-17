from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ERROR_PATTERNS = (
    ("missing_function", re.compile(r"function .* does not exist|undefinedfunction", re.I)),
    ("missing_column", re.compile(r"column .* does not exist|undefinedcolumn", re.I)),
    ("missing_relation", re.compile(r"relation .* does not exist|undefinedtable", re.I)),
    ("type_or_operator", re.compile(r"operator does not exist|datatype mismatch|cannot cast|invalid input syntax", re.I)),
    ("syntax", re.compile(r"syntax error|sql.*parse|SQL.*解析", re.I)),
    ("security_policy", re.compile(r"禁止|只允许|schema.*白名单|requires an explicitly allowed", re.I)),
    ("plan_cost", re.compile(r"plan.*cost|成本", re.I)),
    ("timeout", re.compile(r"timeout|超时|canceling statement", re.I)),
)


def classify(text: str) -> str:
    for label, pattern in ERROR_PATTERNS:
        if pattern.search(text):
            return label
    return "other"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MedDB CLI EXPLAIN validation on converted CLINSQL validation SQL.")
    parser.add_argument("--sql-root", type=Path, required=True)
    parser.add_argument("--meddb-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    if os.environ.get("MEDDB_CODEX_PROFILE") != "no-mcp" or os.environ.get("MEDDB_CODEX_SAFE") != "1":
        raise RuntimeError("Refusing to run without MEDDB_CODEX_PROFILE=no-mcp and MEDDB_CODEX_SAFE=1")

    sql_root = args.sql_root.resolve()
    if sql_root.name.lower() != "validation":
        raise ValueError("This runner intentionally accepts only the validation split")
    sql_files = sorted(sql_root.rglob("sql_*.sql"))
    if len(sql_files) != 190:
        raise ValueError(f"Expected 190 converted validation SQL files, found {len(sql_files)}")

    cli = args.meddb_root.resolve() / "bin" / "meddb.cmd"
    base = [str(cli), "--config", str(args.config.resolve()), "query"]
    records = []
    counts: Counter[str] = Counter()
    started = time.perf_counter()
    for index, sql_file in enumerate(sql_files, start=1):
        task_started = time.perf_counter()
        completed = subprocess.run(
            base + [
                "--sql-file", str(sql_file),
                "--yes",
                "--format", "json",
                "--explain-only",
            ],
            cwd=args.meddb_root.resolve(),
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=None,
            errors="replace",
            timeout=45,
            check=False,
        )
        elapsed_ms = round((time.perf_counter() - task_started) * 1000)
        combined = (completed.stdout + "\n" + completed.stderr).strip()
        if completed.returncode == 0:
            status = "explain_pass"
            category = None
            diagnostic = None
        else:
            status = "explain_fail"
            category = classify(combined)
            diagnostic = combined[-2000:]
        counts[status] += 1
        if category:
            counts[f"error:{category}"] += 1
        records.append({
            "task_key": "validation/" + sql_file.relative_to(sql_root).parent.as_posix().replace("_level_queries", ""),
            "sql_relative_path": sql_file.relative_to(sql_root).as_posix(),
            "status": status,
            "error_category": category,
            "elapsed_ms": elapsed_ms,
            "diagnostic": diagnostic,
        })
        if index % 25 == 0 or index == len(sql_files):
            print(f"progress={index}/{len(sql_files)} pass={counts['explain_pass']} fail={counts['explain_fail']}", flush=True)

    payload = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "CLINSQL PostgreSQL port validation split; EXPLAIN only",
        "database_config": str(args.config.resolve()),
        "cli": str(cli),
        "guardrails": {
            "meddb_codex_profile": os.environ.get("MEDDB_CODEX_PROFILE"),
            "meddb_codex_safe": os.environ.get("MEDDB_CODEX_SAFE"),
            "test_split_opened": False,
            "queries_executed": False,
            "explain_only": True,
        },
        "summary": {
            "total": len(records),
            "counts": dict(sorted(counts.items())),
            "wall_time_seconds": round(time.perf_counter() - started, 3),
        },
        "tasks": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
