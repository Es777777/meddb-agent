from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import tomli


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "configure_no_mcp.py"
SPEC = importlib.util.spec_from_file_location("configure_no_mcp", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ConfigureNoMCPTests(unittest.TestCase):
    def test_configure_is_valid_idempotent_and_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            profile = root / "no-mcp.config.toml"
            cache = root / "cache"
            app_dir = cache / "plugin"
            app_dir.mkdir(parents=True)
            (app_dir / ".app.json").write_text(
                json.dumps({"apps": {"example": {"id": "example@app"}}}),
                encoding="utf-8",
            )
            original = """
model = "gpt-5"

[features]
js_repl = true

[mcp_servers.node_repl]
command = "node"

[mcp_servers.node_repl.env]
NODE_PATH = "example"
            """.strip() + "\n"
            config.write_text(original, encoding="utf-8")

            first = MODULE.configure(config, profile, cache)
            parsed = tomli.loads(config.read_text(encoding="utf-8"))
            parsed_profile = tomli.loads(profile.read_text(encoding="utf-8"))
            self.assertEqual(parsed["model"], "gpt-5")
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertFalse(parsed_profile["features"]["js_repl"])
            self.assertFalse(parsed_profile["mcp_servers"]["node_repl"]["enabled"])
            self.assertFalse(parsed_profile["apps"]["example"]["enabled"])
            self.assertEqual(
                parsed_profile["shell_environment_policy"]["set"]["MEDDB_CODEX_PROFILE"],
                "no-mcp",
            )
            self.assertIsNone(first["backup"])

            second = MODULE.configure(config, profile, cache)
            reparsed = tomli.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(reparsed["model"], "gpt-5")
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertTrue(Path(second["backup"]).exists())


if __name__ == "__main__":
    unittest.main()
