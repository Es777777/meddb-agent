from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from meddb.errors import CatalogError, SecurityError
from meddb.security import validate_identifier


class Catalog:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise CatalogError(
                f"catalog尚未生成: {self.path}\n"
                "请由管理员运行 scripts/build_catalog.py。"
            )
        # The runtime catalog is metadata-only and must never be mutated by a
        # query process.  Opening it with SQLite's explicit read-only URI also
        # avoids requesting write/journal access in a restricted Codex sandbox.
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def metadata(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM catalog_meta ORDER BY key"
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def datasets(self) -> list[dict[str, Any]]:
        sql = """
            SELECT schema_name,
                   COUNT(*) AS table_count,
                   SUM(CASE WHEN access_mode = 'query' THEN 1 ELSE 0 END)
                     AS query_table_count,
                   SUM(CASE WHEN access_mode = 'metadata_only' THEN 1 ELSE 0 END)
                     AS metadata_only_count
              FROM db_tables
             GROUP BY schema_name
             ORDER BY schema_name
        """
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(sql).fetchall()]

    def list_tables(
        self, schema_name: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if schema_name:
            validate_identifier(schema_name, "schema")
            where = "WHERE schema_name = ?"
            params.append(schema_name)
        params.append(limit)
        sql = f"""
            SELECT schema_name, table_name, table_type, estimated_rows,
                   access_mode, primary_key, description_cn
              FROM db_tables
              {where}
             ORDER BY schema_name, table_name
             LIMIT ?
        """
        with self._connect() as connection:
            return [
                dict(row) for row in connection.execute(sql, params).fetchall()
            ]

    def show_table(self, qualified_name: str) -> dict[str, Any]:
        if "." not in qualified_name:
            raise CatalogError("表名必须写成schema.table")
        schema_name, table_name = qualified_name.split(".", 1)
        validate_identifier(schema_name, "schema")
        validate_identifier(table_name, "表名")
        with self._connect() as connection:
            table = connection.execute(
                """
                SELECT * FROM db_tables
                 WHERE schema_name = ? AND table_name = ?
                """,
                (schema_name, table_name),
            ).fetchone()
            if not table:
                raise CatalogError(f"catalog中不存在表: {qualified_name}")
            columns = connection.execute(
                """
                SELECT ordinal_position, column_name, data_type, is_nullable,
                       is_primary_key, foreign_key_target,
                       english_label, chinese_label
                  FROM db_columns
                 WHERE schema_name = ? AND table_name = ?
                 ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            ).fetchall()
            relationships = connection.execute(
                """
                SELECT constraint_name, source_columns, target_schema,
                       target_table, target_columns
                  FROM db_relationships
                 WHERE source_schema = ? AND source_table = ?
                 ORDER BY constraint_name
                """,
                (schema_name, table_name),
            ).fetchall()
        return {
            "table": dict(table),
            "columns": [dict(row) for row in columns],
            "relationships": [dict(row) for row in relationships],
        }

    def search(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        words = [word.strip() for word in query.split() if word.strip()]
        if not words:
            raise CatalogError("搜索词不能为空")
        fts_query = " AND ".join(f'"{word.replace(chr(34), "")}"*' for word in words)
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT kind, schema_name, table_name, column_name,
                           english_label, chinese_label, rank
                      FROM search_index
                     WHERE search_index MATCH ?
                     ORDER BY rank
                     LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                like = f"%{query}%"
                rows = connection.execute(
                    """
                    SELECT kind, schema_name, table_name, column_name,
                           english_label, chinese_label, 0 AS rank
                      FROM search_fallback
                     WHERE searchable_text LIKE ?
                     ORDER BY schema_name, table_name, column_name
                     LIMIT ?
                    """,
                    (like, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def has_column(self, schema_name: str, table_name: str, column_name: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM db_columns
                 WHERE schema_name = ? AND table_name = ? AND column_name = ?
                """,
                (schema_name, table_name, column_name),
            ).fetchone()
        return row is not None

    def table_access_mode(self, schema_name: str, table_name: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT access_mode FROM db_tables
                 WHERE schema_name = ? AND table_name = ?
                """,
                (schema_name, table_name),
            ).fetchone()
        return str(row["access_mode"]) if row else None

    def validate_query_table(self, schema_name: str, table_name: str) -> None:
        mode = self.table_access_mode(schema_name, table_name)
        if mode is None:
            raise SecurityError(f"catalog中不存在表: {schema_name}.{table_name}")
        if mode != "query":
            raise SecurityError(f"该表只允许查看结构: {schema_name}.{table_name}")

    def columns_for_table(
        self, schema_name: str, table_name: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT column_name, data_type, english_label, chinese_label
                  FROM db_columns
                 WHERE schema_name = ? AND table_name = ?
                 ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            ).fetchall()
        return [dict(row) for row in rows]
