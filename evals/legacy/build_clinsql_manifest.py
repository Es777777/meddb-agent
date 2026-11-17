from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_GLOBS = {
    "query": "query_*.txt",
    "sql": "sql_*.sql",
    "result": "result_*.csv",
}
REQUIRED_FIXED = ("sql_rubric_tree.json", "results_rubric_tree.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def task_files(task_dir: Path) -> dict[str, Path]:
    files = {path.name: path for path in task_dir.iterdir() if path.is_file()}
    selected: dict[str, Path] = {}
    for role, pattern in REQUIRED_GLOBS.items():
        matches = sorted(task_dir.glob(pattern))
        if len(matches) != 1:
            raise ValueError(f"{task_dir}: expected exactly one {pattern} file, found {len(matches)}")
        selected[role] = matches[0]
    for name in REQUIRED_FIXED:
        if name not in files:
            raise ValueError(f"{task_dir}: missing {name}")
        selected[name.removesuffix(".json")] = files[name]
    if len(files) != 5:
        raise ValueError(f"{task_dir}: expected 5 files, found {len(files)}")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a tamper-evident manifest for the official CLINSQL split files.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    splits_root = args.repo.resolve() / "data" / "splits"
    records = []
    counts: Counter[str] = Counter()
    for split in ("validation", "test"):
        split_root = splits_root / split
        for query_file in sorted(split_root.rglob("query_*.txt")):
            task_dir = query_file.parent
            rel_parts = task_dir.relative_to(split_root).parts
            if len(rel_parts) != 3:
                raise ValueError(f"Unexpected task path: {task_dir}")
            domain, difficulty_dir, task_number = rel_parts
            difficulty = difficulty_dir.removesuffix("_level_queries")
            files = task_files(task_dir)
            record = {
                "split": split,
                "domain": domain,
                "difficulty": difficulty,
                "task_number": task_number,
                "task_key": f"{split}/{domain}/{difficulty}/{task_number}",
                "files": {
                    role: {
                        "relative_path": path.relative_to(args.repo.resolve()).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                    for role, path in sorted(files.items())
                },
            }
            records.append(record)
            counts[f"split:{split}"] += 1
            counts[f"domain:{domain}"] += 1
            counts[f"difficulty:{difficulty}"] += 1

    unique_keys = {record["task_key"] for record in records}
    if len(unique_keys) != len(records):
        raise ValueError("Duplicate split-scoped task keys detected")
    if counts["split:validation"] != 190 or counts["split:test"] != 443:
        raise ValueError(f"Unexpected official split sizes: {dict(counts)}")

    payload = {
        "manifest_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "repository": "Barryshen1/ClinSQL",
            "archive_path": str(args.archive.resolve()),
            "archive_bytes": args.archive.stat().st_size,
            "archive_sha256": sha256(args.archive.resolve()),
        },
        "guardrails": {
            "test_gold_opened_for_semantic_analysis": False,
            "test_files_processed_as_opaque_bytes_for_hashing_only": True,
        },
        "counts": dict(sorted(counts.items())),
        "task_count": len(records),
        "tasks": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "task_count": len(records),
        "validation": counts["split:validation"],
        "test": counts["split:test"],
        "archive_sha256": payload["source"]["archive_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
