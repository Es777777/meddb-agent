from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from meddb.cli import _emit_result
from meddb.config import Settings
from meddb.errors import MedDBError
from meddb.models import QueryResult


class CodexSafeOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.exports = self.root / "exports"
        self.exports.mkdir()
        self.settings = Settings(
            project_root=self.root,
            source_path=self.root / "config.json",
            database={"user": "meddb_reader"},
            security={
                "codex_aggregate_max_rows": 200,
                "codex_max_output_bytes": 1048576,
            },
            paths={
                "exports": self.exports,
                "audit": self.root / "audit.jsonl",
                "catalog": self.root / "catalog.sqlite",
                "templates": self.root / "templates",
            },
            codex={},
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def result(rows: list[tuple[object, ...]]) -> QueryResult:
        return QueryResult(
            query_id="q_safe_test",
            columns=["value"],
            rows=rows,
            elapsed_ms=2,
            truncated=False,
            plan_total_cost=1.0,
        )

    def emit(self, result: QueryResult, *, detail: bool = False) -> tuple[str | None, str]:
        stream = io.StringIO()
        with mock.patch.dict("os.environ", {"MEDDB_CODEX_SAFE": "1"}):
            with redirect_stdout(stream):
                path = _emit_result(
                    result,
                    self.settings,
                    output_format="json",
                    output=None,
                    clipboard=False,
                    detail=detail,
                )
        return path, stream.getvalue()

    def test_small_aggregate_can_enter_codex_output(self) -> None:
        path, output = self.emit(self.result([("aggregate",)]))
        self.assertIsNone(path)
        self.assertIn("aggregate", output)

    def test_more_than_200_rows_is_export_only(self) -> None:
        path, output = self.emit(self.result([(index,) for index in range(201)]))
        self.assertIsNotNone(path)
        metadata = json.loads(output)
        self.assertEqual(metadata["result_visibility"], "export_only")
        self.assertEqual(metadata["returned_rows"], 201)
        self.assertTrue(Path(path or "").exists())
        self.assertNotIn('"rows"', output)

    def test_detail_is_export_only_even_for_one_row(self) -> None:
        path, output = self.emit(self.result([("sensitive-detail",)]), detail=True)
        self.assertIsNotNone(path)
        self.assertNotIn("sensitive-detail", output)

    def test_clipboard_is_blocked(self) -> None:
        with mock.patch.dict("os.environ", {"MEDDB_CODEX_SAFE": "1"}):
            with self.assertRaises(MedDBError):
                _emit_result(
                    self.result([("x",)]),
                    self.settings,
                    output_format="json",
                    output=None,
                    clipboard=True,
                    detail=False,
                )


if __name__ == "__main__":
    unittest.main()
