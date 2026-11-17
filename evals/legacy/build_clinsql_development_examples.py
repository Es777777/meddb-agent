"""Freeze six declared dev examples. Never read clinical results or test data."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(r"F:\database\benchmark")
    source = Path(r"F:\database\vendor\ClinSQL-main\data\splits\validation")
    output = root / "configs" / "clinsql_declared_development_examples_20261027.json"
    if output.exists():
        raise SystemExit("Already frozen")
    tasks = [
        ("Diagnostic_Procedures", "001"), ("Disease_Diagnosis_and_Outcomes", "002"),
        ("Laboratory_Results_Analysis", "004"), ("Medication_Management", "002"),
        ("Patient_Demographics_and_Admissions", "004"), ("Vital_Signs_Monitoring", "001"),
    ]
    examples = []
    for domain, task_id in tasks:
        rel = Path(domain) / "easy_level_queries" / task_id
        question = source / rel / f"query_{task_id}.txt"
        sql = root / "clinsql-port" / "validation" / rel / f"sql_{task_id}.sql"
        examples.append({
            "task_key": f"validation/{domain}/easy/{task_id}",
            "question": question.read_text(encoding="utf-8"),
            "sql": sql.read_text(encoding="utf-8"),
            "question_sha256": hashlib.sha256(question.read_bytes()).hexdigest(),
            "sql_sha256": hashlib.sha256(sql.read_bytes()).hexdigest(),
        })
    payload = {
        "purpose": "declared_validation_development_examples",
        "source": "https://github.com/Barryshen1/ClinSQL",
        "dialect": "existing deterministic PostgreSQL port",
        "scope": "Explicit dev training examples; exclude all six IDs from every scored task list",
        "not_included": ["result CSV", "patient rows", "rubrics", "test data"],
        "examples": examples,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "examples": len(examples), "bytes": output.stat().st_size}))


if __name__ == "__main__":
    main()
