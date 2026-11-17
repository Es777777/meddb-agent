"""Mandatory research policy around the existing MedDB core."""
from __future__ import annotations

import json
from typing import Any

import sqlglot
from sqlglot import exp

from meddb.audit import AuditLog
from meddb.catalog import Catalog
from meddb.config import Settings
from meddb.db import Database
from meddb.errors import SecurityError
from meddb.models import QueryResult
from meddb.security import validate_parameter_values, validate_sql
from medresearch.contracts import validate_proposal


SCHEMAS = {"mimiciv_hosp", "mimiciv_icu"}


class MedDBTool:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings)
        self.catalog = Catalog(settings.paths["catalog"])

    def preflight(self, required_schemas: set[str] | None = None) -> dict[str, Any]:
        required_schemas = required_schemas or SCHEMAS
        allowed = self.settings.security.get("codex_safe_database_users", ["meddb_reader"])
        if not isinstance(allowed, list) or not allowed or any(not isinstance(x, str) for x in allowed):
            raise SecurityError("Invalid explicit reader allowlist")
        if self.settings.database["user"] not in allowed:
            raise SecurityError("Configured user is not an explicitly allowed reader")
        with self.database.connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute("""SELECT current_database(), current_user,
                        current_setting('transaction_read_only'), rolsuper,
                        rolcreaterole, rolcreatedb, rolbypassrls
                        FROM pg_catalog.pg_roles WHERE rolname = current_user""")
                    row = cursor.fetchone()
                    if not row or row[0] != self.settings.database["dbname"] or row[1] not in allowed:
                        raise SecurityError("Connected database/role differs from configuration")
                    if row[2] != "on" or any(row[3:]):
                        raise SecurityError("A non-privileged read-only execution role is required")
                    cursor.execute("SELECT schema_name FROM information_schema.schemata")
                    present = {x[0] for x in cursor.fetchall()}
                    cursor.execute("""SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = ANY(%s)
                        AND (has_table_privilege(format('%I.%I', table_schema, table_name), 'INSERT')
                          OR has_table_privilege(format('%I.%I', table_schema, table_name), 'UPDATE')
                          OR has_table_privilege(format('%I.%I', table_schema, table_name), 'DELETE')
                          OR has_table_privilege(format('%I.%I', table_schema, table_name), 'TRUNCATE'))""",
                        (sorted(required_schemas),))
                    if cursor.fetchone()[0]:
                        raise SecurityError("Reader has write privileges on research tables")
        if not required_schemas <= present:
            raise SecurityError("Required dataset schemas are not accessible")
        metadata = self.catalog.metadata()
        return {"database": row[0], "user": row[1], "transaction_read_only": True,
                "schemas": sorted(required_schemas), "catalog_metadata": metadata,
                "dataset_version_verified": False}

    def execute(self, proposal: dict[str, Any], *, explain_only: bool = False) -> QueryResult:
        validate_proposal(proposal)
        if proposal["status"] != "ready":
            raise SecurityError("Only ready proposals may execute")
        sql = proposal["sql"]
        tree = sqlglot.parse_one(sql, read="postgres")
        if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise SecurityError("The root statement must be a SELECT query")
        if tree.find(exp.Into) is not None or tree.find(exp.Lock) is not None:
            raise SecurityError("SELECT INTO and row locking are not research queries")
        # Existing aggregate detection permits aggregates in inner CTEs. Require
        # an aggregate/group in the final SELECT too, and forbid identifier or
        # free-text columns. This conservative contract intentionally rejects
        # some valid benchmark queries rather than returning clinical records.
        if not isinstance(tree, exp.Select):
            raise SecurityError("Research output requires a single final SELECT")
        aggregate_types = getattr(exp, "AggFunc")
        local_aggregates = [node for node in tree.find_all(aggregate_types)
                            if node.find_ancestor(exp.Select) is tree
                            and node.find_ancestor(exp.Window) is None]
        if not tree.args.get("group") and not local_aggregates:
            raise SecurityError("Final SELECT must aggregate; inner aggregates are insufficient")
        sensitive = {"subject_id", "hadm_id", "stay_id", "labevent_id", "charttime",
                     "admittime", "dischtime", "intime", "outtime", "dod", "text", "comments"}
        for selected in tree.expressions:
            if any(col.name.lower() in sensitive for col in selected.find_all(exp.Column)):
                if not isinstance(selected.unalias(), exp.Count):
                    raise SecurityError("Record identifiers/timestamps/text cannot be output dimensions")
        policy = dict(self.settings.security, allowed_schemas=sorted(SCHEMAS),
                      require_qualified_tables=True, require_aggregate_by_default=True,
                      block_select_star=True)
        validation = validate_sql(sql, policy)
        if not validation.tables:
            raise SecurityError("Research queries must reference catalogued MIMIC tables")
        for table in validation.tables:
            self.catalog.validate_query_table(*table.split(".", 1))
        params = json.loads(proposal["params_json"])
        validate_parameter_values(validation.parameter_names, params)
        self.preflight()
        result = self.database.execute(validation, params, detail=False, explain_only=explain_only)
        AuditLog(self.settings.paths["audit"]).record(
            result=result, validation=validation, params=params, command_type="research",
            template_id=None, sql_file=None, output_path=None,
            status="explain" if explain_only else "ok",
        )
        if not explain_only and result.columns != proposal["spec"]["output_columns"]:
            raise SecurityError("Result columns differ from the declared ResearchSpec")
        return result
