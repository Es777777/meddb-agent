"""Audit saved score flags only; never load clinical rows or regenerate SQL."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def summarize(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload["tasks"]
    targets = {}
    for target in ("official", "local"):
        executed = 0
        headers = 0
        numeric = 0
        values = 0
        alias_only = []
        for task in tasks:
            comparison = task.get("comparisons", {}).get(f"{target}_vs_reference", {})
            record = task[target]
            execution = record.get("execution", record)
            executed += execution.get("status") == "executed"
            headers += bool(comparison.get("columns_match"))
            numeric += bool(comparison.get("unordered_numeric_tolerant_match"))
            values += bool(comparison.get("unordered_numeric_tolerant_value_only_match"))
            if comparison.get("unordered_numeric_tolerant_value_only_match") and not comparison.get("columns_match"):
                alias_only.append(task["task_key"])
        targets[target] = {
            "executed": executed, "header_match": headers,
            "numeric_with_headers": numeric, "numeric_ignore_alias": values,
            "numeric_ignore_alias_pct": round(100 * values / len(tasks), 2),
            "alias_only_failures": alias_only,
        }
    paired = sum(bool(task.get("comparisons", {}).get("official_vs_local", {}).get("unordered_numeric_tolerant_match")) for task in tasks)
    repairs = {}
    for target in ("official", "local"):
        effective_correct = 0
        attempts = 0
        for task in tasks:
            comparison = task["comparisons"][f"{target}_vs_reference"]
            if "repair" in task:
                attempts += 1
                comparison = task["repair"]["comparisons"][f"{target}_vs_reference"]
            effective_correct += bool(comparison.get("unordered_numeric_tolerant_value_only_match"))
        repairs[target] = {"attempted_tasks": attempts, "effective_value_correct": effective_correct}
    return {
        "path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "tasks": len(tasks), "targets": targets, "paired_numeric": paired,
        "repair_track": repairs,
        "scope": payload.get("scope"),
        "task_keys": [task["task_key"] for task in tasks],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = [summarize(path) for path in args.reports]
    result = {"policy": "frozen stored flags only; original reports unchanged; value-only metric is supplementary, not official rubric score", "reports": reports}
    # Same-task subset comparison, never two different task-set denominators.
    if len(reports) >= 2:
        old = json.loads(args.reports[0].read_text(encoding="utf-8"))
        new = json.loads(args.reports[-1].read_text(encoding="utf-8"))
        old_by_key = {task["task_key"]: task for task in old["tasks"]}
        common = [task for task in new["tasks"] if task["task_key"] in old_by_key]
        result["same_task_comparison"] = {
            "tasks": len(common),
            "old_value_correct": sum(bool(old_by_key[task["task_key"]]["comparisons"]["official_vs_reference"].get("unordered_numeric_tolerant_value_only_match")) for task in common),
            "new_value_correct": sum(bool(task["comparisons"]["official_vs_reference"].get("unordered_numeric_tolerant_value_only_match")) for task in common),
            "limitation": "Development regression; CLI version/time and prompt/schema changed. Not a randomized causal claim.",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reports": [{"path": item["path"], "tasks": item["tasks"], "official": item["targets"]["official"]} for item in reports], "same_task_comparison": result.get("same_task_comparison")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
