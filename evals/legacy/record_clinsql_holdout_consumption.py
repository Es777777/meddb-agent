"""Record two preselected smoke IDs consumed from a frozen validation reserve."""
import hashlib
import json
from pathlib import Path

root = Path(r"F:\database\benchmark\artifacts")
source = root / "clinsql_validation_reserved20_20261027.json"
destination = root / "clinsql_validation_reserved_remaining18_20261027.json"
if destination.exists():
    raise SystemExit("Remaining holdout already frozen")
data = json.loads(source.read_text(encoding="utf-8"))
consumed = {
    "validation/Patient_Demographics_and_Admissions/easy/043",
    "validation/Medication_Management/easy/010",
}
remaining = [item for item in data["tasks"] if item["task_key"] not in consumed]
assert len(remaining) == 18
destination.write_text(json.dumps({
    "purpose": "Remaining validation reserve after two-task comparison smoke",
    "source_manifest": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "consumed_for_zero_shot_and_few_shot_smoke": sorted(consumed),
    "tasks": remaining,
    "limitations": data["exposure_boundary"],
}, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"path": str(destination), "remaining": len(remaining)}))
