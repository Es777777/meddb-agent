from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from run_clinsql_validation_pilot import compare, compare_targets, load_json, load_reference


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute CLINSQL pilot comparisons without rerunning database queries.")
    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--source-validation-root", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()

    payload = load_json(args.input_report.resolve())
    source_root = args.source_validation_root.resolve()
    counts: Counter[str] = Counter()
    for task in payload["tasks"]:
        reference_path = source_root / task["reference_relative_path"]
        reference_columns, reference_rows = load_reference(reference_path)
        loaded_targets = {}
        for target_name in ("official", "local"):
            target = task[target_name]
            if target.get("status") == "executed":
                result = load_json(Path(target["output_path"]))
                loaded_targets[target_name] = {
                    **target,
                    "columns": result.get("columns", []),
                    "rows": result.get("rows", []),
                }
            else:
                loaded_targets[target_name] = dict(target)
        task["official_vs_reference"] = compare(reference_columns, reference_rows, loaded_targets["official"])
        task["local_vs_reference"] = compare(reference_columns, reference_rows, loaded_targets["local"])
        task["official_vs_local"] = compare_targets(loaded_targets["official"], loaded_targets["local"])
        for target_name in ("official", "local"):
            counts[f"{target_name}:{task[target_name]['status']}"] += 1
            comparison = task[f"{target_name}_vs_reference"]
            if comparison.get("unordered_value_match"):
                counts[f"{target_name}:reference_strict_match"] += 1
            if comparison.get("unordered_numeric_tolerant_match"):
                counts[f"{target_name}:reference_numeric_tolerant_match"] += 1
        paired = task["official_vs_local"]
        if paired.get("unordered_value_match"):
            counts["paired:strict_match"] += 1
        if paired.get("unordered_numeric_tolerant_match"):
            counts["paired:numeric_tolerant_match"] += 1

    payload["reanalyzed_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["comparison_policy"] = {
        "strict": "Canonical type/value equality after decimal normalization; row-order-free metric uses a multiset.",
        "numeric_tolerant": "Absolute difference <= 1e-9 * max(1, abs(reference), abs(actual)); all nonnumeric cells exact.",
    }
    payload["summary"]["counts"] = dict(sorted(counts.items()))
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
