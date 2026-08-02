"""Tests for the /save_cache runtime toggle command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqfi.agent.cli import (
    format_save_cache_status,
    handle_runtime_toggle_commands,
    handle_save_cache_command,
)
from cqfi.config import get_runtime_settings, load_runtime_settings, save_runtime_settings


@pytest.fixture
def runtime_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cqfi_runtime.json"
    monkeypatch.setenv("CQFI_RUNTIME_CONFIG", str(path))
    load_runtime_settings(path)
    return path


def test_save_cache_help_shows_status(runtime_path: Path):
    result = handle_save_cache_command("/save_cache")
    assert result is not None
    assert "Quant Cache Session-End Policy" in result
    assert "save_quant_cache_to_bond_analytics_after_session is disabled" in result


def test_save_cache_on_persists(runtime_path: Path):
    result = handle_save_cache_command("/save_cache on")
    assert result is not None
    assert "set to true" in result
    assert get_runtime_settings().save_quant_cache_to_bond_analytics_after_session is True

    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert data["save_quant_cache_to_bond_analytics_after_session"] is True


def test_save_cache_off_persists(runtime_path: Path):
    save_runtime_settings(
        get_runtime_settings().__class__(
            save_quant_cache_to_bond_analytics_after_session=True,
        ),
        runtime_path,
    )

    result = handle_save_cache_command("/save_cache off")
    assert result is not None
    assert "set to false" in result
    assert (
        get_runtime_settings().save_quant_cache_to_bond_analytics_after_session is False
    )

    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert data["save_quant_cache_to_bond_analytics_after_session"] is False


def test_save_cache_invalid_form(runtime_path: Path):
    result = handle_save_cache_command("/save_cache maybe")
    assert result is not None
    assert "Invalid /save_cache command" in result


def test_save_cache_not_handled_for_other_commands():
    assert handle_save_cache_command("/cache on") is None
    assert handle_save_cache_command("/bond usa10y001") is None


def test_runtime_toggle_commands_dispatch(runtime_path: Path):
    cache_help = handle_runtime_toggle_commands("/cache")
    assert cache_help is not None
    assert "use_quant_cache" in cache_help

    save_help = handle_runtime_toggle_commands("/save_cache")
    assert save_help is not None
    assert "save_quant_cache_to_bond_analytics_after_session" in save_help


def test_format_save_cache_status_reflects_runtime(runtime_path: Path):
    handle_save_cache_command("/save_cache on")
    assert "enabled (true)" in format_save_cache_status()

    handle_save_cache_command("/save_cache off")
    assert "disabled (false)" in format_save_cache_status()
