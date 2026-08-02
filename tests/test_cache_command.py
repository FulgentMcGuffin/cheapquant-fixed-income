"""Tests for the /cache runtime toggle command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqfi.agent.cli import format_cache_status, handle_cache_command
from cqfi.config import get_runtime_settings, load_runtime_settings, save_runtime_settings


@pytest.fixture
def runtime_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cqfi_runtime.json"
    monkeypatch.setenv("CQFI_RUNTIME_CONFIG", str(path))
    load_runtime_settings(path)
    return path


def test_cache_help_shows_status(runtime_path: Path):
    result = handle_cache_command("/cache")
    assert result is not None
    assert "Quant Cache Session Toggle" in result
    assert "use_quant_cache is disabled (false)" in result


def test_cache_on_persists(runtime_path: Path):
    result = handle_cache_command("/cache on")
    assert result is not None
    assert "set to true" in result
    assert get_runtime_settings().use_quant_cache is True

    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert data["use_quant_cache"] is True


def test_cache_off_persists(runtime_path: Path):
    save_runtime_settings(
        get_runtime_settings().__class__(use_quant_cache=True),
        runtime_path,
    )

    result = handle_cache_command("/cache off")
    assert result is not None
    assert "set to false" in result
    assert get_runtime_settings().use_quant_cache is False

    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert data["use_quant_cache"] is False


def test_cache_invalid_form(runtime_path: Path):
    result = handle_cache_command("/cache maybe")
    assert result is not None
    assert "Invalid /cache command" in result


def test_cache_not_handled_for_other_commands():
    assert handle_cache_command("/bond usa10y001") is None
    assert handle_cache_command("price cmt USA 2020-01-02") is None


def test_format_cache_status_reflects_runtime(runtime_path: Path):
    handle_cache_command("/cache on")
    assert "enabled (true)" in format_cache_status()

    handle_cache_command("/cache off")
    assert "disabled (false)" in format_cache_status()


def test_load_runtime_settings_on_session_start(runtime_path: Path):
    runtime_path.write_text(
        json.dumps({"use_quant_cache": True}),
        encoding="utf-8",
    )
    loaded = load_runtime_settings(runtime_path)
    assert loaded.use_quant_cache is True
    assert get_runtime_settings().use_quant_cache is True
