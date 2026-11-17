from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from meddb.audit import AuditLog
from meddb.catalog import Catalog
from meddb.config import Settings, ensure_runtime_directories, load_settings
from meddb.db import Database
from meddb.errors import MedDBError
from meddb.exporter import (
    copy_to_clipboard,
    render_table,
    result_as_text,
    write_output,
)
from meddb.mcp_guard import audit_no_mcp
from meddb.models import QueryResult
from meddb.security import validate_sql
from meddb.templates import RenderedTemplate, TemplateRegistry


OUTPUT_FORMATS = ("table", "csv", "json", "tsv")


def _records_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "(没有结果)"
    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    rows = [tuple(record.get(column) for column in columns) for record in records]
    result = QueryResult(
        query_id="local_metadata",
        columns=columns,
        rows=rows,
        elapsed_ms=0,
        truncated=False,
        plan_total_cost=None,
    )
    return render_table(result)


def _load_params_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise MedDBError(f"参数文件不存在: {source}")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MedDBError(f"参数文件不是有效JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MedDBError("参数文件顶层必须是JSON对象")
    return value


def _parse_key_values(values: list[str] | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in values or []:
        if "=" not in item:
            raise MedDBError(f"模板参数必须写成name=value: {item}")
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise MedDBError(f"模板参数名不能为空: {item}")
        parsed[name] = value.strip()
    return parsed


def _print_query_plan(
    *,
    source_label: str,
    tables: tuple[str, ...],
    params: dict[str, Any],
    detail: bool,
    notes: tuple[str, ...] = (),
) -> None:
    print("查询计划")
    print(f"  来源: {source_label}")
    print(f"  模式: {'记录级明细（最多100行）' if detail else '聚合统计'}")
    print(f"  表: {', '.join(tables) if tables else '(无实体表)'}")
    print(
        "  参数: "
        + (
            json.dumps(params, ensure_ascii=False, default=str)
            if params
            else "{}"
        )
    )
    for note in notes:
        print(f"  统计说明: {note}")


def _emit_result(
    result: QueryResult,
    settings: Settings,
    *,
    output_format: str,
    output: str | None,
    clipboard: bool,
    detail: bool,
) -> str | None:
    output_path: str | None = None
    if os.environ.get("MEDDB_CODEX_SAFE") == "1":
        if clipboard:
            raise MedDBError("Clipboard output is disabled in Codex safe mode")
        rendered = result_as_text(result, output_format)
        max_rows = int(settings.security.get("codex_aggregate_max_rows", 200))
        max_bytes = int(settings.security.get("codex_max_output_bytes", 1048576))
        export_only = (
            detail
            or result.returned_rows > max_rows
            or len(rendered.encode("utf-8")) > max_bytes
            or output is not None
        )
        if export_only:
            effective_output = output or f"{result.query_id}.json"
            effective_format = output_format if output else "json"
            saved = write_output(
                result,
                settings.paths["exports"],
                effective_output,
                effective_format,
            )
            output_path = str(saved)
            print(
                json.dumps(
                    {
                        "query_id": result.query_id,
                        "result_visibility": "export_only",
                        "returned_rows": result.returned_rows,
                        "elapsed_ms": result.elapsed_ms,
                        "truncated": result.truncated,
                        "output_path": output_path,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(rendered)
        return output_path
    if output:
        saved = write_output(
            result,
            settings.paths["exports"],
            output,
            output_format,
        )
        output_path = str(saved)
        print(f"已导出: {saved}")
    else:
        print(result_as_text(result, output_format))
    if clipboard:
        copy_to_clipboard(result)
        print("结果已复制到系统剪贴板")
    return output_path


def _run_validated(
    *,
    settings: Settings,
    sql: str,
    params: dict[str, Any],
    detail: bool,
    source_label: str,
    command_type: str,
    template_id: str | None,
    sql_file: str | None,
    output_format: str,
    output: str | None,
    clipboard: bool,
    explain_only: bool,
    notes: tuple[str, ...] = (),
    require_confirmation: bool = False,
    confirmed: bool = False,
) -> int:
    validation = validate_sql(sql, settings.security, detail=detail)
    _print_query_plan(
        source_label=source_label,
        tables=validation.tables,
        params=params,
        detail=detail,
        notes=notes,
    )
    if require_confirmation and not confirmed:
        answer = input("确认执行该只读查询？[y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消")
            return 1

    database = Database(settings)
    result = database.execute(
        validation,
        params,
        detail=detail,
        explain_only=explain_only,
    )
    output_path = _emit_result(
        result,
        settings,
        output_format=output_format,
        output=output,
        clipboard=clipboard,
        detail=detail,
    )
    AuditLog(settings.paths["audit"]).record(
        result=result,
        validation=validation,
        params=params,
        command_type=command_type,
        template_id=template_id,
        sql_file=sql_file,
        output_path=output_path,
    )
    return 0


def command_status(settings: Settings) -> int:
    database_status = Database(settings).status()
    catalog = Catalog(settings.paths["catalog"])
    catalog_status = catalog.metadata()
    mcp_status = audit_no_mcp(
        settings.codex["config"],
        settings.codex["plugin_cache"],
        settings.codex.get("no_mcp_profile"),
    )
    records = [
        {"item": "database", "value": database_status["database"]},
        {"item": "database_user", "value": database_status["user"]},
        {
            "item": "db_default_read_only",
            "value": database_status["default_transaction_read_only"],
        },
        {"item": "statement_timeout", "value": database_status["statement_timeout"]},
        {"item": "catalog_generated", "value": catalog_status.get("generated_at_utc")},
        {"item": "catalog_tables", "value": catalog_status.get("table_count")},
        {"item": "catalog_columns", "value": catalog_status.get("column_count")},
        {"item": "mcp_disabled", "value": str(mcp_status.ok).lower()},
    ]
    print(_records_table(records))
    if not mcp_status.ok:
        for issue in mcp_status.issues:
            print(f"MCP配置问题: {issue}", file=sys.stderr)
        return 3
    return 0


def command_mcp_status(settings: Settings) -> int:
    status = audit_no_mcp(
        settings.codex["config"],
        settings.codex["plugin_cache"],
        settings.codex.get("no_mcp_profile"),
    )
    records = [
        {
            "item": "disabled_mcp_servers",
            "value": ", ".join(status.disabled_mcp_servers) or "(none configured)",
        },
        {
            "item": "disabled_apps",
            "value": ", ".join(status.disabled_apps) or "(none discovered)",
        },
        {"item": "fully_disabled", "value": str(status.ok).lower()},
    ]
    print(_records_table(records))
    for issue in status.issues:
        print(f"问题: {issue}", file=sys.stderr)
    return 0 if status.ok else 3


def command_catalog(args: argparse.Namespace, settings: Settings) -> int:
    catalog = Catalog(settings.paths["catalog"])
    if args.catalog_command == "search":
        print(_records_table(catalog.search(args.query, args.limit)))
        return 0
    if args.catalog_command == "metadata":
        print(
            _records_table(
                [{"key": key, "value": value} for key, value in catalog.metadata().items()]
            )
        )
        return 0
    raise MedDBError("缺少catalog子命令")


def command_schema(args: argparse.Namespace, settings: Settings) -> int:
    catalog = Catalog(settings.paths["catalog"])
    if args.schema_command == "list":
        print(_records_table(catalog.list_tables(args.dataset, args.limit)))
        return 0
    if args.schema_command == "show":
        detail = catalog.show_table(args.table)
        print("表")
        print(_records_table([detail["table"]]))
        print("\n字段")
        print(_records_table(detail["columns"]))
        if detail["relationships"]:
            print("\n外键")
            print(_records_table(detail["relationships"]))
        return 0
    raise MedDBError("缺少schema子命令")


def command_templates(args: argparse.Namespace, settings: Settings) -> int:
    registry = TemplateRegistry(
        settings.paths["templates"],
        Catalog(settings.paths["catalog"]),
    )
    rows = [
        {
            "id": item["id"],
            "title": item.get("title_cn"),
            "datasets": ",".join(item.get("datasets", [])),
            "description": item.get("description_cn"),
        }
        for item in registry.list(args.dataset)
    ]
    print(_records_table(rows))
    return 0


def command_run(args: argparse.Namespace, settings: Settings) -> int:
    catalog = Catalog(settings.paths["catalog"])
    registry = TemplateRegistry(settings.paths["templates"], catalog)
    supplied = _parse_key_values(args.param)
    rendered = registry.render(args.template_id, supplied)
    return _run_validated(
        settings=settings,
        sql=rendered.sql,
        params=rendered.params,
        detail=rendered.detail,
        source_label=f"内置模板 {rendered.template_id}: {rendered.title}",
        command_type="template",
        template_id=rendered.template_id,
        sql_file=None,
        output_format=args.format,
        output=args.output,
        clipboard=args.clipboard,
        explain_only=args.explain_only,
        notes=rendered.notes,
    )


def command_query(args: argparse.Namespace, settings: Settings) -> int:
    sql_path = Path(args.sql_file).expanduser().resolve()
    if not sql_path.exists() or not sql_path.is_file():
        raise MedDBError(f"SQL文件不存在: {sql_path}")
    sql = sql_path.read_text(encoding="utf-8")
    params = _load_params_file(args.params_file)
    return _run_validated(
        settings=settings,
        sql=sql,
        params=params,
        detail=args.detail,
        source_label=f"自定义SQL文件 {sql_path}",
        command_type="custom_sql",
        template_id=None,
        sql_file=str(sql_path),
        output_format=args.format,
        output=args.output,
        clipboard=args.clipboard,
        explain_only=args.explain_only,
        require_confirmation=True,
        confirmed=args.yes,
    )


def command_audit(args: argparse.Namespace, settings: Settings) -> int:
    print(_records_table(AuditLog(settings.paths["audit"]).recent(args.limit)))
    return 0


def _prompt_template(registry: TemplateRegistry) -> tuple[str, dict[str, Any]] | None:
    templates = registry.list()
    print("\n可用分析模板")
    for index, item in enumerate(templates, start=1):
        print(
            f"  {index}. {item.get('title_cn')} "
            f"[{','.join(item.get('datasets', []))}]"
        )
    print("  0. 退出")
    raw = input("请选择模板编号: ").strip()
    if raw in {"", "0"}:
        return None
    try:
        selected = templates[int(raw) - 1]
    except (ValueError, IndexError) as exc:
        raise MedDBError("模板编号无效") from exc

    supplied: dict[str, Any] = {}
    for spec in selected.get("parameters", []):
        name = spec["name"]
        default = spec.get("default")
        choices = spec.get("choices")
        if name == "dataset":
            choices = selected.get("datasets", [])
        if choices and len(choices) <= 12:
            print(f"  {name}可选: {', '.join(map(str, choices))}")
        default_text = "" if default is None else f" [默认 {default}]"
        value = input(f"请输入{name}{default_text}: ").strip()
        if value == "" and default is not None:
            value = default
        supplied[name] = value
    return selected["id"], supplied


def interactive_menu(settings: Settings) -> int:
    catalog = Catalog(settings.paths["catalog"])
    registry = TemplateRegistry(settings.paths["templates"], catalog)
    print("meddb 本地医学数据库只读终端")
    print("数据不会发送给Codex模型，也不使用MCP。")
    while True:
        selection = _prompt_template(registry)
        if selection is None:
            print("已退出")
            return 0
        template_id, supplied = selection
        rendered: RenderedTemplate = registry.render(template_id, supplied)
        code = _run_validated(
            settings=settings,
            sql=rendered.sql,
            params=rendered.params,
            detail=rendered.detail,
            source_label=f"内置模板 {rendered.template_id}: {rendered.title}",
            command_type="interactive_template",
            template_id=rendered.template_id,
            sql_file=None,
            output_format="table",
            output=None,
            clipboard=False,
            explain_only=False,
            notes=rendered.notes,
        )
        if code != 0:
            return code
        again = input("继续执行其他模板？[Y/n]: ").strip().lower()
        if again in {"n", "no"}:
            return 0


def add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=OUTPUT_FORMATS, default="table")
    parser.add_argument(
        "--output",
        help="保存到exports目录中的相对文件名；不提供则显示在终端。",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="同时复制制表符分隔结果到系统剪贴板。",
    )
    parser.add_argument(
        "--explain-only",
        action="store_true",
        help="只显示查询计划成本，不执行查询。",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meddb",
        description="本地、无模型、无MCP的PostgreSQL医学数据库只读CLI。",
    )
    parser.add_argument("--config", help="覆盖默认meddb.json路径。")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="检查数据库、catalog和MCP状态。")
    subparsers.add_parser("mcp-status", help="验证MCP与连接器全部禁用。")
    subparsers.add_parser("datasets", help="列出数据集及访问模式。")

    catalog = subparsers.add_parser("catalog", help="检索本地字段catalog。")
    catalog_sub = catalog.add_subparsers(dest="catalog_command")
    search = catalog_sub.add_parser("search", help="搜索表和字段。")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=30)
    catalog_sub.add_parser("metadata", help="查看catalog版本信息。")

    schema = subparsers.add_parser("schema", help="查看本地schema结构。")
    schema_sub = schema.add_subparsers(dest="schema_command")
    schema_list = schema_sub.add_parser("list", help="列出表。")
    schema_list.add_argument("--dataset")
    schema_list.add_argument("--limit", type=int, default=200)
    schema_show = schema_sub.add_parser("show", help="显示表全部字段和外键。")
    schema_show.add_argument("table", help="schema.table")

    templates = subparsers.add_parser("templates", help="列出内置查询模板。")
    templates.add_argument("--dataset")

    run = subparsers.add_parser("run", help="运行内置参数化模板。")
    run.add_argument("template_id")
    run.add_argument(
        "--param",
        action="append",
        help="模板参数name=value，可重复。",
    )
    add_output_arguments(run)

    query = subparsers.add_parser("query", help="运行严格校验的自定义只读SQL。")
    query.add_argument("--sql-file", required=True)
    query.add_argument("--params-file")
    query.add_argument(
        "--detail",
        action="store_true",
        help="显式允许记录级结果，最多100行。",
    )
    query.add_argument("--yes", action="store_true", help="跳过执行前确认。")
    add_output_arguments(query)

    audit = subparsers.add_parser("audit", help="查看不含结果正文的查询审计。")
    audit.add_argument("--limit", type=int, default=20)
    return parser


def dispatch(args: argparse.Namespace, settings: Settings) -> int:
    if args.command is None:
        return interactive_menu(settings)
    if args.command == "status":
        return command_status(settings)
    if args.command == "mcp-status":
        return command_mcp_status(settings)
    if args.command == "datasets":
        print(_records_table(Catalog(settings.paths["catalog"]).datasets()))
        return 0
    if args.command == "catalog":
        return command_catalog(args, settings)
    if args.command == "schema":
        return command_schema(args, settings)
    if args.command == "templates":
        return command_templates(args, settings)
    if args.command in {"run", "query"} and os.environ.get("MEDDB_CODEX_SAFE") == "1":
        allowed_users = settings.security.get(
            "codex_safe_database_users", ["meddb_reader"]
        )
        if not isinstance(allowed_users, list) or not allowed_users or not all(
            isinstance(value, str) and value for value in allowed_users
        ):
            raise MedDBError("security.codex_safe_database_users must be a non-empty string list")
        database_user = settings.database.get("user")
        if database_user not in allowed_users:
            raise MedDBError(
                "Codex safe mode requires an explicitly allowed read-only role; "
                f"configured user is {database_user!r}"
            )
        status = audit_no_mcp(
            settings.codex["config"],
            settings.codex["plugin_cache"],
            settings.codex.get("no_mcp_profile"),
        )
        if not status.ok:
            raise MedDBError("Codex no-mcp check failed: " + "; ".join(status.issues))
    if args.command == "run":
        return command_run(args, settings)
    if args.command == "query":
        return command_query(args, settings)
    if args.command == "audit":
        return command_audit(args, settings)
    raise MedDBError(f"未知命令: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        ensure_runtime_directories(settings)
        return dispatch(args, settings)
    except (MedDBError, KeyboardInterrupt) as exc:
        message = "操作已取消" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"错误: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
