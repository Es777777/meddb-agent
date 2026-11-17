from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli


@dataclass(frozen=True)
class MCPAudit:
    ok: bool
    issues: tuple[str, ...]
    disabled_mcp_servers: tuple[str, ...]
    disabled_apps: tuple[str, ...]


def _load_toml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomli.load(handle)


def _merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def discover_plugin_apps(plugin_cache: Path) -> dict[str, str]:
    apps: dict[str, str] = {}
    if not plugin_cache.exists():
        return apps
    for app_file in plugin_cache.rglob(".app.json"):
        try:
            data = json.loads(app_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for alias, item in data.get("apps", {}).items():
            apps[str(alias)] = str(item.get("id", ""))
    return apps


def audit_no_mcp(
    config_path: Path,
    plugin_cache: Path,
    profile_path: Path | None = None,
) -> MCPAudit:
    effective = _merge_dicts(_load_toml(config_path), _load_toml(profile_path))
    issues: list[str] = []
    if os.environ.get("MEDDB_CODEX_PROFILE") != "no-mcp":
        issues.append("The active Codex session is not marked as no-mcp")

    disabled_servers: list[str] = []
    for name, server in effective.get("mcp_servers", {}).items():
        if isinstance(server, dict) and server.get("enabled") is False:
            disabled_servers.append(str(name))
        else:
            issues.append(f"MCP server is not disabled: {name}")

    configured_apps = effective.get("apps", {})
    discovered_apps = discover_plugin_apps(plugin_cache)
    disabled_apps: list[str] = []
    for alias in sorted(discovered_apps):
        app_config = configured_apps.get(alias, {})
        if isinstance(app_config, dict) and app_config.get("enabled") is False:
            disabled_apps.append(alias)
        else:
            issues.append(f"Connector app is not explicitly disabled: {alias}")

    features = effective.get("features", {})
    if features.get("js_repl") is not False:
        issues.append("features.js_repl is not disabled")

    environment = effective.get("shell_environment_policy", {}).get("set", {})
    if environment.get("MEDDB_CODEX_PROFILE") != "no-mcp":
        issues.append("The no-mcp profile lacks MEDDB_CODEX_PROFILE=no-mcp")
    if environment.get("MEDDB_CODEX_SAFE") != "1":
        issues.append("The no-mcp profile lacks MEDDB_CODEX_SAFE=1")

    return MCPAudit(
        ok=not issues,
        issues=tuple(issues),
        disabled_mcp_servers=tuple(sorted(disabled_servers)),
        disabled_apps=tuple(sorted(disabled_apps)),
    )
