#!/usr/bin/env python3
"""Merge disjoint CLINSQL model-generation batch reports with invariant checks."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def invariant(report: dict[str, Any]) -> tuple[Any, ...]:
    guard = report["guardrails"]
    return (
        guard.get("model"),
        guard.get("reasoning_effort"),
        guard.get("pairing_mode"),
        guard.get("schema_context_sha256"),
        guard.get("skill_entry_sha256"),
        guard.get("skill_protocol_sha256"),
        guard.get("database_role"),
        guard.get("gold_visible_to_model"),
        guard.get("mcp_enabled"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    paths = [path.resolve() for path in args.reports]
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    expected = invariant(reports[0])
    if any(invariant(report) != expected for report in reports[1:]):
        raise ValueError("Batch benchmark invariants differ")

    tasks: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    manifests = []
    wall_time = 0.0
    for path, report in zip(paths, reports, strict=True):
        manifests.append({"path": str(path), "sha256": sha256_file(path)})
        counts.update(report["summary"].get("counts", {}))
        wall_time += float(report["summary"].get("wall_time_seconds", 0))
        for task in report["tasks"]:
            key = task["task_key"]
            if key in tasks:
                raise ValueError(f"Duplicate task across batches: {key}")
            tasks[key] = task

    ordered_tasks = [tasks[key] for key in sorted(tasks)]
    output = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Merged CLINSQL validation model-generation batches",
        "source_batches": manifests,
        "guardrails": reports[0]["guardrails"],
        "summary": {
            "tasks": len(ordered_tasks),
            "generation_units": sum(int(report["summary"].get("generation_units", 0)) for report in reports),
            "counts": dict(sorted(counts.items())),
            "summed_wall_time_seconds": round(wall_time, 3),
        },
        "tasks": ordered_tasks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
