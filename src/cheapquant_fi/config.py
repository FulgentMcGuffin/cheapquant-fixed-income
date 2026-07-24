"""Project settings for databases, semantics, and cache paths."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "cqfi.yaml"
DEFAULT_RUNTIME_CONFIG_DIR = Path.home() / ".cqfi"
DEFAULT_RUNTIME_CONFIG_PATH = DEFAULT_RUNTIME_CONFIG_DIR / "cqfi_runtime.json"

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
_runtime_settings: RuntimeSettings | None = None


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


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in ("1", "true", "yes", "on")


@dataclass
class RuntimeSettings:
    """Mutable application settings that can change during a session.

    Persisted under the user home directory at ``~/.cqfi/cqfi_runtime.json``
    (see :data:`DEFAULT_RUNTIME_CONFIG_PATH`). Path/database locations stay in
    ``cqfi.yaml``; this file holds session/cache behaviour toggles.
    """

    use_quant_cache: bool = False
    save_quant_cache_to_bond_analytics_after_session: bool = False
    clear_quant_cache_after_session: bool = False
    restore_quant_cache_at_session_start: bool = False

    def update(self, **kwargs: object) -> None:
        """Update known fields in place (unknown keys are ignored)."""
        known = {f.name for f in fields(self)}
        for key, value in kwargs.items():
            if key not in known:
                continue
            setattr(self, key, _parse_bool(value) if isinstance(getattr(self, key), bool) else value)

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


def _runtime_defaults() -> RuntimeSettings:
    return RuntimeSettings()


def _resolve_runtime_config_path(path: str | Path | None = None) -> Path:
    """Resolve the runtime JSON path (default: ``~/.cqfi/cqfi_runtime.json``)."""
    if path is None:
        env = os.environ.get("CQFI_RUNTIME_CONFIG")
        config_path = Path(env) if env else DEFAULT_RUNTIME_CONFIG_PATH
    else:
        config_path = Path(path)
    config_path = config_path.expanduser()
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()
    return config_path


def load_runtime_settings(
    path: str | Path | None = None,
    *,
    create_if_missing: bool = True,
) -> RuntimeSettings:
    """Load runtime settings from JSON; create dir/file with all-false defaults if missing."""
    global _runtime_settings
    config_path = _resolve_runtime_config_path(path)

    if not config_path.exists():
        settings = _runtime_defaults()
        if create_if_missing:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            save_runtime_settings(settings, config_path)
        _runtime_settings = settings
        return settings

    with config_path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Runtime config {config_path} must be a JSON object.")

    defaults = _runtime_defaults()
    defaults.update(**data)
    _runtime_settings = defaults
    return defaults


def save_runtime_settings(
    settings: RuntimeSettings | None = None,
    path: str | Path | None = None,
) -> Path:
    """Persist runtime settings to JSON and keep the in-memory copy in sync."""
    global _runtime_settings
    settings = settings or get_runtime_settings()
    config_path = _resolve_runtime_config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(settings.as_dict(), fh, indent=2)
        fh.write("\n")
    _runtime_settings = settings
    return config_path


def get_runtime_settings() -> RuntimeSettings:
    """Return the active runtime settings, loading (or creating) the JSON file if needed."""
    global _runtime_settings
    if _runtime_settings is None:
        load_runtime_settings()
    assert _runtime_settings is not None
    return _runtime_settings


@dataclass(frozen=True)
class DatasetConfig:
    """A single MCP-queryable dataset: a database plus its semantic profile.

    Registering a new dataset for the LLM to query is purely a config change —
    add an entry (built-in path keys, or a ``datasets:`` block in
    ``cqfi.yaml``) and it becomes routable/queryable with no code changes.
    """

    db_path: Path
    semantics_dir: Path
    dataset: str
    keywords: tuple[str, ...] = ()


# Default natural-language routing hints for the three built-in datasets.
_INPUT_KEYWORDS = (
    "zero rate", "par rate", "spotfx", "window_corr", "correlation", "spread",
    "slope", "ycs_data", "treasury curve", "yield curve",
)
_CACHE_KEYWORDS = (
    "cmt", "clean price", "cached", "pricing run", "calculation_log",
    "quantlib", "we computed", "we priced", "session",
)
_BOND_ANALYTICS_KEYWORDS = (
    "bond", "duration", "convexity", "z-spread", "asw", "carry", "roll",
    "tenor pillar", "bond_universe", "bond_analytics", "cmt_analytics",
    "isin", "market", "/mctx", "/bond",
)


@dataclass(frozen=True)
class AppSettings:
    """Resolved runtime paths and dataset names."""

    config_path: Path
    ycs_db_path: Path
    ycs_semantics_dir: Path
    bond_analytics_db_path: Path
    bond_analytics_semantics_dir: Path
    bond_analytics_semantics_path: Path
    quant_cache_db_path: Path
    quant_cache_semantics_dir: Path
    sessions_dir: Path
    mcp_datasets: dict[str, DatasetConfig]
    write_to_bond_analytics_db: bool = False

    @property
    def ycs_dataset(self) -> str:
        return self.ycs_db_path.stem

    @property
    def cache_dataset(self) -> str:
        return "quant_cache"

    @property
    def bond_analytics_dataset(self) -> str:
        return self.bond_analytics_db_path.stem

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

        settings = data.get("settings") or {}
        if not isinstance(settings, dict):
            raise ValueError(f"Config {config_path} 'settings' must be a mapping.")

        def _get(key: str, env_key: str, default: str | None = None) -> str:
            return os.environ.get(env_key) or str(paths.get(key, default or ""))

        def _get_bool(key: str, env_key: str, default: bool = False) -> bool:
            env_val = os.environ.get(env_key)
            if env_val is not None and env_val.strip():
                return _parse_bool(env_val)
            yaml_val = settings.get(key)
            if yaml_val is not None:
                return _parse_bool(yaml_val)
            return default

        ycs_db = _get("ycs_db", "CQFI_YCS_DB")
        ycs_semantics = _get("ycs_semantics", "CQFI_YCS_SEMANTICS")
        bond_analytics_db = _get("bond_analytics_db", "CQFI_BOND_ANALYTICS_DB")
        bond_analytics_semantics = _get(
            "bond_analytics_semantics", "CQFI_BOND_ANALYTICS_SEMANTICS"
        )
        quant_cache_db = _get("quant_cache_db", "CQFI_QUANT_CACHE_DB")
        quant_cache_semantics = _get(
            "quant_cache_semantics", "CQFI_QUANT_CACHE_SEMANTICS"
        )
        sessions = _get("sessions_dir", "CQFI_SESSIONS_DIR")
        write_to_bond_analytics_db = _get_bool(
            "write_to_bond_analytics_db", "CQFI_WRITE_TO_BOND_ANALYTICS_DB"
        )

        missing = [
            name
            for name, val in [
                ("ycs_db", ycs_db),
                ("ycs_semantics", ycs_semantics),
                ("bond_analytics_db", bond_analytics_db),
                ("bond_analytics_semantics", bond_analytics_semantics),
                ("quant_cache_db", quant_cache_db),
                ("quant_cache_semantics", quant_cache_semantics),
                ("sessions_dir", sessions),
            ]
            if not val
        ]
        if missing:
            raise ValueError(
                f"Config {config_path} is missing required path(s): {', '.join(missing)}"
            )

        ycs_db_path = _resolve_path(ycs_db)
        bond_analytics_db_path = _resolve_path(bond_analytics_db)
        quant_cache_db_path = _resolve_path(quant_cache_db)
        quant_cache_semantics_dir = _semantics_dir_from_value(quant_cache_semantics)
        bond_analytics_semantics_dir = _semantics_dir_from_value(bond_analytics_semantics)
        bond_analytics_semantics_path = _resolve_path(bond_analytics_semantics)

        mcp_datasets: dict[str, DatasetConfig] = {
            "input": DatasetConfig(
                db_path=ycs_db_path,
                semantics_dir=_semantics_dir_from_value(ycs_semantics),
                dataset=ycs_db_path.stem,
                keywords=_INPUT_KEYWORDS,
            ),
            "cache": DatasetConfig(
                db_path=quant_cache_db_path,
                semantics_dir=quant_cache_semantics_dir,
                dataset="quant_cache",
                keywords=_CACHE_KEYWORDS,
            ),
            "bond_analytics": DatasetConfig(
                db_path=bond_analytics_db_path,
                semantics_dir=bond_analytics_semantics_dir,
                dataset=bond_analytics_db_path.stem,
                keywords=_BOND_ANALYTICS_KEYWORDS,
            ),
        }

        extra_datasets = data.get("datasets") or {}
        if not isinstance(extra_datasets, dict):
            raise ValueError(f"Config {config_path} 'datasets' must be a mapping.")
        for name, entry in extra_datasets.items():
            if not isinstance(entry, dict) or not entry.get("db") or not entry.get("semantics"):
                raise ValueError(
                    f"Config {config_path} dataset {name!r} needs 'db' and 'semantics'."
                )
            db_path = _resolve_path(entry["db"])
            mcp_datasets[name] = DatasetConfig(
                db_path=db_path,
                semantics_dir=_semantics_dir_from_value(entry["semantics"]),
                dataset=db_path.stem,
                keywords=tuple(entry.get("keywords", ())),
            )

        return cls(
            config_path=config_path,
            ycs_db_path=ycs_db_path,
            ycs_semantics_dir=_semantics_dir_from_value(ycs_semantics),
            bond_analytics_db_path=bond_analytics_db_path,
            bond_analytics_semantics_dir=bond_analytics_semantics_dir,
            bond_analytics_semantics_path=bond_analytics_semantics_path,
            quant_cache_db_path=quant_cache_db_path,
            quant_cache_semantics_dir=quant_cache_semantics_dir,
            sessions_dir=_resolve_path(sessions),
            mcp_datasets=mcp_datasets,
            write_to_bond_analytics_db=write_to_bond_analytics_db,
        )

    def ensure_dirs(self) -> None:
        self.quant_cache_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.bond_analytics_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.quant_cache_semantics_dir.mkdir(parents=True, exist_ok=True)


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
