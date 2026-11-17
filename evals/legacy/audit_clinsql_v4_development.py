"""Collate stored score flags; distinguish resource deferral from a wrong answer.

No reference CSVs, task SQL/rubrics, clinical result rows or model calls are read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(tasks: list[dict]) -> dict:
    deferred = sum(t["official"]["generation"]["status"] == "resource_deferred" for t in tasks)
    result = {"selected": len(tasks), "resource_deferred": deferred,
              "model_attempted": len(tasks) - deferred, "targets": {}}
    for target in ("official", "local"):
        generated = sum(t[target]["generation"]["status"] == "generated" for t in tasks)
        executed = sum(t[target]["execution"]["status"] == "executed" for t in tasks)
        correct = sum(bool(t["comparisons"][f"{target}_vs_reference"].get("unordered_numeric_tolerant_value_only_match")) for t in tasks)
        aliases = sum(bool(t["comparisons"][f"{target}_vs_reference"].get("unordered_numeric_tolerant_match")) for t in tasks)
        result["targets"][target] = {
            "generated": generated, "executed": executed,
            "gold_value_correct": correct, "gold_with_alias_correct": aliases,
            "gold_accuracy_pct": round(100 * correct / len(tasks), 2) if tasks and not deferred else None,
            "coverage_incomplete": bool(deferred),
        }
    both = [t for t in tasks if all(t[x]["execution"]["status"] == "executed" for x in ("official", "local"))]
    result["paired_executed"] = len(both)
    result["paired_strict_equal"] = sum(bool(t["comparisons"]["official_vs_local"].get("unordered_value_match")) for t in both)
    result["paired_numeric_equal"] = sum(bool(t["comparisons"]["official_vs_local"].get("unordered_numeric_tolerant_match")) for t in both)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--baselines", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = json.loads(args.new.read_text(encoding="utf-8"))
    prior = {}
    for path in args.baselines:
        for task in json.loads(path.read_text(encoding="utf-8"))["tasks"]:
            key = task["task_key"]
            if key in prior:
                raise ValueError(f"Duplicate baseline ID; choose one prespecified baseline: {key}")
            prior[key] = (path, task)
    cases, baseline_tasks = [], []
    for task in current["tasks"]:
        key = task["task_key"]
        path, old = prior[key]
        baseline_tasks.append(old)
        cases.append({
            "task_key": key, "baseline_report": str(path),
            "baseline_gold_value_match": old["comparisons"]["official_vs_reference"].get("unordered_numeric_tolerant_value_only_match", False),
            "new_generation_status": task["official"]["generation"]["status"],
            "new_execution_status": task["official"]["execution"]["status"],
            "new_gold_value_match": task["comparisons"]["official_vs_reference"].get("unordered_numeric_tolerant_value_only_match", False) if task["official"]["generation"]["status"] != "resource_deferred" else None,
        })
    result = {
        "evaluation_status": "exposed-development-regression-not-heldout",
        "policy": "No attempted-model accuracy is inferred from resource-deferred tasks. All-task accuracy withheld if resource coverage incomplete. No official rubric score.",
        "baseline": metrics(baseline_tasks), "new": metrics(current["tasks"]), "cases": cases,
        "hashes": {str(path): digest(path) for path in [args.new, *args.baselines]},
        "new_wall_seconds": current["summary"]["wall_time_seconds"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
