from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meddb.mcp_guard import audit_no_mcp


class MCPGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.cache = self.root / "cache"
        app_dir = self.cache / "plugin"
        app_dir.mkdir(parents=True)
        (app_dir / ".app.json").write_text(
            json.dumps({"apps": {"example": {"id": "example@app"}}}),
            encoding="utf-8",
        )
        self.config = self.root / "config.toml"
        self.profile = self.root / "no-mcp.config.toml"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_accepts_layered_no_mcp_profile(self) -> None:
        self.config.write_text(
            '[mcp_servers.node_repl]\ncommand = "node"\n',
            encoding="utf-8",
        )
        self.profile.write_text(
            """
[features]
js_repl = false

[mcp_servers.node_repl]
enabled = false

[apps.example]
enabled = false

[shell_environment_policy.set]
MEDDB_CODEX_PROFILE = "no-mcp"
MEDDB_CODEX_SAFE = "1"
            """.strip(),
            encoding="utf-8",
        )
        with mock.patch.dict("os.environ", {"MEDDB_CODEX_PROFILE": "no-mcp"}):
            result = audit_no_mcp(self.config, self.cache, self.profile)
        self.assertTrue(result.ok)
        self.assertEqual(result.disabled_mcp_servers, ("node_repl",))
        self.assertEqual(result.disabled_apps, ("example",))

    def test_reports_enabled_or_missing_entries(self) -> None:
        self.config.write_text(
            """
[features]
js_repl = true

[mcp_servers.node_repl]
enabled = true
            """.strip(),
            encoding="utf-8",
        )
        result = audit_no_mcp(self.config, self.cache, self.profile)
        self.assertFalse(result.ok)
        self.assertTrue(any("MCP server is not disabled" in issue for issue in result.issues))
        self.assertTrue(any("Connector app" in issue for issue in result.issues))
        self.assertTrue(any("js_repl" in issue for issue in result.issues))
        self.assertTrue(any("not marked as no-mcp" in issue for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
