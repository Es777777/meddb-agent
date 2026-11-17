#!/usr/bin/env python3
"""Run paired MedDB CLI EXPLAIN gates for direct-port EHRSQL MIMIC-III SQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ERROR_PATTERNS = (
    ("missing_function", re.compile(r"function .* does not exist|undefinedfunction", re.I)),
    ("missing_column", re.compile(r"column .* does not exist|undefinedcolumn", re.I)),
    ("missing_relation", re.compile(r"relation .* does not exist|undefinedtable", re.I)),
    ("type_or_operator", re.compile(r"operator does not exist|datatype mismatch|cannot cast|invalid input syntax", re.I)),
    ("syntax", re.compile(r"syntax error|sql.*parse", re.I)),
    ("security_policy", re.compile(r"禁止|只允许|schema.*白名单|requires an explicitly allowed", re.I)),
    ("plan_cost", re.compile(r"plan.*cost|成本", re.I)),
    ("timeout", re.compile(r"timeout|超时|canceling statement", re.I)),
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(text: str) -> str:
    for label, pattern in ERROR_PATTERNS:
        if pattern.search(text):
            return label
    return "other"


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


def explain(meddb_root: Path, config: Path, sql_path: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["MEDDB_CODEX_PROFILE"] = "no-mcp"
    env["MEDDB_CODEX_SAFE"] = "1"
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [str(meddb_root / "bin" / "meddb.cmd"), "--config", str(config), "query",
             "--sql-file", str(sql_path), "--detail", "--yes", "--format", "json", "--explain-only"],
            cwd=meddb_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=45, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "explain_timeout", "error_category": "timeout", "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    combined = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode == 0:
        return {"status": "explain_pass", "error_category": None, "elapsed_ms": elapsed_ms}
    return {
        "status": "explain_fail",
        "error_category": classify(combined),
        "elapsed_ms": elapsed_ms,
        "diagnostic": combined[-2000:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portability", type=Path, required=True)
    parser.add_argument("--meddb-root", type=Path, required=True)
    parser.add_argument("--official-config", type=Path, required=True)
    parser.add_argument("--local-config", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    meddb_root = args.meddb_root.resolve()
    run_root = args.run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    configs = {
        "official": args.official_config.resolve(),
        "local": args.local_config.resolve(),
    }
    preflight_results = {name: preflight(meddb_root, config) for name, config in configs.items()}
    source = load_json(args.portability)
    candidates = [
        record for record in source["records"]
        if record.get("classification") == "direct_postgres_candidate"
    ]
    if len(candidates) != source["summary"]["direct_postgres_candidates"]:
        raise ValueError("Candidate count does not match portability summary")

    counts: Counter[str] = Counter()
    records = []
    started = time.perf_counter()
    for index, candidate in enumerate(candidates, start=1):
        sql_path = run_root / f"{candidate['id']}.sql"
        sql_path.write_text(candidate["postgres_sql"] + "\n", encoding="utf-8")
        outcomes = {name: explain(meddb_root, config, sql_path) for name, config in configs.items()}
        for name, outcome in outcomes.items():
            counts[f"{name}:{outcome['status']}"] += 1
            if outcome.get("error_category"):
                counts[f"{name}:error:{outcome['error_category']}"] += 1
        paired_pass = all(outcome["status"] == "explain_pass" for outcome in outcomes.values())
        counts["paired:pass" if paired_pass else "paired:not_pass"] += 1
        records.append({
            "id": candidate["id"],
            "question": candidate["question"],
            "para_type": candidate.get("para_type"),
            "department": candidate.get("department"),
            "q_tag": candidate.get("q_tag"),
            "tables": candidate.get("tables", []),
            "sql_path": str(sql_path),
            "sql_sha256": sha256(sql_path),
            "official": outcomes["official"],
            "local": outcomes["local"],
            "paired_pass": paired_pass,
        })
        if index % 25 == 0 or index == len(candidates):
            print(f"progress={index}/{len(candidates)} paired_pass={counts['paired:pass']}", flush=True)

    payload = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "EHRSQL v1.5.0 MIMIC-III validation direct PostgreSQL candidates; paired EXPLAIN only",
        "source": str(args.portability.resolve()),
        "source_sha256": sha256(args.portability),
        "guardrails": {
            "mcp_enabled": False,
            "database_role": "meddb_mimic_reader",
            "read_only": True,
            "queries_executed": False,
            "explain_only": True,
            "detail_mode": True,
            "detail_mode_reason": "Published EHRSQL includes row/detail lookup questions; CLI output remains capped.",
            "preflight": preflight_results,
        },
        "summary": {
            "candidates": len(candidates),
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
