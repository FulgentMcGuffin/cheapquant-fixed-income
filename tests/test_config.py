"""Tests for YAML configuration loading."""

from __future__ import annotations

from pathlib import Path

from cheapquant_fi.config import (
    DEFAULT_CONFIG_PATH,
    AppSettings,
    load_settings,
)


def test_default_config_file_exists():
    assert DEFAULT_CONFIG_PATH.exists()


def test_load_default_config():
    settings = AppSettings.from_yaml(DEFAULT_CONFIG_PATH)
    assert settings.input_db_path.name == "ycs_data.duckdb"
    assert settings.input_semantics_dir.name == "semantics"
    assert settings.bond_analytics_db_path.name == "bond_analytics.duckdb"
    assert settings.cache_db_path.name == "active_cache.db"
    assert settings.sessions_dir.name == "sessions"
    assert settings.write_to_bond_analytics_db is True


def test_load_settings_sets_active_config():
    settings = load_settings(DEFAULT_CONFIG_PATH)
    assert settings.config_path == DEFAULT_CONFIG_PATH.resolve()


def test_env_override(monkeypatch, tmp_path: Path):
    config = tmp_path / "cqfi.yaml"
    config.write_text(
        """
paths:
  input_db: ./input.db
  input_semantics: ./sem/input.yaml
  bond_analytics_db: ./bond_analytics.db
  cache_db: ./cache.db
  cache_semantics_dir: ./sem
  sessions_dir: ./sessions
settings:
  write_to_bond_analytics_db: false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CQFI_INPUT_DB", r"C:\override\ycs_data.duckdb")
    monkeypatch.setenv("CQFI_WRITE_TO_BOND_ANALYTICS_DB", "true")
    settings = AppSettings.from_yaml(config)
    assert settings.input_db_path == Path(r"C:\override\ycs_data.duckdb")
    assert settings.write_to_bond_analytics_db is True


def test_bond_analytics_db_env_override(monkeypatch, tmp_path: Path):
    config = tmp_path / "cqfi.yaml"
    config.write_text(
        """
paths:
  input_db: ./input.db
  input_semantics: ./sem/input.yaml
  bond_analytics_db: ./bond_analytics.db
  cache_db: ./cache.db
  cache_semantics_dir: ./sem
  sessions_dir: ./sessions
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CQFI_BOND_ANALYTICS_DB", r"D:\data\duckdb\bond_analytics.duckdb")
    settings = AppSettings.from_yaml(config)
    assert settings.bond_analytics_db_path == Path(r"D:\data\duckdb\bond_analytics.duckdb")
