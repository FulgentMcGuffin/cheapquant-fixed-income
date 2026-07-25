"""Tests for YAML configuration loading."""

from __future__ import annotations

import json
from pathlib import Path

from cheapquant_fi.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_RUNTIME_CONFIG_DIR,
    DEFAULT_RUNTIME_CONFIG_PATH,
    AppSettings,
    RuntimeSettings,
    get_runtime_settings,
    load_runtime_settings,
    load_settings,
    save_runtime_settings,
)


def test_default_config_file_exists():
    assert DEFAULT_CONFIG_PATH.exists()


def test_default_runtime_config_path_is_under_home():
    assert DEFAULT_RUNTIME_CONFIG_DIR == Path.home() / ".cqfi"
    assert DEFAULT_RUNTIME_CONFIG_PATH == DEFAULT_RUNTIME_CONFIG_DIR / "cqfi_runtime.json"


def test_load_default_config(monkeypatch):
    monkeypatch.delenv("CQFI_QUANT_CACHE_DB", raising=False)
    monkeypatch.delenv("CQFI_CACHE_DB", raising=False)
    settings = AppSettings.from_yaml(DEFAULT_CONFIG_PATH)
    assert settings.ycs_db_path.name == "ycs_data.duckdb"
    assert settings.ycs_semantics_dir.name == "semantics"
    assert settings.bond_analytics_db_path.name == "bond_analytics.duckdb"
    assert settings.quant_cache_db_path.name  # path resolved from yaml / env
    assert settings.sessions_dir.name == "sessions"
    assert set(settings.mcp_datasets) == {"input", "cache", "bond_analytics"}
    assert settings.mcp_datasets["bond_analytics"].db_path == settings.bond_analytics_db_path


def test_load_settings_sets_active_config():
    settings = load_settings(DEFAULT_CONFIG_PATH)
    assert settings.config_path == DEFAULT_CONFIG_PATH.resolve()


def test_load_runtime_settings_creates_dir_and_all_false_defaults(tmp_path: Path):
    runtime_dir = tmp_path / ".cqfi"
    path = runtime_dir / "cqfi_runtime.json"
    assert not runtime_dir.exists()

    settings = load_runtime_settings(path)
    assert runtime_dir.is_dir()
    assert path.is_file()
    assert settings.use_quant_cache is False
    assert settings.save_quant_cache_to_bond_analytics_after_session is False

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "use_quant_cache": False,
        "save_quant_cache_to_bond_analytics_after_session": False,
    }


def test_runtime_settings_update_and_save(tmp_path: Path):
    path = tmp_path / ".cqfi" / "cqfi_runtime.json"
    settings = RuntimeSettings(use_quant_cache=True)
    save_runtime_settings(settings, path)

    loaded = load_runtime_settings(path)
    assert loaded.use_quant_cache is True

    loaded.update(
        use_quant_cache=False,
        save_quant_cache_to_bond_analytics_after_session=True,
        unknown_key="ignored",
    )
    save_runtime_settings(loaded, path)

    again = load_runtime_settings(path)
    assert again.use_quant_cache is False
    assert again.save_quant_cache_to_bond_analytics_after_session is True
    assert get_runtime_settings().save_quant_cache_to_bond_analytics_after_session is True


def test_env_override(monkeypatch, tmp_path: Path):
    config = tmp_path / "cqfi.yaml"
    config.write_text(
        """
paths:
  ycs_db: ./input.db
  ycs_semantics: ./sem/input.yaml
  bond_analytics_db: ./bond_analytics.db
  bond_analytics_semantics: ./sem/bond_analytics.yaml
  quant_cache_db: ./cache.db
  quant_cache_semantics: ./sem
  sessions_dir: ./sessions
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CQFI_YCS_DB", r"C:\override\ycs_data.duckdb")
    settings = AppSettings.from_yaml(config)
    assert settings.ycs_db_path == Path(r"C:\override\ycs_data.duckdb")


def test_bond_analytics_db_env_override(monkeypatch, tmp_path: Path):
    config = tmp_path / "cqfi.yaml"
    config.write_text(
        """
paths:
  ycs_db: ./input.db
  ycs_semantics: ./sem/input.yaml
  bond_analytics_db: ./bond_analytics.db
  bond_analytics_semantics: ./sem/bond_analytics.yaml
  quant_cache_db: ./cache.db
  quant_cache_semantics: ./sem/quant_cache.yaml
  sessions_dir: ./sessions
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CQFI_BOND_ANALYTICS_DB", r"D:\data\duckdb\bond_analytics.duckdb")
    settings = AppSettings.from_yaml(config)
    assert settings.bond_analytics_db_path == Path(r"D:\data\duckdb\bond_analytics.duckdb")


def test_duplicate_database_paths_rejected(tmp_path: Path):
    config = tmp_path / "cqfi.yaml"
    config.write_text(
        """
paths:
  ycs_db: ./same.db
  ycs_semantics: ./sem/ycs.yaml
  bond_analytics_db: ./same.db
  bond_analytics_semantics: ./sem/bond.yaml
  quant_cache_db: ./cache.db
  quant_cache_semantics: ./sem/cache.yaml
  sessions_dir: ./sessions
""",
        encoding="utf-8",
    )
    try:
        AppSettings.from_yaml(config)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "ycs_db and bond_analytics_db are the same" in str(exc)
        assert "database path" in str(exc)


def test_duplicate_semantics_paths_rejected(tmp_path: Path):
    config = tmp_path / "cqfi.yaml"
    config.write_text(
        """
paths:
  ycs_db: ./input.db
  ycs_semantics: ./sem/shared.yaml
  bond_analytics_db: ./bond.db
  bond_analytics_semantics: ./sem/shared.yaml
  quant_cache_db: ./cache.db
  quant_cache_semantics: ./sem/cache.yaml
  sessions_dir: ./sessions
""",
        encoding="utf-8",
    )
    try:
        AppSettings.from_yaml(config)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "ycs_semantics and bond_analytics_semantics are the same" in str(exc)
        assert "semantics path" in str(exc)


def test_duplicate_db_via_env_override_rejected(monkeypatch, tmp_path: Path):
    config = tmp_path / "cqfi.yaml"
    config.write_text(
        """
paths:
  ycs_db: ./input.db
  ycs_semantics: ./sem/ycs.yaml
  bond_analytics_db: ./bond.db
  bond_analytics_semantics: ./sem/bond.yaml
  quant_cache_db: ./cache.db
  quant_cache_semantics: ./sem/cache.yaml
  sessions_dir: ./sessions
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CQFI_QUANT_CACHE_DB", "./input.db")
    try:
        AppSettings.from_yaml(config)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "ycs_db and quant_cache_db are the same" in str(exc)
