from __future__ import annotations

import json
import unittest
from pathlib import Path

from meddb.errors import SecurityError
from meddb.security import validate_parameter_values, validate_sql


ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads(
    (ROOT / "config" / "meddb.json").read_text(encoding="utf-8")
)["security"]
MIMIC_POLICY = json.loads(
    (ROOT / "config" / "meddb-mimic-local.json").read_text(encoding="utf-8")
)["security"]


class SecurityTests(unittest.TestCase):
    def test_accepts_parameterized_aggregate(self) -> None:
        result = validate_sql(
            """
            SELECT diagnosis_year, COUNT(*) AS case_count
              FROM seer8.tumors
             WHERE diagnosis_year BETWEEN :start_year AND :end_year
             GROUP BY diagnosis_year
            """,
            POLICY,
        )
        self.assertEqual(result.tables, ("seer8.tumors",))
        self.assertEqual(result.parameter_names, ("end_year", "start_year"))
        self.assertIn("%(start_year)s", result.normalized_sql)
        self.assertNotIn("%%(start_year)s", result.normalized_sql)
        self.assertTrue(result.is_aggregate)

    def test_escapes_literal_percent_for_psycopg(self) -> None:
        result = validate_sql(
            """
            SELECT COUNT(*)
              FROM nhanes.variable_dictionary
             WHERE variable_name ILIKE '%' || :search_text || '%'
            """,
            POLICY,
        )
        self.assertIn("'%%'", result.normalized_sql)
        self.assertIn("%(search_text)s", result.normalized_sql)

    def test_rejects_write_statement(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql("DELETE FROM seer8.tumors", POLICY)

    def test_rejects_multiple_statements(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql(
                "SELECT COUNT(*) FROM seer8.tumors; "
                "SELECT COUNT(*) FROM seer12.tumors",
                POLICY,
            )

    def test_rejects_unqualified_table(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql("SELECT COUNT(*) FROM tumors", POLICY)

    def test_rejects_select_star(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql("SELECT * FROM seer8.tumors", POLICY, detail=True)

    def test_allows_count_star(self) -> None:
        result = validate_sql("SELECT COUNT(*) FROM seer8.tumors", POLICY)
        self.assertTrue(result.is_aggregate)

    def test_rejects_raw_table(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql("SELECT COUNT(*) FROM seer8.raw", POLICY)

    def test_rejects_metadata_only_schema(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql("SELECT COUNT(*) FROM nhanes_raw.demo_j", POLICY)

    def test_rejects_dangerous_function(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql(
                "SELECT pg_read_file('/tmp/example') FROM seer8.tumors LIMIT 1",
                POLICY,
                detail=True,
            )

    def test_requires_aggregate_by_default(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql("SELECT id FROM seer8.tumors", POLICY)

    def test_detail_mode_allows_explicit_columns(self) -> None:
        result = validate_sql(
            "SELECT id, diagnosis_year FROM seer8.tumors",
            POLICY,
            detail=True,
        )
        self.assertFalse(result.is_aggregate)

    def test_parameter_set_must_match(self) -> None:
        with self.assertRaises(SecurityError):
            validate_parameter_values(("start_year",), {})
        with self.assertRaises(SecurityError):
            validate_parameter_values(("start_year",), {"start_year": 2020, "x": 1})

    def test_mimic_policy_accepts_source_table(self) -> None:
        result = validate_sql(
            "SELECT COUNT(*) FROM mimiciv_hosp.admissions", MIMIC_POLICY
        )
        self.assertEqual(result.tables, ("mimiciv_hosp.admissions",))

    def test_mimic_policy_rejects_governance_table(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql(
                "SELECT COUNT(*) FROM mimiciv_hosp.import_manifest",
                MIMIC_POLICY,
            )

    def test_mimic_policy_rejects_precision_backup(self) -> None:
        with self.assertRaises(SecurityError):
            validate_sql(
                "SELECT COUNT(*) "
                "FROM mimiciv_icu.procedureevents_pre_precision_fix",
                MIMIC_POLICY,
            )


if __name__ == "__main__":
    unittest.main()
