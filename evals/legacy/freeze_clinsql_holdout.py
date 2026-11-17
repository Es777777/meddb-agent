"""Freeze a stratified validation holdout without opening SQL/results/rubrics."""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    root = Path(r"F:\database\vendor\ClinSQL-main\data\splits\validation")
    artifacts = Path(r"F:\database\benchmark\artifacts")
    out = artifacts / "clinsql_validation_reserved20_20261027.json"
    if out.exists():
        raise SystemExit("Holdout already frozen; refusing to change task selection")
    exposed = set()
    source_reports = [
        artifacts / "clinsql_model_generation_pilot_18_merged.json",
        artifacts / "clinsql_model_generation_validation_sample20_skill_v2.json",
    ]
    for path in source_reports:
        exposed.update(task["task_key"] for task in json.loads(path.read_text(encoding="utf-8"))["tasks"])
    groups = defaultdict(list)
    for path in sorted(root.glob("*/*_level_queries/*/query_*.txt")):
        relative = path.relative_to(root)
        domain, difficulty, task_id = relative.parts[:3]
        difficulty = difficulty.replace("_level_queries", "")
        key = f"validation/{domain}/{difficulty}/{task_id}"
        if key not in exposed:
            groups[(domain, difficulty)].append((key, path))
    rng = random.Random(20261027)
    strata = sorted(groups)
    rng.shuffle(strata)
    for group in strata:
        rng.shuffle(groups[group])
    selected = []
    while len(selected) < 20:
        for group in strata:
            if groups[group] and len(selected) < 20:
                key, path = groups[group].pop()
                selected.append({"task_key": key, "question_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    payload = {
        "purpose": "Reserved validation holdout; do not use for development",
        "seed": 20261027, "selection": "round-robin across seeded shuffled domain/difficulty strata",
        "not_read": ["Gold SQL", "results CSV", "rubrics", "test split"],
        "excluded_exposed_tasks": len(exposed),
        "exposure_boundary": "Known human/model-generation analyses only; Gold-port infrastructure already used all validation. This is not a pristine external test set.",
        "tasks": selected,
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "tasks": len(selected), "strata": len({tuple(item['task_key'].split('/')[1:3]) for item in selected}), "domains": dict(Counter(item['task_key'].split('/')[1] for item in selected)), "overlap_exposed": sum(item['task_key'] in exposed for item in selected)}))


if __name__ == "__main__":
    main()
