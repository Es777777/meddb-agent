#!/usr/bin/env python3
"""Build an EHRSQL prediction mapping from a published split's gold queries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = json.loads(args.data.read_text(encoding="utf-8"))
    predictions = {record["id"]: record["query"] for record in records}
    if len(predictions) != len(records):
        raise ValueError("Duplicate EHRSQL ids found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "records": len(records),
        "answerable": sum(not record["is_impossible"] for record in records),
        "unanswerable": sum(record["is_impossible"] for record in records),
        "source_sha256": sha256(args.data),
        "output_sha256": sha256(args.output),
    }))


if __name__ == "__main__":
    main()
