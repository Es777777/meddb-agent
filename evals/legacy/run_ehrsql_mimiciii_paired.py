#!/usr/bin/env python3
"""Execute paired direct-port EHRSQL gold SQL through the read-only MedDB CLI."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_compare_helper(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("clinsql_compare", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load comparison helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preflight(meddb_root: Path, config: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["MEDDB_CODEX_PROFILE"] = "no-mcp"
    env["MEDDB_CODEX_SAFE"] = "1"
    completed = subprocess.run(
        [str(meddb_root / "bin" / "meddb.cmd"), "--config", str(config), "status"],
        cwd=meddb_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    combined = completed.stdout + "\n" + completed.stderr
    normalized = combined.lower()
    if completed.returncode != 0:
        raise RuntimeError(f"Status preflight failed for {config}: {combined[-2000:]}")
    if (
        "meddb_mimic_reader" not in normalized
        or not re.search(r"db_default_read_only\s*\|\s*on", normalized)
        or not re.search(r"mcp_disabled\s*\|\s*true", normalized)
    ):
        raise RuntimeError(f"Status preflight did not prove safe state for {config}")
    return {"config": str(config), "status_output_sha256": hashlib.sha256(combined.encode()).hexdigest()}


def execute(
    meddb_root: Path,
    config_path: Path,
    config: dict[str, Any],
    sql_path: Path,
    slug: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    env = os.environ.copy()
    env["MEDDB_CODEX_PROFILE"] = "no-mcp"
    env["MEDDB_CODEX_SAFE"] = "1"
    command = [
        str(meddb_root / "bin" / "meddb.cmd"), "--config", str(config_path), "query",
        "--sql-file", str(sql_path), "--detail", "--yes", "--format", "json", "--output", slug,
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command, cwd=meddb_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return ({"status": "query_process_timeout", "elapsed_ms": round((time.perf_counter() - started) * 1000)}, None)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    combined = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode != 0:
        return ({"status": "query_error", "elapsed_ms": elapsed_ms, "diagnostic": combined[-2000:]}, None)
    export_root = Path(config["paths"]["exports"])
    if not export_root.is_absolute():
        export_root = (meddb_root / export_root).resolve()
    output_path = (export_root / slug).resolve()
    if not output_path.exists():
        return ({"status": "missing_output", "elapsed_ms": elapsed_ms}, None)
    payload = load_json(output_path)
    record = {
        "status": "executed",
        "elapsed_ms": elapsed_ms,
        "db_elapsed_ms": payload.get("elapsed_ms"),
        "returned_rows": payload.get("returned_rows"),
        "truncated": payload.get("truncated"),
        "columns": payload.get("columns", []),
        "output_path": str(output_path),
        "output_sha256": sha256(output_path),
    }
    comparable = dict(record)
    comparable["rows"] = payload.get("rows", [])
    return record, comparable


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--explain-report", type=Path, required=True)
    parser.add_argument("--meddb-root", type=Path, required=True)
    parser.add_argument("--official-config", type=Path, required=True)
    parser.add_argument("--local-config", type=Path, required=True)
    parser.add_argument("--compare-helper", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()

    meddb_root = args.meddb_root.resolve()
    configs = {
        "official": (args.official_config.resolve(), load_json(args.official_config.resolve())),
        "local": (args.local_config.resolve(), load_json(args.local_config.resolve())),
    }
    preflight_results = {name: preflight(meddb_root, path) for name, (path, _) in configs.items()}
    source = load_json(args.explain_report)
    tasks = [task for task in source["tasks"] if task["paired_pass"]]
    compare = load_compare_helper(args.compare_helper.resolve())
    counts: Counter[str] = Counter()
    records = []
    started = time.perf_counter()
    for index, task in enumerate(tasks, start=1):
        actuals: dict[str, dict[str, Any] | None] = {}
        outcomes: dict[str, dict[str, Any]] = {}
        slug = f"ehrsql_mimiciii_{task['id']}.json"
        for target, (config_path, config) in configs.items():
            outcome, actual = execute(
                meddb_root, config_path, config, Path(task["sql_path"]), slug, args.timeout_seconds
            )
            outcomes[target] = outcome
            actuals[target] = actual
            counts[f"{target}:{outcome['status']}"] += 1
        if actuals["official"] is not None and actuals["local"] is not None:
            comparison = compare.compare_targets(actuals["official"], actuals["local"])
            counts["paired:comparable"] += 1
            if comparison.get("unordered_value_match"):
                counts["paired:strict_match"] += 1
            if comparison.get("unordered_numeric_tolerant_match"):
                counts["paired:numeric_tolerant_match"] += 1
            untruncated = not outcomes["official"].get("truncated") and not outcomes["local"].get("truncated")
            if untruncated:
                counts["paired:untruncated_comparable"] += 1
                if comparison.get("unordered_numeric_tolerant_match"):
                    counts["paired:untruncated_numeric_tolerant_match"] += 1
        else:
            comparison = {"comparable": False}
            untruncated = False
        records.append({
            "id": task["id"],
            "question": task["question"],
            "para_type": task.get("para_type"),
            "department": task.get("department"),
            "q_tag": task.get("q_tag"),
            "tables": task.get("tables", []),
            "sql_path": task["sql_path"],
            "sql_sha256": task["sql_sha256"],
            "official": outcomes["official"],
            "local": outcomes["local"],
            "comparison": comparison,
            "untruncated_pair": untruncated,
        })
        if index % 20 == 0 or index == len(tasks):
            print(
                f"progress={index}/{len(tasks)} comparable={counts['paired:comparable']} "
                f"numeric_match={counts['paired:numeric_tolerant_match']}",
                flush=True,
            )

    payload = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "EHRSQL v1.5.0 MIMIC-III validation direct PostgreSQL tasks passing paired EXPLAIN",
        "source": str(args.explain_report.resolve()),
        "source_sha256": sha256(args.explain_report),
        "guardrails": {
            "mcp_enabled": False,
            "database_role": "meddb_mimic_reader",
            "read_only": True,
            "detail_mode": True,
            "result_cap_rows": 100,
            "preflight": preflight_results,
        },
        "summary": {
            "tasks": len(tasks),
            "execution_units": len(tasks) * 2,
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
