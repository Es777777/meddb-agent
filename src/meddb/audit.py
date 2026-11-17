from __future__ import annotations

import getpass
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meddb.models import QueryResult, ValidationResult


SENSITIVE_KEY_PARTS = ("password", "secret", "token", "credential")


def _redact_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            "<redacted>"
            if any(part in key.lower() for part in SENSITIVE_KEY_PARTS)
            else value
        )
        for key, value in params.items()
    }


class AuditLog:
    def __init__(self, path: Path):
        self.path = path

    def record(
        self,
        *,
        result: QueryResult,
        validation: ValidationResult,
        params: dict[str, Any],
        command_type: str,
        template_id: str | None,
        sql_file: str | None,
        output_path: str | None,
        status: str = "ok",
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "query_id": result.query_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "os_user": getpass.getuser(),
            "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME"),
            "command_type": command_type,
            "template_id": template_id,
            "sql_file": sql_file,
            "sql_sha256": hashlib.sha256(
                validation.normalized_sql.encode("utf-8")
            ).hexdigest(),
            "parameters": _redact_params(params),
            "tables": list(validation.tables),
            "columns": list(validation.columns),
            "is_aggregate": validation.is_aggregate,
            "elapsed_ms": result.elapsed_ms,
            "plan_total_cost": result.plan_total_cost,
            "returned_rows": result.returned_rows,
            "truncated": result.truncated,
            "output_path": output_path,
            "status": status,
            "warnings": result.warnings,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str))
            handle.write("\n")

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        records.reverse()
        return records

