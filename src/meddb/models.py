from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValidationResult:
    normalized_sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    functions: tuple[str, ...]
    parameter_names: tuple[str, ...]
    is_aggregate: bool


@dataclass
class QueryResult:
    query_id: str
    columns: list[str]
    rows: list[tuple[Any, ...]]
    elapsed_ms: int
    truncated: bool
    plan_total_cost: float | None
    warnings: list[str] = field(default_factory=list)

    @property
    def returned_rows(self) -> int:
        return len(self.rows)

