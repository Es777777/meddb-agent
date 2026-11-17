from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meddb.catalog import Catalog
from meddb.errors import TemplateError
from meddb.security import validate_identifier


PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


@dataclass(frozen=True)
class RenderedTemplate:
    template_id: str
    title: str
    sql: str
    params: dict[str, Any]
    detail: bool
    notes: tuple[str, ...]


class TemplateRegistry:
    def __init__(self, root: Path, catalog: Catalog):
        self.root = root
        self.catalog = catalog
        manifest = root / "manifest.json"
        if not manifest.exists():
            raise TemplateError(f"模板清单不存在: {manifest}")
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TemplateError(f"无法读取模板清单: {exc}") from exc
        self._templates = {
            item["id"]: item for item in raw.get("templates", []) if "id" in item
        }

    def list(self, dataset: str | None = None) -> list[dict[str, Any]]:
        values = list(self._templates.values())
        if dataset:
            values = [
                item for item in values if dataset in item.get("datasets", [])
            ]
        return sorted(values, key=lambda item: item["id"])

    def get(self, template_id: str) -> dict[str, Any]:
        try:
            return self._templates[template_id]
        except KeyError as exc:
            raise TemplateError(f"不存在模板: {template_id}") from exc

    @staticmethod
    def _coerce(spec: dict[str, Any], value: Any) -> Any:
        if value is None or value == "":
            return None
        value_type = spec.get("type", "str")
        try:
            if value_type == "int":
                return int(value)
            if value_type == "float":
                return float(value)
            if value_type == "bool":
                if isinstance(value, bool):
                    return value
                lowered = str(value).strip().lower()
                if lowered in {"1", "true", "yes", "y"}:
                    return True
                if lowered in {"0", "false", "no", "n"}:
                    return False
                raise ValueError
            return str(value)
        except (TypeError, ValueError) as exc:
            raise TemplateError(f"参数{spec['name']}类型应为{value_type}") from exc

    def _resolve_identifier(
        self,
        spec: dict[str, Any],
        value: Any,
        all_values: dict[str, Any],
    ) -> str:
        name = spec["name"]
        identifier = validate_identifier(str(value), f"参数{name}")
        choices = spec.get("choices")
        if choices and identifier not in choices:
            raise TemplateError(
                f"参数{name}不在允许范围: {', '.join(map(str, choices))}"
            )
        schema_name = spec.get("schema")
        if spec.get("identifier_kind") == "table":
            if not schema_name:
                raise TemplateError(f"模板参数{name}缺少schema配置")
            self.catalog.validate_query_table(schema_name, identifier)
        if spec.get("identifier_kind") == "column":
            table_param = spec.get("table_param")
            table_name = all_values.get(table_param) if table_param else spec.get("table")
            if not schema_name or not table_name:
                raise TemplateError(f"模板参数{name}无法确定所属表")
            self.catalog.validate_query_table(schema_name, str(table_name))
            if not self.catalog.has_column(schema_name, str(table_name), identifier):
                raise TemplateError(
                    f"列不存在: {schema_name}.{table_name}.{identifier}"
                )
            denied = set(spec.get("denied", []))
            if identifier in denied:
                raise TemplateError(f"模板禁止使用列: {identifier}")
        return identifier

    def render(self, template_id: str, supplied: dict[str, Any]) -> RenderedTemplate:
        template = self.get(template_id)
        values: dict[str, Any] = {}
        unknown = set(supplied) - {
            spec["name"] for spec in template.get("parameters", [])
        }
        if unknown:
            raise TemplateError(f"未知模板参数: {', '.join(sorted(unknown))}")

        for spec in template.get("parameters", []):
            name = spec["name"]
            raw_value = supplied.get(name, spec.get("default"))
            if spec.get("required") and (raw_value is None or raw_value == ""):
                raise TemplateError(f"缺少模板参数: {name}")
            values[name] = self._coerce(spec, raw_value)

        context: dict[str, str] = {}
        dataset = values.get("dataset")
        if dataset:
            dataset = validate_identifier(str(dataset), "dataset")
            if dataset not in template.get("datasets", []):
                raise TemplateError(f"模板不支持数据集: {dataset}")
            context.update(template.get("dataset_identifiers", {}).get(dataset, {}))
            context["schema"] = dataset

        sql_params: dict[str, Any] = {}
        for spec in template.get("parameters", []):
            name = spec["name"]
            value = values[name]
            if spec.get("identifier_kind"):
                if value is None:
                    raise TemplateError(f"标识符参数不能为空: {name}")
                context[name] = self._resolve_identifier(spec, value, values)
            elif name != "dataset":
                sql_params[name] = value

        sql_path = (self.root / template["sql_file"]).resolve()
        try:
            sql_path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise TemplateError("模板SQL路径越界") from exc
        if not sql_path.exists():
            raise TemplateError(f"模板SQL不存在: {sql_path}")
        sql = sql_path.read_text(encoding="utf-8")

        missing_context: set[str] = set()

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in context:
                missing_context.add(key)
                return match.group(0)
            return validate_identifier(str(context[key]), f"模板标识符{key}")

        rendered_sql = PLACEHOLDER_RE.sub(replace, sql)
        if missing_context:
            raise TemplateError(
                f"模板缺少标识符: {', '.join(sorted(missing_context))}"
            )
        return RenderedTemplate(
            template_id=template_id,
            title=template.get("title_cn") or template_id,
            sql=rendered_sql,
            params=sql_params,
            detail=bool(template.get("detail", False)),
            notes=tuple(template.get("notes", [])),
        )

