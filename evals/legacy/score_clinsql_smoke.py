#!/usr/bin/env python3
"""Score one CLINSQL validation smoke result without printing result values."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_runner(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("clinsql_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_export(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "status": "executed",
        "columns": payload.get("columns", []),
        "rows": payload.get("rows", []),
        "returned_rows": payload.get("returned_rows"),
        "elapsed_ms": payload.get("elapsed_ms"),
        "truncated": payload.get("truncated"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runner = load_runner(args.runner.resolve())
    official = load_export(args.official.resolve())
    local = load_export(args.local.resolve())
    reference_columns, reference_rows = runner.load_reference(args.reference.resolve())
    official_vs_reference = runner.compare(reference_columns, reference_rows, official)
    local_vs_reference = runner.compare(reference_columns, reference_rows, local)
    official_vs_local = runner.compare_targets(official, local)

    # Persist only comparison metadata and cryptographic provenance, never values.
    payload = {
        "task_key": "validation/Diagnostic_Procedures/easy/001",
        "official_export_sha256": sha256_file(args.official.resolve()),
        "local_export_sha256": sha256_file(args.local.resolve()),
        "reference_sha256": sha256_file(args.reference.resolve()),
        "official_returned_rows": official.get("returned_rows"),
        "local_returned_rows": local.get("returned_rows"),
        "reference_rows": len(reference_rows),
        "official_elapsed_ms": official.get("elapsed_ms"),
        "local_elapsed_ms": local.get("elapsed_ms"),
        "official_vs_reference": official_vs_reference,
        "local_vs_reference": local_vs_reference,
        "official_vs_local": official_vs_local,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "task_key": payload["task_key"],
        "official_rows": payload["official_returned_rows"],
        "local_rows": payload["local_returned_rows"],
        "reference_rows": payload["reference_rows"],
        "official_local_strict_match": official_vs_local.get("unordered_value_match", False),
        "official_local_numeric_tolerant_match": official_vs_local.get(
            "unordered_numeric_tolerant_match", False
        ),
        "official_reference_strict_match": official_vs_reference.get("unordered_value_match", False),
        "official_reference_numeric_tolerant_match": official_vs_reference.get(
            "unordered_numeric_tolerant_match", False
        ),
        "local_reference_strict_match": local_vs_reference.get("unordered_value_match", False),
        "local_reference_numeric_tolerant_match": local_vs_reference.get(
            "unordered_numeric_tolerant_match", False
        ),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
