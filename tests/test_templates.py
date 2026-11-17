from __future__ import annotations

import json
import unittest
from pathlib import Path

from meddb.errors import SecurityError
from meddb.security import validate_sql
from meddb.templates import TemplateRegistry


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = json.loads(
    (ROOT / "config" / "meddb.json").read_text(encoding="utf-8")
)

SAMPLES = {
    "seer-case-trend": {
        "dataset": "seer8",
        "start_year": 2018,
        "end_year": 2020,
        "race": None,
    },
    "seer-site-distribution": {
        "dataset": "seer8",
        "start_year": 2018,
        "end_year": 2020,
        "race": None,
    },
    "seer-stage-distribution": {
        "dataset": "seer8",
        "start_year": 2018,
        "end_year": 2020,
    },
    "seer-survival-summary": {
        "dataset": "seer8",
        "start_year": 2018,
        "end_year": 2020,
    },
    "brfss-sample-counts": {
        "dataset": "brfss",
        "start_year": 2020,
        "end_year": 2021,
        "state_code": None,
    },
    "brfss-weighted-distribution": {
        "dataset": "brfss",
        "topic_table": "demographics",
        "variable_column": "sex",
        "weight_column": "llcp_weight",
        "start_year": 2020,
        "end_year": 2021,
    },
    "brfss-cross-tab": {
        "dataset": "brfss",
        "table_a": "demographics",
        "column_a": "sex",
        "table_b": "demographics",
        "column_b": "age_group",
        "start_year": 2020,
        "end_year": 2021,
    },
    "nhanes-participant-counts": {"dataset": "nhanes"},
    "nhanes-demographic-distribution": {
        "dataset": "nhanes",
        "demographic_column": "riagendr",
    },
    "nhanes-component-inventory": {"dataset": "nhanes"},
    "nhanes-variable-search": {
        "dataset": "nhanes",
        "search_text": "cancer",
    },
}


class SyntheticCatalog:
    """Strict synthetic metadata; no real schema catalog or patient records."""
    columns = {
        ("brfss", "demographics"): {"sex", "llcp_weight", "age_group"},
        ("brfss", "survey_design"): {"llcp_weight"},
        ("nhanes", "participants"): {"riagendr"},
    }

    def validate_query_table(self, schema_name, table_name):
        if (schema_name, table_name) not in self.columns:
            raise SecurityError("Unknown synthetic table")

    def has_column(self, schema_name, table_name, column_name):
        return column_name in self.columns.get((schema_name, table_name), set())


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = SyntheticCatalog()
        cls.registry = TemplateRegistry(
            ROOT / SETTINGS["paths"]["templates"],
            cls.catalog,
        )

    def test_synthetic_metadata_rejects_unknown_table_and_column(self):
        with self.assertRaises(SecurityError):
            self.catalog.validate_query_table("brfss", "unknown")
        self.assertFalse(self.catalog.has_column("brfss", "demographics", "unknown"))

    def test_every_template_has_a_validation_sample(self) -> None:
        template_ids = {item["id"] for item in self.registry.list()}
        self.assertEqual(template_ids, set(SAMPLES))

    def test_every_template_renders_and_passes_security_validation(self) -> None:
        for template_id, params in SAMPLES.items():
            with self.subTest(template_id=template_id):
                rendered = self.registry.render(template_id, params)
                validation = validate_sql(
                    rendered.sql,
                    SETTINGS["security"],
                    detail=rendered.detail,
                )
                self.assertTrue(validation.tables)
                self.assertEqual(
                    set(validation.parameter_names),
                    set(rendered.params),
                )


if __name__ == "__main__":
    unittest.main()

