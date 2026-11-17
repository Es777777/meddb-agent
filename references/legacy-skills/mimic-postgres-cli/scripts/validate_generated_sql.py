#!/usr/bin/env python3
"""Static, no-database validation for MedDB MIMIC generated SQL.

This validator is intentionally conservative. It checks routing and format
contracts before EXPLAIN; PostgreSQL remains the authority for complete parsing
and column validation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

POSITIONAL = re.compile(r"\$\d+|\?|%s")
NAMED = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
FORBIDDEN = re.compile(r"\b(?:insert|update|delete|merge|alter|create|drop|truncate|grant|revoke|copy|do|call|set|reset|vacuum|analyze)\b", re.IGNORECASE)
PHYSICAL_SOURCE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_$.]*)", re.IGNORECASE)
CTE = re.compile(r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.IGNORECASE)
CREATE_TABLE = re.compile(r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_][A-Za-z0-9_.]*)", re.IGNORECASE)


def compact_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def allowed_schemas(dataset: str) -> set[str]:
    return {"mimiciii"} if dataset == "mimiciii" else {"mimiciv_hosp", "mimiciv_icu", "mimiciv_derived"}


def context_tables(path: Path | None) -> set[str]:
    if path is None:
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    return {name.lower().split(".")[-1] for name in CREATE_TABLE.findall(text)}


def validate(sql: str, params: object, dataset: str, schema_context: Path | None) -> list[str]:
    errors: list[str] = []
    if not sql.strip():
        return ["empty_sql"]
    try:
        statements = [item for item in sqlglot.parse(sql, read="postgres") if item is not None]
    except ParseError:
        return ["sql_parse_error"]
    if len(statements) != 1:
        return ["multiple_statements"]
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        return ["not_select_or_with_select"]
    if statement.find(exp.Into, exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Lock):
        errors.append("forbidden_statement_or_keyword")
    if statement.find(exp.Parameter):
        errors.append("positional_or_driver_parameter")
    placeholders = list(statement.find_all(exp.Placeholder))
    if any(not item.name or item.name.isdigit() for item in placeholders):
        errors.append("positional_or_driver_parameter")
    for star in statement.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            errors.append("select_star")
    if not isinstance(params, dict):
        errors.append("params_not_json_object")
        params = {}
    if {item.name for item in placeholders} != set(params):
        errors.append("named_parameter_key_mismatch")
    permitted, known_tables = allowed_schemas(dataset), context_tables(schema_context)
    for scope in traverse_scope(statement):
        for _, (_, source) in scope.selected_sources.items():
            if not isinstance(source, exp.Table):
                continue  # Resolved CTE/subquery, not a physical table.
            table, schema = source.name.lower(), source.db.lower()
            label = source.sql(dialect="postgres")
            if not schema:
                errors.append(f"unqualified_physical_source:{label}")
                continue
            if source.catalog or schema not in permitted:
                errors.append(f"wrong_or_unapproved_schema:{label}")
            if known_tables and table not in known_tables:
                errors.append(f"table_not_in_schema_context:{label}")
    return sorted(set(errors))


def main() -> None:
    parser = argparse.ArgumentParser(description="Static no-database MIMIC SQL validation")
    parser.add_argument("--sql-file", type=Path, required=True)
    parser.add_argument("--params-file", type=Path, required=True)
    parser.add_argument("--dataset", choices=("mimiciii", "mimiciv"), required=True)
    parser.add_argument("--schema-context", type=Path)
    args = parser.parse_args()
    try:
        params = json.loads(args.params_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result = {"valid": False, "errors": [f"invalid_params_json:{type(exc).__name__}"]}
    else:
        errors = validate(args.sql_file.read_text(encoding="utf-8", errors="replace"), params, args.dataset, args.schema_context)
        result = {"valid": not errors, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["valid"] else 2)


if __name__ == "__main__":
    main()
