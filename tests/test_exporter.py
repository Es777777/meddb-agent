from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meddb.errors import MedDBError
from meddb.exporter import resolve_output_path, write_output
from meddb.models import QueryResult


class ExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.result = QueryResult(
            query_id="q_test",
            columns=["类别", "数量"],
            rows=[("A", 2), ("B", None)],
            elapsed_ms=3,
            truncated=False,
            plan_total_cost=1.0,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_csv_export_uses_excel_friendly_utf8(self) -> None:
        path = write_output(self.result, self.root, "result", "csv")
        self.assertEqual(path.suffix, ".csv")
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("类别,数量", path.read_text(encoding="utf-8-sig"))

    def test_relative_path_stays_inside_export_root(self) -> None:
        path = resolve_output_path(self.root, "nested/result", "json")
        self.assertEqual(path, self.root / "nested" / "result.json")

    def test_rejects_parent_directory_escape(self) -> None:
        with self.assertRaises(MedDBError):
            resolve_output_path(self.root, "../outside.csv", "csv")

    def test_rejects_absolute_path_outside_export_root(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(MedDBError):
                resolve_output_path(self.root, str(Path(other) / "x.csv"), "csv")


if __name__ == "__main__":
    unittest.main()

