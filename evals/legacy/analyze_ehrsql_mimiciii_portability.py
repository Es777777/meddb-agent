#!/usr/bin/env python3
"""Classify published EHRSQL MIMIC-III validation queries for raw PostgreSQL use.

EHRSQL's official database is a 17-table, sampled and time-shifted derivative.
This tool does not pretend that its rows equal raw MIMIC-III.  It only identifies
gold queries whose referenced objects and SQL syntax are directly portable to the
raw MIMIC-III PostgreSQL schemas used by the paired import comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


RAW_TABLES = {
    "admissions", "chartevents", "diagnoses_icd", "d_icd_diagnoses",
    "d_icd_procedures", "d_items", "d_labitems", "icustays",
    "inputevents_cv", "labevents", "microbiologyevents", "outputevents",
    "patients", "prescriptions", "procedures_icd", "transfers",
}

# Columns created by EHRSQL preprocessing rather than present in MIMIC-III 1.4.
DERIVED_COLUMNS = {
    "admissions.age",
    "diagnoses_icd.charttime",
    "procedures_icd.charttime",
}

SQLITE_ONLY_PATTERNS = {
    "sqlite_datetime": re.compile(r"\bdatetime\s*\(", re.I),
    "sqlite_strftime": re.compile(r"\bstrftime\s*\(", re.I),
    "sqlite_julianday": re.compile(r"\bjulianday\s*\(", re.I),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def referenced_tables(sql: str) -> list[str]:
    tables = re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I)
    return sorted(set(table.lower() for table in tables))


def classify(sql: str) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    tables = referenced_tables(sql)
    unsupported = sorted(set(tables) - RAW_TABLES)
    if unsupported:
        reasons.extend(f"derived_or_missing_table:{table}" for table in unsupported)
    lower = sql.lower()
    for column in sorted(DERIVED_COLUMNS):
        if re.search(rf"\b{re.escape(column)}\b", lower):
            reasons.append(f"derived_or_missing_column:{column}")
    for label, pattern in SQLITE_ONLY_PATTERNS.items():
        if pattern.search(sql):
            reasons.append(label)
    if re.search(r"\bcurrent_time\b", sql, flags=re.I):
        reasons.append("benchmark_current_time_placeholder")
    return tables, sorted(set(reasons))


def qualify(sql: str, tables: list[str]) -> str:
    converted = sql
    for table in sorted(tables, key=len, reverse=True):
        converted = re.sub(
            rf"\b(from|join)\s+{re.escape(table)}\b",
            lambda match: f"{match.group(1)} mimiciii.{table}",
            converted,
            flags=re.I,
        )
    return converted.rstrip().rstrip(";") + ";"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records: list[dict[str, Any]] = json.loads(args.validation.read_text(encoding="utf-8"))
    reason_counts: Counter[str] = Counter()
    table_counts: Counter[str] = Counter()
    out_records: list[dict[str, Any]] = []
    for record in records:
        if record["is_impossible"]:
            out_records.append({
                "id": record["id"],
                "question": record["question"],
                "is_impossible": True,
                "para_type": record.get("para_type"),
                "department": record.get("department"),
                "classification": "published_unanswerable",
            })
            continue
        sql = record["query"]
        tables, reasons = classify(sql)
        table_counts.update(tables)
        reason_counts.update(reasons)
        out_records.append({
            "id": record["id"],
            "question": record["question"],
            "is_impossible": False,
            "para_type": record.get("para_type"),
            "department": record.get("department"),
            "q_tag": record.get("q_tag"),
            "tables": tables,
            "classification": "direct_postgres_candidate" if not reasons else "requires_preprocessed_schema_or_translation",
            "reasons": reasons,
            "gold_sql": sql,
            "postgres_sql": qualify(sql, tables) if not reasons else None,
        })

    answerable = [record for record in out_records if not record["is_impossible"]]
    portable = [record for record in answerable if record["classification"] == "direct_postgres_candidate"]
    summary = {
        "validation_records": len(records),
        "answerable": len(answerable),
        "unanswerable": len(records) - len(answerable),
        "direct_postgres_candidates": len(portable),
        "requires_preprocessed_schema_or_translation": len(answerable) - len(portable),
        "candidate_rate_of_answerable_pct": round(100 * len(portable) / len(answerable), 2),
        "reason_counts": dict(sorted(reason_counts.items())),
        "referenced_table_counts": dict(sorted(table_counts.items())),
    }
    output = {
        "source": str(args.validation.resolve()),
        "source_sha256": sha256(args.validation),
        "classification_policy": {
            "raw_tables": sorted(RAW_TABLES),
            "derived_columns": sorted(DERIVED_COLUMNS),
            "sqlite_only_patterns": sorted(SQLITE_ONLY_PATTERNS),
            "note": "Candidate means direct syntax/object compatibility only; execution is a separate gate.",
        },
        "summary": summary,
        "records": out_records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
