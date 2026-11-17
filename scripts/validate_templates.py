from __future__ import annotations

import json
from pathlib import Path

from meddb.catalog import Catalog
from meddb.config import load_settings
from meddb.db import Database
from meddb.security import validate_sql
from meddb.templates import TemplateRegistry


ROOT = Path(__file__).resolve().parents[1]

BASE_SAMPLES = {
    "seer-case-trend": {
        "start_year": 2018,
        "end_year": 2020,
        "race": None,
    },
    "seer-site-distribution": {
        "start_year": 2018,
        "end_year": 2020,
        "race": None,
    },
    "seer-stage-distribution": {
        "start_year": 2018,
        "end_year": 2020,
    },
    "seer-survival-summary": {
        "start_year": 2018,
        "end_year": 2020,
    },
    "brfss-sample-counts": {
        "start_year": 2020,
        "end_year": 2021,
        "state_code": None,
    },
    "brfss-weighted-distribution": {
        "topic_table": "demographics",
        "variable_column": "sex",
        "weight_column": "llcp_weight",
        "start_year": 2020,
        "end_year": 2021,
    },
    "brfss-cross-tab": {
        "table_a": "demographics",
        "column_a": "sex",
        "table_b": "demographics",
        "column_b": "age_group",
        "start_year": 2020,
        "end_year": 2021,
    },
    "nhanes-participant-counts": {},
    "nhanes-demographic-distribution": {
        "demographic_column": "riagendr",
    },
    "nhanes-component-inventory": {},
    "nhanes-variable-search": {
        "search_text": "cancer",
    },
}


def main() -> int:
    settings = load_settings()
    catalog = Catalog(settings.paths["catalog"])
    registry = TemplateRegistry(settings.paths["templates"], catalog)
    database = Database(settings)
    rows: list[dict[str, object]] = []

    for template in registry.list():
        template_id = template["id"]
        for dataset in template["datasets"]:
            supplied = dict(BASE_SAMPLES[template_id])
            supplied["dataset"] = dataset
            rendered = registry.render(template_id, supplied)
            validation = validate_sql(
                rendered.sql,
                settings.security,
                detail=rendered.detail,
            )
            result = database.execute(
                validation,
                rendered.params,
                detail=rendered.detail,
                explain_only=True,
            )
            rows.append(
                {
                    "template": template_id,
                    "dataset": dataset,
                    "status": "ok",
                    "plan_total_cost": result.plan_total_cost,
                }
            )

    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
