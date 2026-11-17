from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_catalog.py"
SPEC = importlib.util.spec_from_file_location("build_catalog", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildCatalogTests(unittest.TestCase):
    def test_uses_schemas_from_security_policy(self) -> None:
        schemas, metadata_only = MODULE.configured_schemas(
            {
                "security": {
                    "allowed_schemas": ["mimiciii", "mimiciv_hosp"],
                    "metadata_only_schemas": ["mimiciv_icu_meta"],
                }
            }
        )
        self.assertEqual(
            schemas, ("mimiciii", "mimiciv_hosp", "mimiciv_icu_meta")
        )
        self.assertEqual(metadata_only, {"mimiciv_icu_meta"})

    def test_deduplicates_schema_names(self) -> None:
        schemas, _ = MODULE.configured_schemas(
            {
                "security": {
                    "allowed_schemas": ["mimiciii"],
                    "metadata_only_schemas": ["mimiciii"],
                }
            }
        )
        self.assertEqual(schemas, ("mimiciii",))

    def test_rejects_invalid_schema_name(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.configured_schemas(
                {"security": {"allowed_schemas": ["mimiciii;drop schema x"]}}
            )

    def test_marks_configured_metadata_schema_as_metadata_only(self) -> None:
        self.assertEqual(
            MODULE.access_mode(
                "mimiciv_meta", "tables", {"mimiciv_meta"}
            ),
            "metadata_only",
        )

    def test_filters_excluded_tables_and_dependent_metadata(self) -> None:
        tables = [
            ("mimiciv_icu", "procedureevents", "table", 10),
            (
                "mimiciv_icu",
                "procedureevents_pre_precision_fix",
                "table",
                10,
            ),
        ]
        columns = [
            ("mimiciv_icu", "procedureevents", 1, "id", "bigint", False),
            (
                "mimiciv_icu",
                "procedureevents_pre_precision_fix",
                1,
                "id",
                "bigint",
                False,
            ),
        ]
        filtered = MODULE.filter_catalog_source(
            tables,
            columns,
            [],
            [],
            [re.compile(r"_pre_precision_fix$", re.IGNORECASE)],
        )
        self.assertEqual(filtered[0], [tables[0]])
        self.assertEqual(filtered[1], [columns[0]])


if __name__ == "__main__":
    unittest.main()
