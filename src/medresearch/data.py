"""Local tabular intake and bounded PostgreSQL extraction through MedDB."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import sqlglot
from sqlglot import exp

from meddb.audit import AuditLog
from meddb.catalog import Catalog
from meddb.config import load_settings
from meddb.db import Database
from meddb.security import validate_parameter_values, validate_sql
from medresearch.contracts import digest
from medresearch.study_contract import DATASETS
from medresearch.tools import MedDBTool


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def relative_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return (base/path).resolve() if not path.is_absolute() else path.resolve()


def source_identity(spec: dict, base: Path) -> dict:
    source = spec["data"]
    if source["kind"] == "file":
        path = relative_path(source["path"], base)
        if path.suffix.lower() not in {".csv", ".json"} or not path.is_file():
            raise ValueError("Local analysis input must be a CSV or JSON record file")
        if path.stat().st_size > 128*1024*1024:
            raise ValueError("Local analysis input exceeds 128 MiB limit")
        return {"kind": "file", "source_sha256": hash_file(path), "bytes": path.stat().st_size,
                "dataset_version_verified": False}
    settings = load_settings(relative_path(source["config"], base))
    catalog = settings.paths["catalog"]
    return {"kind": "postgres", "configuration_sha256": digest({"database": settings.database,
        "security": settings.security}), "catalog_sha256": hash_file(catalog),
        "sql_sha256": digest(source["sql"]), "dataset_version_verified": False}


def load_data(spec: dict, base: Path) -> tuple[pd.DataFrame, dict]:
    source = spec["data"]
    if source["kind"] == "file":
        path = relative_path(source["path"], base)
        data = pd.read_csv(path, nrows=1_000_001) if path.suffix.lower() == ".csv" else pd.read_json(path)
        if len(data) > 1_000_000:
            raise ValueError("Local input exceeds one million rows")
        return data.reset_index(drop=True), {"kind": "file", "rows": len(data), "truncated": False}
    settings = load_settings(relative_path(source["config"], base))
    schemas = set(DATASETS[spec["dataset"]["name"]]["schemas"])
    if not schemas <= set(settings.security["allowed_schemas"]):
        raise ValueError("Dataset schemas exceed the configured allowlist")
    policy = dict(settings.security, allowed_schemas=sorted(schemas), require_qualified_tables=True,
                  block_select_star=True)
    tree = sqlglot.parse_one(source["sql"], read="postgres")
    if not isinstance(tree, exp.Select) or tree.find(exp.Into) is not None or tree.find(exp.Lock) is not None:
        raise ValueError("Local cohort extraction requires a single non-locking SELECT")
    validation = validate_sql(source["sql"], policy, detail=True)
    if not validation.tables:
        raise ValueError("Cohort extraction must reference catalogued dataset tables")
    validate_parameter_values(validation.parameter_names, source["params"])
    catalog = Catalog(settings.paths["catalog"])
    for table in validation.tables:
        catalog.validate_query_table(*table.split(".", 1))
    preflight = MedDBTool(settings).preflight(schemas)
    result = Database(settings).execute(validation, source["params"], detail=True)
    AuditLog(settings.paths["audit"]).record(result=result, validation=validation,
        params=source["params"], command_type="local-study-extract", template_id=None,
        sql_file=None, output_path=None)
    if result.truncated:
        raise ValueError("Cohort extraction was truncated; set an appropriate explicit detail_max_rows locally")
    if result.columns != source["output_columns"]:
        raise ValueError("Cohort columns differ from the declared extraction contract")
    return pd.DataFrame(result.rows, columns=result.columns), {
        "kind": "postgres", "query_id": result.query_id, "rows": result.returned_rows,
        "truncated": False, "preflight": preflight}
