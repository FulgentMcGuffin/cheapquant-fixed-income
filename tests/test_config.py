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
    assert settings.input_db_path.name == "input_data.db"
    assert settings.input_semantics_dir.name == "semantics"
    assert settings.cache_db_path.name == "active_cache.db"
    assert settings.sessions_dir.name == "sessions"


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
  cache_db: ./cache.db
  cache_semantics_dir: ./sem
  sessions_dir: ./sessions
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CQFI_INPUT_DB", r"C:\override\input_data.db")
    settings = AppSettings.from_yaml(config)
    assert settings.input_db_path == Path(r"C:\override\input_data.db")
