"""Project settings for databases, semantics, and cache paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "cqfi.yaml"

load_dotenv(PROJECT_ROOT / ".env", override=False)


def configure_langsmith() -> None:
    """Disable LangSmith run export unless the user explicitly opts in.

    LangChain may attempt to POST traces to api.smith.langchain.com when
    ``LANGCHAIN_TRACING_V2`` is enabled globally (e.g. in a conda base env) but
    no valid LangSmith API key is configured — resulting in noisy 403 errors
    while the actual LLM query still succeeds.

    Set ``CQFI_LANGSMITH=1`` in ``.env`` to keep tracing enabled for cqfi.
    """
    if os.environ.get("CQFI_LANGSMITH", "").strip().lower() in ("1", "true", "yes"):
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"


configure_langsmith()

_settings: AppSettings | None = None


def _resolve_path(value: str | Path, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def _semantics_dir_from_value(value: str | Path) -> Path:
    """Accept a semantics directory or a ``*.yaml`` profile path."""
    path = Path(value).expanduser()
    if path.suffix.lower() in (".yaml", ".yml"):
        return path.parent if path.is_absolute() else (PROJECT_ROOT / path).resolve().parent
    return _resolve_path(path)


@dataclass(frozen=True)
class AppSettings:
    """Resolved runtime paths and dataset names."""

    config_path: Path
    input_db_path: Path
    input_semantics_dir: Path
    cache_db_path: Path
    cache_semantics_dir: Path
    sessions_dir: Path

    @property
    def input_dataset(self) -> str:
        return self.input_db_path.stem

    @property
    def cache_dataset(self) -> str:
        return "quant_cache"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppSettings":
        config_path = Path(path).expanduser()
        if not config_path.is_absolute():
            config_path = (PROJECT_ROOT / config_path).resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with config_path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}

        paths = data.get("paths") or data
        if not isinstance(paths, dict):
            raise ValueError(f"Config {config_path} must contain a 'paths' mapping.")

        def _get(key: str, env_key: str, default: str | None = None) -> str:
            return os.environ.get(env_key) or str(paths.get(key, default or ""))

        input_db = _get("input_db", "CQFI_INPUT_DB")
        input_semantics = _get("input_semantics", "CQFI_INPUT_SEMANTICS")
        cache_db = _get("cache_db", "CQFI_CACHE_DB")
        cache_semantics = _get("cache_semantics_dir", "CQFI_CACHE_SEMANTICS")
        sessions = _get("sessions_dir", "CQFI_SESSIONS_DIR")

        missing = [
            name
            for name, val in [
                ("input_db", input_db),
                ("input_semantics", input_semantics),
                ("cache_db", cache_db),
                ("cache_semantics_dir", cache_semantics),
                ("sessions_dir", sessions),
            ]
            if not val
        ]
        if missing:
            raise ValueError(
                f"Config {config_path} is missing required path(s): {', '.join(missing)}"
            )

        return cls(
            config_path=config_path,
            input_db_path=_resolve_path(input_db),
            input_semantics_dir=_semantics_dir_from_value(input_semantics),
            cache_db_path=_resolve_path(cache_db),
            cache_semantics_dir=_resolve_path(cache_semantics),
            sessions_dir=_resolve_path(sessions),
        )

    def ensure_dirs(self) -> None:
        self.cache_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.cache_semantics_dir.mkdir(parents=True, exist_ok=True)


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    """Load settings from YAML and store as the active runtime configuration."""
    global _settings
    if config_path is None:
        config_path = os.environ.get("CQFI_CONFIG", DEFAULT_CONFIG_PATH)
    _settings = AppSettings.from_yaml(config_path)
    return _settings


def get_settings() -> AppSettings:
    """Return the active settings, loading the default config file if needed."""
    global _settings
    if _settings is None:
        load_settings()
    assert _settings is not None
    return _settings
