from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meddb.cli import dispatch
from meddb.config import Settings
from meddb.errors import MedDBError


class SafeDatabaseUserTests(unittest.TestCase):
    def settings(self, user: str, allowed: object = None) -> Settings:
        root = Path(tempfile.gettempdir())
        security = {}
        if allowed is not None:
            security["codex_safe_database_users"] = allowed
        return Settings(
            project_root=root,
            source_path=root / "config.json",
            database={"user": user},
            security=security,
            paths={},
            codex={
                "config": root / "config.toml",
                "plugin_cache": root / "plugins",
                "no_mcp_profile": root / "no-mcp.config.toml",
            },
        )

    @staticmethod
    def args() -> argparse.Namespace:
        return argparse.Namespace(command="query")

    def test_default_role_remains_meddb_reader(self) -> None:
        with mock.patch.dict(os.environ, {"MEDDB_CODEX_SAFE": "1"}, clear=False):
            with mock.patch("meddb.cli.audit_no_mcp") as audit:
                audit.return_value.ok = True
                with mock.patch("meddb.cli.command_query", return_value=0):
                    self.assertEqual(dispatch(self.args(), self.settings("meddb_reader")), 0)

    def test_explicit_mimic_reader_is_allowed(self) -> None:
        settings = self.settings("meddb_mimic_reader", ["meddb_mimic_reader"])
        with mock.patch.dict(os.environ, {"MEDDB_CODEX_SAFE": "1"}, clear=False):
            with mock.patch("meddb.cli.audit_no_mcp") as audit:
                audit.return_value.ok = True
                with mock.patch("meddb.cli.command_query", return_value=0):
                    self.assertEqual(dispatch(self.args(), settings), 0)

    def test_unlisted_role_is_rejected_before_query(self) -> None:
        settings = self.settings("postgres", ["meddb_mimic_reader"])
        with mock.patch.dict(os.environ, {"MEDDB_CODEX_SAFE": "1"}, clear=False):
            with self.assertRaisesRegex(MedDBError, "explicitly allowed read-only role"):
                dispatch(self.args(), settings)

    def test_invalid_allowlist_is_rejected(self) -> None:
        settings = self.settings("meddb_mimic_reader", "meddb_mimic_reader")
        with mock.patch.dict(os.environ, {"MEDDB_CODEX_SAFE": "1"}, clear=False):
            with self.assertRaisesRegex(MedDBError, "must be a non-empty string list"):
                dispatch(self.args(), settings)


if __name__ == "__main__":
    unittest.main()
