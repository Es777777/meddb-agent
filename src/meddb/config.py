from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meddb.errors import ConfigError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "meddb.json"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    source_path: Path
    database: dict[str, Any]
    security: dict[str, Any]
    paths: dict[str, Path]
    codex: dict[str, Path]


def _resolve_path(root: Path, value: str) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    return expanded if expanded.is_absolute() else (root / expanded).resolve()


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(
        path or os.environ.get("MEDDB_CONFIG") or DEFAULT_CONFIG
    ).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取配置文件: {exc}") from exc

    for key in ("database", "security", "paths", "codex"):
        if key not in raw or not isinstance(raw[key], dict):
            raise ConfigError(f"配置缺少对象: {key}")

    root = PROJECT_ROOT
    paths = {key: _resolve_path(root, value) for key, value in raw["paths"].items()}
    codex = {key: _resolve_path(root, value) for key, value in raw["codex"].items()}
    return Settings(
        project_root=root,
        source_path=config_path,
        database=raw["database"],
        security=raw["security"],
        paths=paths,
        codex=codex,
    )


def ensure_runtime_directories(settings: Settings) -> None:
    settings.paths["exports"].mkdir(parents=True, exist_ok=True)
    settings.paths["audit"].parent.mkdir(parents=True, exist_ok=True)
    settings.paths["catalog"].parent.mkdir(parents=True, exist_ok=True)

