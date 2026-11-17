from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import tomli


def discover_apps(plugin_cache: Path) -> dict[str, str]:
    apps: dict[str, str] = {}
    if not plugin_cache.exists():
        return apps
    for app_file in plugin_cache.rglob(".app.json"):
        try:
            value = json.loads(app_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for alias, item in value.get("apps", {}).items():
            apps[str(alias)] = str(item.get("id", ""))
    return apps


def configure(config_path: Path, profile_path: Path, plugin_cache: Path) -> dict:
    base_text = config_path.read_text(encoding="utf-8")
    base = tomli.loads(base_text)
    mcp_servers = sorted(str(name) for name in base.get("mcp_servers", {}))
    apps = discover_apps(plugin_cache)

    profile_lines = [
        "[features]",
        "js_repl = false",
    ]
    for name in mcp_servers:
        profile_lines.extend(["", f"[mcp_servers.{name}]", "enabled = false"])
    for alias in sorted(apps):
        profile_lines.extend(["", f"[apps.{alias}]", "enabled = false"])
    profile_lines.extend(
        [
            "",
            "[shell_environment_policy.set]",
            'MEDDB_CODEX_PROFILE = "no-mcp"',
            'MEDDB_CODEX_SAFE = "1"',
        ]
    )
    updated_profile = "\n".join(profile_lines) + "\n"
    tomli.loads(updated_profile)

    backup_path: Path | None = None
    if profile_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = profile_path.with_name(
            f"{profile_path.name}.backup_{timestamp}"
        )
        backup_path.write_bytes(profile_path.read_bytes())

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_temp = profile_path.with_suffix(profile_path.suffix + ".tmp")
    profile_temp.write_text(updated_profile, encoding="utf-8")
    profile_temp.replace(profile_path)

    if config_path.read_text(encoding="utf-8") != base_text:
        raise RuntimeError("Base Codex config changed unexpectedly")

    return {
        "status": "ok",
        "config_unchanged": str(config_path),
        "profile": str(profile_path),
        "backup": str(backup_path) if backup_path else None,
        "disabled_mcp_servers": mcp_servers,
        "disabled_apps": apps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--plugin-cache", type=Path, required=True)
    args = parser.parse_args()
    result = configure(
        args.config.resolve(),
        args.profile.resolve(),
        args.plugin_cache.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
