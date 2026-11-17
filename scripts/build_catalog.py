from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "meddb.json"
DEFAULT_BRFSS_INVENTORY = (
    PROJECT_ROOT.parent / "field_inventory" / "BRFSS_去重字段_中英文.csv"
)
DEFAULT_NHANES_INVENTORY = (
    PROJECT_ROOT.parent / "field_inventory" / "NHANES_去重字段_中英文.csv"
)
SEER8_ROLES = (
    PROJECT_ROOT.parent
    / "seer"
    / "seer8_1975_2023_selected_variables"
    / "seer8_字段角色.csv"
)

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

TABLE_DESCRIPTIONS = {
    "patients": "患者基本人口学和患者级汇总信息。",
    "patient_tumor_summary": "患者拥有的肿瘤数量与多原发汇总。",
    "tumors": "肿瘤病例核心表，一条肿瘤病例一行。",
    "diagnosis": "诊断确认、报告来源和诊断时信息。",
    "tumor_site": "原发部位和癌种重编码。",
    "tumor_site_hierarchy": "肿瘤部位层级关系。",
    "tumor_histology": "组织学、行为和病理类型。",
    "tumor_pathology": "部位、组织学、行为、分级和侧别。",
    "tumor_grade": "临床和病理分级。",
    "tumor_schema": "肿瘤分期schema及适用规则。",
    "stage_summary": "Summary Stage、Historic Stage等概括分期。",
    "stage_ajcc3": "AJCC第3版TNM分期。",
    "stage_ajcc6": "AJCC第6版TNM分期。",
    "stage_ajcc7": "AJCC第7版TNM分期。",
    "stage_2016_2017": "2016至2017年过渡期联合分期。",
    "stage_eod2018": "2018年后EOD分期。",
    "stage_cs": "Collaborative Stage核心字段。",
    "stage_cs_factors": "Collaborative Stage特异因子。",
    "stage_eod10": "早期EOD10分期字段。",
    "stage_eod_legacy": "历史EOD分期字段。",
    "treatments": "手术、放疗、化疗及治疗顺序。",
    "treatment_surgery": "肿瘤手术治疗。",
    "treatment_radiation": "放射治疗。",
    "treatment_systemic": "化疗和其他系统治疗。",
    "outcomes": "生存、生命状态和死亡结局。",
    "outcomes_survival": "生存月数和生命状态。",
    "cause_of_death": "死亡原因分类。",
    "mortality_followup": "死亡和随访信息。",
    "socioeconomic": "地区和社会经济信息。",
    "responses": "BRFSS调查响应主表，一名受访者一年一条记录。",
    "survey_design": "BRFSS复杂抽样的PSU、分层和权重。",
    "demographics": "人口学信息。",
    "health_status": "总体健康和健康天数。",
    "chronic_conditions": "慢性病和既往诊断。",
    "tobacco": "吸烟、电子烟和无烟烟草。",
    "alcohol": "饮酒相关变量。",
    "physical_activity": "身体活动和运动。",
    "diet_nutrition": "膳食与营养。",
    "mental_health": "心理健康。",
    "healthcare_access": "医疗服务可及性。",
    "participants": "NHANES参与者主表，一名参与者在一个周期一行。",
    "cycles": "NHANES调查周期字典。",
    "components": "NHANES组件家族和主题域字典。",
    "source_files": "原始来源文件清单。",
    "import_manifest": "文件导入状态和审计信息。",
    "variable_dictionary": "变量名称、标签、类型和来源字典。",
    "variable_availability": "变量在年份、周期或文件中的可用性。",
    "value_labels": "代码值与文字标签映射。",
}


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_inventory(path: Path) -> dict[str, tuple[str, str]]:
    labels: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return labels
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("field_code") or "").strip().lower()
            if code:
                labels[code] = (
                    (row.get("english_label") or "").strip(),
                    (row.get("chinese_label") or "").strip(),
                )
    return labels


def load_seer8_roles(path: Path) -> dict[str, tuple[str, str]]:
    labels: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return labels
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            field = (row.get("字段") or "").strip().lower()
            if field:
                english = (row.get("科研角色") or "").strip()
                chinese = (row.get("说明") or "").strip()
                labels[field] = (english, chinese)
    return labels


def configured_schemas(config: dict[str, Any]) -> tuple[tuple[str, ...], set[str]]:
    security = config.get("security", {})
    allowed = tuple(str(value) for value in security.get("allowed_schemas", []))
    metadata_only = {
        str(value) for value in security.get("metadata_only_schemas", [])
    }
    schemas = tuple(dict.fromkeys((*allowed, *sorted(metadata_only))))
    if not schemas:
        raise ValueError("配置中没有可编目的schema")
    invalid = [schema for schema in schemas if not IDENTIFIER_RE.fullmatch(schema)]
    if invalid:
        raise ValueError(f"配置包含非法schema名称: {', '.join(invalid)}")
    return schemas, metadata_only


def access_mode(
    schema_name: str,
    table_name: str,
    metadata_only_schemas: set[str] | None = None,
) -> str:
    lowered = table_name.lower()
    if schema_name in (metadata_only_schemas or {"nhanes_raw"}):
        return "metadata_only"
    if lowered == "raw" or lowered.startswith("raw_") or "_raw_" in lowered:
        return "metadata_only"
    return "query"


def table_description(schema_name: str, table_name: str) -> str:
    if schema_name == "nhanes_raw":
        return "NHANES原始XPT组件表，只允许查看结构。"
    if table_name in TABLE_DESCRIPTIONS:
        return TABLE_DESCRIPTIONS[table_name]
    if table_name.endswith("_markers"):
        return "癌种特异性生物标志物和病理扩展字段。"
    if table_name.startswith("stage_"):
        return "按年代和体系拆分的肿瘤分期字段。"
    if table_name.startswith("raw"):
        return "原始数据保留表，只允许查看结构。"
    return ""


def fetch_all(connection: psycopg.Connection[Any], sql: str) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return list(cursor.fetchall())


def fetch_catalog_source(
    connection: psycopg.Connection[Any],
    schemas: tuple[str, ...],
) -> tuple[
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
]:
    schema_sql = ", ".join(f"'{item}'" for item in schemas)
    tables = fetch_all(
        connection,
        f"""
        SELECT n.nspname,
               c.relname,
               CASE c.relkind
                 WHEN 'r' THEN 'table'
                 WHEN 'p' THEN 'partitioned_table'
                 WHEN 'v' THEN 'view'
                 WHEN 'm' THEN 'materialized_view'
               END,
               GREATEST(c.reltuples, 0)::bigint
          FROM pg_class AS c
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname IN ({schema_sql})
           AND c.relkind IN ('r', 'p', 'v', 'm')
           AND NOT EXISTS (
                 SELECT 1
                   FROM pg_inherits AS inheritance
                  WHERE inheritance.inhrelid = c.oid
               )
         ORDER BY n.nspname, c.relname
        """,
    )
    columns = fetch_all(
        connection,
        f"""
        SELECT n.nspname,
               c.relname,
               a.attnum,
               a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod),
               NOT a.attnotnull
          FROM pg_attribute AS a
          JOIN pg_class AS c ON c.oid = a.attrelid
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname IN ({schema_sql})
           AND c.relkind IN ('r', 'p', 'v', 'm')
           AND a.attnum > 0
           AND NOT a.attisdropped
         ORDER BY n.nspname, c.relname, a.attnum
        """,
    )
    primary_keys = fetch_all(
        connection,
        f"""
        SELECT n.nspname,
               c.relname,
               con.conname,
               string_agg(a.attname, ',' ORDER BY keys.ordinality)
          FROM pg_constraint AS con
          JOIN pg_class AS c ON c.oid = con.conrelid
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
          JOIN LATERAL unnest(con.conkey) WITH ORDINALITY
               AS keys(attnum, ordinality) ON true
          JOIN pg_attribute AS a
            ON a.attrelid = c.oid AND a.attnum = keys.attnum
         WHERE con.contype = 'p'
           AND n.nspname IN ({schema_sql})
         GROUP BY n.nspname, c.relname, con.conname
         ORDER BY n.nspname, c.relname
        """,
    )
    foreign_keys = fetch_all(
        connection,
        f"""
        SELECT source_ns.nspname,
               source.relname,
               con.conname,
               string_agg(source_att.attname, ',' ORDER BY source_key.ordinality),
               target_ns.nspname,
               target.relname,
               string_agg(target_att.attname, ',' ORDER BY target_key.ordinality)
          FROM pg_constraint AS con
          JOIN pg_class AS source ON source.oid = con.conrelid
          JOIN pg_namespace AS source_ns ON source_ns.oid = source.relnamespace
          JOIN pg_class AS target ON target.oid = con.confrelid
          JOIN pg_namespace AS target_ns ON target_ns.oid = target.relnamespace
          JOIN LATERAL unnest(con.conkey) WITH ORDINALITY
               AS source_key(attnum, ordinality) ON true
          JOIN LATERAL unnest(con.confkey) WITH ORDINALITY
               AS target_key(attnum, ordinality)
            ON target_key.ordinality = source_key.ordinality
          JOIN pg_attribute AS source_att
            ON source_att.attrelid = source.oid
           AND source_att.attnum = source_key.attnum
          JOIN pg_attribute AS target_att
            ON target_att.attrelid = target.oid
           AND target_att.attnum = target_key.attnum
         WHERE con.contype = 'f'
           AND source_ns.nspname IN ({schema_sql})
         GROUP BY source_ns.nspname, source.relname, con.conname,
                  target_ns.nspname, target.relname
         ORDER BY source_ns.nspname, source.relname, con.conname
        """,
    )
    return tables, columns, primary_keys, foreign_keys


def filter_catalog_source(
    tables: list[tuple[Any, ...]],
    columns: list[tuple[Any, ...]],
    primary_keys: list[tuple[Any, ...]],
    foreign_keys: list[tuple[Any, ...]],
    excluded_patterns: list[re.Pattern[str]],
) -> tuple[
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
]:
    kept_tables = [
        row
        for row in tables
        if not any(pattern.search(str(row[1])) for pattern in excluded_patterns)
    ]
    kept_keys = {(str(row[0]), str(row[1])) for row in kept_tables}
    kept_columns = [
        row for row in columns if (str(row[0]), str(row[1])) in kept_keys
    ]
    kept_primary_keys = [
        row for row in primary_keys if (str(row[0]), str(row[1])) in kept_keys
    ]
    kept_foreign_keys = [
        row
        for row in foreign_keys
        if (str(row[0]), str(row[1])) in kept_keys
        and (str(row[4]), str(row[5])) in kept_keys
    ]
    return kept_tables, kept_columns, kept_primary_keys, kept_foreign_keys


def load_database_labels(
    connection: psycopg.Connection[Any],
    brfss_inventory: dict[str, tuple[str, str]],
    nhanes_inventory: dict[str, tuple[str, str]],
    seer8_roles: dict[str, tuple[str, str]],
) -> dict[tuple[str, str], tuple[str, str]]:
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    for code, value in brfss_inventory.items():
        labels[("brfss", code)] = value
    try:
        rows = fetch_all(
            connection,
            """
            SELECT variable_name, canonical_column
              FROM brfss.variable_dictionary
            """,
        )
        for variable_name, canonical_column in rows:
            source = brfss_inventory.get(str(variable_name).lower())
            if source and canonical_column:
                labels[("brfss", str(canonical_column).lower())] = source
    except psycopg.Error:
        connection.rollback()

    for code, value in nhanes_inventory.items():
        labels[("nhanes", code)] = value
        labels[("nhanes_raw", code)] = value
    for field, value in seer8_roles.items():
        labels[("seer8", field)] = value

    for schema_name in ("seer12", "seer17"):
        try:
            rows = fetch_all(
                connection,
                f"""
                SELECT raw_column_name, source_field_name
                  FROM {schema_name}.variable_dictionary
                """,
            )
            for raw_column, source_field in rows:
                if raw_column:
                    labels[(schema_name, str(raw_column).lower())] = (
                        str(source_field or ""),
                        "",
                    )
        except psycopg.Error:
            connection.rollback()
    return labels


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE catalog_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE db_tables (
          schema_name TEXT NOT NULL,
          table_name TEXT NOT NULL,
          table_type TEXT NOT NULL,
          estimated_rows INTEGER NOT NULL,
          access_mode TEXT NOT NULL,
          primary_key TEXT,
          description_cn TEXT,
          PRIMARY KEY (schema_name, table_name)
        );

        CREATE TABLE db_columns (
          schema_name TEXT NOT NULL,
          table_name TEXT NOT NULL,
          ordinal_position INTEGER NOT NULL,
          column_name TEXT NOT NULL,
          data_type TEXT NOT NULL,
          is_nullable INTEGER NOT NULL,
          is_primary_key INTEGER NOT NULL,
          foreign_key_target TEXT,
          english_label TEXT,
          chinese_label TEXT,
          PRIMARY KEY (schema_name, table_name, column_name)
        );

        CREATE TABLE db_relationships (
          source_schema TEXT NOT NULL,
          source_table TEXT NOT NULL,
          constraint_name TEXT NOT NULL,
          source_columns TEXT NOT NULL,
          target_schema TEXT NOT NULL,
          target_table TEXT NOT NULL,
          target_columns TEXT NOT NULL,
          PRIMARY KEY (source_schema, source_table, constraint_name)
        );

        CREATE TABLE search_fallback (
          kind TEXT NOT NULL,
          schema_name TEXT NOT NULL,
          table_name TEXT NOT NULL,
          column_name TEXT,
          english_label TEXT,
          chinese_label TEXT,
          searchable_text TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE search_index USING fts5(
          kind UNINDEXED,
          schema_name,
          table_name,
          column_name,
          english_label,
          chinese_label,
          tokenize = 'unicode61'
        );

        CREATE INDEX idx_columns_name
          ON db_columns(schema_name, table_name, column_name);
        CREATE INDEX idx_relationship_target
          ON db_relationships(target_schema, target_table);
        """
    )


def build_catalog(
    output: Path,
    tables: list[tuple[Any, ...]],
    columns: list[tuple[Any, ...]],
    primary_keys: list[tuple[Any, ...]],
    foreign_keys: list[tuple[Any, ...]],
    labels: dict[tuple[str, str], tuple[str, str]],
    metadata_only_schemas: set[str] | None = None,
) -> None:
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.unlink(missing_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    pk_map = {
        (str(schema), str(table)): str(pk_columns)
        for schema, table, _constraint, pk_columns in primary_keys
    }
    pk_columns = {
        (schema, table, column)
        for (schema, table), joined in pk_map.items()
        for column in joined.split(",")
    }
    fk_column_map: dict[tuple[str, str, str], str] = {}
    for (
        source_schema,
        source_table,
        _constraint,
        source_columns,
        target_schema,
        target_table,
        target_columns,
    ) in foreign_keys:
        sources = str(source_columns).split(",")
        targets = str(target_columns).split(",")
        for source_column, target_column in zip(sources, targets):
            fk_column_map[
                (str(source_schema), str(source_table), source_column)
            ] = f"{target_schema}.{target_table}.{target_column}"

    digest = hashlib.sha256()
    for sequence in (tables, columns, primary_keys, foreign_keys):
        digest.update(
            json.dumps(sequence, ensure_ascii=False, default=str).encode("utf-8")
        )

    with closing(sqlite3.connect(temp)) as sqlite:
        create_schema(sqlite)
        sqlite.executemany(
            """
            INSERT INTO db_tables (
              schema_name, table_name, table_type, estimated_rows,
              access_mode, primary_key, description_cn
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(schema),
                    str(table),
                    str(table_type),
                    int(estimated_rows),
                    access_mode(
                        str(schema), str(table), metadata_only_schemas
                    ),
                    pk_map.get((str(schema), str(table))),
                    table_description(str(schema), str(table)),
                )
                for schema, table, table_type, estimated_rows in tables
            ],
        )

        column_rows: list[tuple[Any, ...]] = []
        search_rows: list[tuple[Any, ...]] = []
        for schema, table, ordinal, column, data_type, is_nullable in columns:
            schema_name = str(schema)
            table_name = str(table)
            column_name = str(column)
            english, chinese = labels.get(
                (schema_name, column_name.lower()), ("", "")
            )
            column_rows.append(
                (
                    schema_name,
                    table_name,
                    int(ordinal),
                    column_name,
                    str(data_type),
                    int(bool(is_nullable)),
                    int((schema_name, table_name, column_name) in pk_columns),
                    fk_column_map.get((schema_name, table_name, column_name)),
                    english,
                    chinese,
                )
            )
            search_rows.append(
                (
                    "column",
                    schema_name,
                    table_name,
                    column_name,
                    english,
                    chinese,
                )
            )
        sqlite.executemany(
            """
            INSERT INTO db_columns (
              schema_name, table_name, ordinal_position, column_name,
              data_type, is_nullable, is_primary_key, foreign_key_target,
              english_label, chinese_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            column_rows,
        )

        sqlite.executemany(
            """
            INSERT INTO db_relationships (
              source_schema, source_table, constraint_name, source_columns,
              target_schema, target_table, target_columns
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            foreign_keys,
        )

        for schema, table, _table_type, _estimated_rows in tables:
            description = table_description(str(schema), str(table))
            search_rows.append(
                ("table", str(schema), str(table), "", "", description)
            )
        sqlite.executemany(
            """
            INSERT INTO search_index (
              kind, schema_name, table_name, column_name,
              english_label, chinese_label
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            search_rows,
        )
        sqlite.executemany(
            """
            INSERT INTO search_fallback (
              kind, schema_name, table_name, column_name,
              english_label, chinese_label, searchable_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    *row,
                    " ".join(str(value or "") for value in row),
                )
                for row in search_rows
            ],
        )

        metadata = {
            "catalog_version": "1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "schema_sha256": digest.hexdigest(),
            "schema_count": str(len({str(row[0]) for row in tables})),
            "table_count": str(len(tables)),
            "column_count": str(len(columns)),
            "relationship_count": str(len(foreign_keys)),
        }
        sqlite.executemany(
            "INSERT INTO catalog_meta(key, value) VALUES (?, ?)",
            metadata.items(),
        )
        sqlite.commit()
        sqlite.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    temp.replace(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the local meddb catalog.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db-user", default=os.environ.get("PGUSER", "postgres"))
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config.resolve())
    schemas, metadata_only_schemas = configured_schemas(config)
    db = config["database"]
    output = args.output or (PROJECT_ROOT / config["paths"]["catalog"])
    connection = psycopg.connect(
        host=db["host"],
        port=db["port"],
        dbname=db["dbname"],
        user=args.db_user,
        connect_timeout=db.get("connect_timeout_seconds", 5),
        application_name="meddb-catalog-builder",
    )
    try:
        tables, columns, primary_keys, foreign_keys = fetch_catalog_source(
            connection, schemas
        )
        excluded_patterns = [
            re.compile(str(pattern), re.IGNORECASE)
            for pattern in config["security"].get(
                "catalog_excluded_table_patterns", []
            )
        ]
        tables, columns, primary_keys, foreign_keys = filter_catalog_source(
            tables,
            columns,
            primary_keys,
            foreign_keys,
            excluded_patterns,
        )
        labels = load_database_labels(
            connection,
            load_inventory(DEFAULT_BRFSS_INVENTORY),
            load_inventory(DEFAULT_NHANES_INVENTORY),
            load_seer8_roles(SEER8_ROLES),
        )
    finally:
        connection.close()

    build_catalog(
        output.resolve(),
        tables,
        columns,
        primary_keys,
        foreign_keys,
        labels,
        metadata_only_schemas,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output.resolve()),
                "tables": len(tables),
                "columns": len(columns),
                "relationships": len(foreign_keys),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
