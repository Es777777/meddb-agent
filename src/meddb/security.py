from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from meddb.errors import SecurityError
from meddb.models import ValidationResult


PARAMETER_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PROHIBITED_NODE_NAMES = {
    "alter",
    "analyze",
    "attach",
    "cache",
    "command",
    "copy",
    "create",
    "delete",
    "detach",
    "drop",
    "execute",
    "grant",
    "insert",
    "load",
    "lock",
    "merge",
    "pragma",
    "refresh",
    "replace",
    "revoke",
    "set",
    "transaction",
    "truncate",
    "uncache",
    "update",
    "use",
    "vacuum",
}


def validate_identifier(value: str, label: str = "标识符") -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise SecurityError(f"{label}不合法: {value!r}")
    return value


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        return str(node.sql_name()).lower()
    except (AttributeError, TypeError):
        return type(node).__name__.lower()


def _walk(tree: exp.Expression) -> Iterable[exp.Expression]:
    for node in tree.walk():
        yield node


def _is_allowed_star(node: exp.Star) -> bool:
    return isinstance(node.parent, exp.Count)


def _translate_parameters(sql: str) -> str:
    escaped = re.sub(
        r"%(?!\([A-Za-z_][A-Za-z0-9_]*\)s)",
        "%%",
        sql,
    )
    return PARAMETER_RE.sub(lambda match: f"%({match.group(1)})s", escaped)


def validate_parameter_values(
    expected: Iterable[str], supplied: Mapping[str, Any]
) -> dict[str, Any]:
    expected_set = set(expected)
    supplied_set = set(supplied)
    missing = sorted(expected_set - supplied_set)
    unknown = sorted(supplied_set - expected_set)
    if missing:
        raise SecurityError(f"缺少SQL参数: {', '.join(missing)}")
    if unknown:
        raise SecurityError(f"存在未使用参数: {', '.join(unknown)}")
    return dict(supplied)


def validate_sql(
    sql: str,
    policy: Mapping[str, Any],
    *,
    detail: bool = False,
    allow_metadata_only: bool = False,
) -> ValidationResult:
    source = sql.strip()
    if not source:
        raise SecurityError("SQL文件为空")
    if "\x00" in source:
        raise SecurityError("SQL包含NUL字符")
    if re.search(r"\$\d+", source):
        raise SecurityError("不支持位置参数，请使用:name命名参数")

    try:
        statements = [item for item in sqlglot.parse(source, read="postgres") if item]
    except ParseError as exc:
        raise SecurityError(f"SQL语法解析失败: {exc}") from exc

    if len(statements) != 1:
        raise SecurityError("只允许单条SQL语句")
    tree = statements[0]
    if tree.find(exp.Select) is None:
        raise SecurityError("只允许SELECT或WITH ... SELECT查询")

    for node in _walk(tree):
        if type(node).__name__.lower() in PROHIBITED_NODE_NAMES:
            raise SecurityError(f"SQL包含禁止节点: {type(node).__name__}")

    blocked_functions = {
        str(name).lower() for name in policy.get("blocked_functions", [])
    }
    functions = sorted(
        {
            _function_name(node)
            for node in _walk(tree)
            if isinstance(node, exp.Func)
        }
    )
    denied = sorted(set(functions) & blocked_functions)
    if denied:
        raise SecurityError(f"SQL调用禁止函数: {', '.join(denied)}")

    if policy.get("block_select_star", True):
        stars = [
            node
            for node in _walk(tree)
            if isinstance(node, exp.Star) and not _is_allowed_star(node)
        ]
        if stars:
            raise SecurityError("禁止SELECT *，请明确列出字段")

    cte_names = {
        cte.alias_or_name.lower()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }
    allowed_schemas = {
        str(value).lower() for value in policy.get("allowed_schemas", [])
    }
    metadata_only = {
        str(value).lower() for value in policy.get("metadata_only_schemas", [])
    }
    blocked_patterns = [
        re.compile(str(pattern), re.IGNORECASE)
        for pattern in policy.get("blocked_table_patterns", [])
    ]

    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        table_name = table.name.lower()
        schema_name = (table.db or "").lower()
        if not schema_name:
            if table_name in cte_names:
                continue
            if policy.get("require_qualified_tables", True):
                raise SecurityError(f"表必须带schema: {table.name}")
            continue
        if schema_name in metadata_only and not allow_metadata_only:
            raise SecurityError(f"schema只允许查看结构，不能查询数据: {schema_name}")
        if schema_name not in allowed_schemas and schema_name not in metadata_only:
            raise SecurityError(f"schema不在白名单: {schema_name}")
        if any(pattern.search(table_name) for pattern in blocked_patterns):
            raise SecurityError(f"禁止直接查询raw表: {schema_name}.{table_name}")
        tables.add(f"{schema_name}.{table_name}")

    aggregate_types = getattr(exp, "AggFunc", ())
    has_aggregate = bool(aggregate_types) and any(
        isinstance(node, aggregate_types) for node in _walk(tree)
    )
    has_group = tree.find(exp.Group) is not None
    is_aggregate = bool(has_aggregate or has_group)
    if (
        policy.get("require_aggregate_by_default", True)
        and not detail
        and not is_aggregate
    ):
        raise SecurityError("默认模式只允许聚合查询；记录级查询必须显式使用--detail")

    columns = sorted(
        {
            ".".join(part for part in (column.table, column.name) if part)
            for column in tree.find_all(exp.Column)
        }
    )
    parameter_names = tuple(sorted(set(PARAMETER_RE.findall(source))))
    normalized = tree.sql(dialect="postgres", pretty=False)
    normalized = _translate_parameters(normalized)
    return ValidationResult(
        normalized_sql=normalized,
        tables=tuple(sorted(tables)),
        columns=tuple(columns),
        functions=tuple(functions),
        parameter_names=parameter_names,
        is_aggregate=is_aggregate,
    )
