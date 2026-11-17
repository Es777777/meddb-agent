from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from meddb.config import Settings
from meddb.errors import QueryError, SecurityError
from meddb.models import QueryResult, ValidationResult
from meddb.security import validate_parameter_values


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self) -> psycopg.Connection[Any]:
        db = self.settings.database
        try:
            return psycopg.connect(
                host=db["host"],
                port=int(db["port"]),
                dbname=db["dbname"],
                user=db["user"],
                connect_timeout=int(db.get("connect_timeout_seconds", 5)),
                application_name=db.get("application_name", "meddb-cli"),
                row_factory=tuple_row,
            )
        except psycopg.Error as exc:
            raise QueryError(f"无法连接PostgreSQL: {exc}") from exc

    def status(self) -> dict[str, Any]:
        sql = """
            SELECT current_database(),
                   current_user,
                   current_setting('default_transaction_read_only'),
                   current_setting('statement_timeout'),
                   version()
        """
        try:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    row = cursor.fetchone()
        except psycopg.Error as exc:
            raise QueryError(f"数据库状态检查失败: {exc}") from exc
        assert row is not None
        return {
            "database": row[0],
            "user": row[1],
            "default_transaction_read_only": row[2],
            "statement_timeout": row[3],
            "server_version": row[4],
        }

    @staticmethod
    def _plan_total_cost(plan_value: Any) -> float | None:
        try:
            if isinstance(plan_value, str):
                import json

                plan_value = json.loads(plan_value)
            return float(plan_value[0]["Plan"]["Total Cost"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def execute(
        self,
        validation: ValidationResult,
        params: Mapping[str, Any],
        *,
        detail: bool,
        explain_only: bool = False,
    ) -> QueryResult:
        supplied = validate_parameter_values(validation.parameter_names, params)
        policy = self.settings.security
        max_rows = int(
            policy["detail_max_rows"] if detail else policy["aggregate_max_rows"]
        )
        statement_timeout = int(self.settings.database["statement_timeout_ms"])
        lock_timeout = int(self.settings.database["lock_timeout_ms"])
        max_cost = float(policy["max_plan_total_cost"])
        query_id = f"q_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        plan_cost: float | None = None

        try:
            with self.connect() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                        cursor.execute(
                            f"SET LOCAL statement_timeout = '{statement_timeout}ms'"
                        )
                        cursor.execute(f"SET LOCAL lock_timeout = '{lock_timeout}ms'")
                        cursor.execute(
                            f"EXPLAIN (FORMAT JSON) {validation.normalized_sql}",
                            supplied,
                        )
                        plan_row = cursor.fetchone()
                        plan_cost = (
                            self._plan_total_cost(plan_row[0]) if plan_row else None
                        )
                        if plan_cost is not None and plan_cost > max_cost:
                            raise SecurityError(
                                "查询计划成本超过限制: "
                                f"{plan_cost:.0f} > {max_cost:.0f}"
                            )
                        if explain_only:
                            elapsed = int((time.perf_counter() - started) * 1000)
                            return QueryResult(
                                query_id=query_id,
                                columns=["plan_total_cost"],
                                rows=[(plan_cost,)],
                                elapsed_ms=elapsed,
                                truncated=False,
                                plan_total_cost=plan_cost,
                            )

                        limited_sql = (
                            "SELECT * FROM ("
                            f"{validation.normalized_sql}"
                            f") AS meddb_result LIMIT {max_rows + 1}"
                        )
                        cursor.execute(limited_sql, supplied)
                        columns = [
                            item.name for item in cursor.description or ()
                        ]
                        fetched = cursor.fetchall()
                        truncated = len(fetched) > max_rows
                        rows = fetched[:max_rows]
        except SecurityError:
            raise
        except psycopg.Error as exc:
            raise QueryError(f"查询执行失败: {exc}") from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        warnings = ["结果已达到行数上限并被截断"] if truncated else []
        return QueryResult(
            query_id=query_id,
            columns=columns,
            rows=rows,
            elapsed_ms=elapsed,
            truncated=truncated,
            plan_total_cost=plan_cost,
            warnings=warnings,
        )

