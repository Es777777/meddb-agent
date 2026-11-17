"""Portable CLINSQL validation adapter. Scoring is never part of generation."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from medresearch.contracts import digest
from medresearch.runtime import ResearchRunner, atomic_json, generate_proposal, implementation_hash


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_validation(official: Path, limit: int) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be positive")
    official = official.resolve()
    manifest = json.loads((official / "MANIFEST.json").read_text(encoding="utf-8"))
    known = {item["path"]: item for item in manifest["files"]}
    root = official / "clinsql/data/splits/validation"
    paths = sorted(root.glob("*/*/*/query_*.txt"))
    if not paths:
        raise ValueError("No official CLINSQL validation questions found")
    # Round-robin by domain/difficulty, rather than filling the pilot with the
    # first lexical scenario. Do not inspect the held-out test directory.
    buckets: dict[tuple[str, str], list[Path]] = {}
    for path in paths:
        relative = path.relative_to(root)
        buckets.setdefault((relative.parts[0], relative.parts[1]), []).append(path)
    selected: list[Path] = []
    while buckets and len(selected) < limit:
        for key in list(buckets):
            selected.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
            if len(selected) == limit:
                break
    tasks = []
    for path in selected:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Question escaped validation root")
        relative = path.relative_to(official).as_posix()
        actual_hash = file_hash(path)
        if relative not in known or known[relative]["sha256"] != actual_hash:
            raise ValueError("Official question is missing from manifest or was modified")
        tasks.append({"task_id": "validation/" + path.parent.relative_to(root).as_posix(),
                      "question": path.read_text(encoding="utf-8").strip(),
                      "question_sha256": actual_hash, "question_path": relative})
    return tasks


def _cell_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        a, b = float(left), float(right)
        if not (math.isfinite(a) and math.isfinite(b)):
            return False
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-8)
    except (ValueError, TypeError, OverflowError):
        return str(left) == str(right)


def compare_results(columns: list[str], rows: list[list[Any]],
                    reference_columns: list[str], reference_rows: list[list[Any]],
                    *, truncated: bool = False) -> dict[str, Any]:
    """Unordered rows; positional columns; preserve duplicate multiplicity.

    Maximum bipartite matching handles tolerance overlaps without greedy false
    negatives. Empty CSV cells are NULL, never numeric zero. No column permutation.
    """
    matched = False
    if not truncated and len(columns) == len(reference_columns) and len(rows) == len(reference_rows):
        if all(len(row) == len(columns) for row in rows + reference_rows):
            edges = [[j for j, ref in enumerate(reference_rows)
                      if all(_cell_equal(a, b) for a, b in zip(row, ref))] for row in rows]
            assigned: dict[int, int] = {}
            reverse: dict[int, int] = {}

            def augment(index: int) -> bool:
                # Alternating-path search avoids recursion limits with duplicates.
                queue = [index]
                seen_left = {index}
                parent: dict[int, int] = {}
                for left in queue:
                    for target in edges[left]:
                        if target in parent:
                            continue
                        parent[target] = left
                        if target not in assigned:
                            while True:
                                source = parent[target]
                                previous = reverse.get(source)
                                assigned[target] = source
                                reverse[source] = target
                                if previous is None:
                                    return True
                                target = previous
                        other = assigned[target]
                        if other not in seen_left:
                            seen_left.add(other)
                            queue.append(other)
                return False

            matched = all(augment(i) for i in range(len(rows)))
    return {"value_match": matched, "alias_sensitive_match": matched and columns == reference_columns,
            "truncated": truncated, "row_order": "ignored", "column_order": "positional",
            "relative_tolerance": 1e-6, "absolute_tolerance": 1e-8}


def score_task(official: Path, task: dict[str, Any], result_path: Path) -> dict[str, Any]:
    # Called only after execution. No Gold SQL or rubric is ever loaded.
    question = official / task["question_path"]
    suffix = question.stem.removeprefix("query_")
    reference = question.with_name(f"result_{suffix}.csv")
    if not reference.resolve().is_relative_to((official / "clinsql/data/splits/validation").resolve()):
        raise ValueError("Reference escaped validation root")
    manifest = json.loads((official / "MANIFEST.json").read_text(encoding="utf-8"))
    expected = {item["path"]: item["sha256"] for item in manifest["files"]}
    if expected.get(reference.relative_to(official).as_posix()) != file_hash(reference):
        raise ValueError("Official reference integrity mismatch")
    with reference.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        columns = next(reader)
        rows = [[None if cell == "" else cell for cell in row] for row in reader]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    return dict(compare_results(result["columns"], result["rows"], columns, rows,
                                truncated=result["truncated"]), reference_sha256=file_hash(reference))


def run_benchmark(*, official: Path, output: Path, limit: int, generator: Any,
                  tool: Any, schema: str, rules: str, provenance: dict[str, Any],
                  resume: bool = False) -> dict[str, Any]:
    tasks = discover_validation(official, limit)
    frozen = {"tasks": tasks, "schema_sha256": digest(schema), "rules_sha256": digest(rules),
              "provenance": provenance, "implementation_sha256": implementation_hash(),
              "source_manifest_sha256": file_hash(official / "MANIFEST.json")}
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if not resume or json.loads(manifest_path.read_text(encoding="utf-8")) != frozen:
            raise ValueError("Benchmark exists or frozen inputs changed; use a new output directory")
    elif output.exists() and any(output.iterdir()):
        raise ValueError("Benchmark output must be empty")
    atomic_json(manifest_path, frozen)
    records = []
    for task in tasks:
        target = output / "tasks" / task["task_id"]
        generated = target / "generation.json"
        record: dict[str, Any] = {"task_id": task["task_id"]}
        try:
            if generated.exists() and resume:
                envelope = json.loads(generated.read_text(encoding="utf-8"))
                proposal = envelope["proposal"]
                if digest(proposal) != envelope["sha256"]:
                    raise ValueError("Frozen generation modified")
            else:
                proposal = generate_proposal(generator, question=task["question"],
                    task_id=task["task_id"], dataset_version="3.1", schema=schema, rules=rules)
                atomic_json(generated, {"proposal": proposal, "sha256": digest(proposal)})
            record["generated"] = True
            state = ResearchRunner(tool, target / "execution", frozen).run(proposal, resume=resume)
            record["status"] = state["status"]
            record["executed"] = state["status"] == "completed"
            if record["executed"]:
                record["comparison"] = score_task(official, task, target / "execution/result.json")
        except Exception as exc:
            record.update(status="error", error_type=type(exc).__name__)
        records.append(record)
        atomic_json(output / "records.json", records)
    denominator = len(tasks)
    generated_count = sum(bool(x.get("generated")) for x in records)
    executed_count = sum(bool(x.get("executed")) for x in records)
    values = sum(x.get("comparison", {}).get("value_match", False) for x in records)
    aliases = sum(x.get("comparison", {}).get("alias_sensitive_match", False) for x in records)
    summary = {"benchmark": "CLINSQL validation", "split": "validation", "selected": denominator,
               "generated": generated_count, "executed": executed_count,
               "value_matches": values, "alias_sensitive_matches": aliases,
               "generation_rate": generated_count / denominator,
               "execution_rate": executed_count / denominator,
               "value_match_rate": values / denominator,
               "alias_sensitive_match_rate": aliases / denominator,
               "paired_database_agreement": None, "official_rubric_score": None,
               "limitations": ["Development validation, not held-out evaluation",
                   "PostgreSQL execution against upstream BigQuery reference CSV",
                   "Dataset release is declared, not independently verified",
                   "Conservative aggregate policy can reject valid benchmark questions",
                   "No Gold SQL, rubric scoring, result-guided repair or paired execution"]}
    atomic_json(output / "summary.json", summary)
    return summary
