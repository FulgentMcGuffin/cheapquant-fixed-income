"""Tests for /batch command parsing and launch-request building."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from cqfi.agent.cli import execute_batch_command, handle_batch_command
from cqfi.batch.command import (
    BatchCommandResult,
    build_batch_launch_request,
    build_future_batch_launch_request,
    parse_batch_command,
)
from cqfi.bond_manager import BondManager
from cqfi.config import AppSettings, get_runtime_settings, load_runtime_settings
from cqfi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    create_schema,
    load_semantics,
    open_sink,
)


def _insert_bond(db, **fields) -> None:
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    db.execute(
        f"INSERT INTO bond_universe ({columns}) VALUES ({placeholders})",
        list(fields.values()),
    )


# ── Parsing ───────────────────────────────────────────────────────────────


def test_parse_batch_command_run():
    parsed = parse_batch_command("/batch FRA 2020-01-01 2020-12-31")
    assert parsed == BatchCommandResult(
        kind="run", mode="bond", issuer="FRA", start=date(2020, 1, 1), end=date(2020, 12, 31)
    )


def test_parse_batch_command_case_insensitive_issuer_preserved():
    parsed = parse_batch_command("/batch fra 2020-01-01 2020-12-31")
    assert parsed is not None
    assert parsed.kind == "run"
    assert parsed.issuer == "fra"  # resolved to a code later, not here


def test_parse_batch_command_bare_is_help():
    assert parse_batch_command("/batch").kind == "help"
    assert parse_batch_command("/batch   ").kind == "help"


def test_parse_batch_command_rejects_end_before_start():
    assert parse_batch_command("/batch FRA 2020-12-31 2020-01-01").kind == "invalid"


def test_parse_batch_command_rejects_multiple_issuers():
    assert parse_batch_command("/batch FRA ITA 2020-01-01 2020-12-31").kind == "invalid"


def test_parse_batch_command_rejects_bad_dates():
    assert parse_batch_command("/batch FRA not-a-date 2020-12-31").kind == "invalid"


def test_parse_batch_command_not_a_batch_command():
    assert parse_batch_command("/calc fraapr029") is None
    assert parse_batch_command("hello") is None


# ── Future-mode parsing ──────────────────────────────────────────────────────


def test_parse_batch_command_future_run():
    parsed = parse_batch_command("/batch FGBM HMUZ 2020-01-01 2020-12-31")
    assert parsed == BatchCommandResult(
        kind="run",
        mode="future",
        future_code="FGBM",
        delivery="HMUZ",
        start=date(2020, 1, 1),
        end=date(2020, 12, 31),
    )


def test_parse_batch_command_future_single_letter():
    parsed = parse_batch_command("/batch IK H 2027-01-01 2027-12-31")
    assert parsed is not None
    assert parsed.kind == "run"
    assert parsed.mode == "future"
    assert parsed.future_code == "IK"
    assert parsed.delivery == "H"


def test_parse_batch_command_future_rejects_end_before_start():
    result = parse_batch_command("/batch FGBM HMUZ 2020-12-31 2020-01-01")
    assert result.kind == "invalid"


def test_parse_batch_command_future_rejects_bad_dates():
    result = parse_batch_command("/batch FGBM HMUZ not-a-date 2020-12-31")
    assert result.kind == "invalid"


# ── handle_batch_command (help / invalid text) ──────────────────────────────


def test_handle_batch_command_help():
    result = handle_batch_command("/batch")
    assert result is not None
    assert "Batch Analytics" in result
    assert "/batch <issuer> <start> <end>" in result
    assert "/batch <future_code> <delivery> <start> <end>" in result


def test_handle_batch_command_invalid():
    result = handle_batch_command("/batch FRA 2020-12-31 2020-01-01")
    assert result is not None
    assert "Invalid /batch command" in result


def test_handle_batch_command_valid_not_handled():
    assert handle_batch_command("/batch FRA 2020-01-01 2020-12-31") is None


def test_handle_batch_command_not_a_batch_command():
    assert handle_batch_command("/calc fraapr029") is None


# ── build_batch_launch_request ──────────────────────────────────────────────


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    bond_analytics_db = tmp_path / "bond_analytics.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(bond_analytics_db) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        _insert_bond(
            db,
            bond_id="FR0001", user_friendly_id="fraapr029", issuer="FRA",
            coupon=1.0, maturity="2029-04-25", issue_date="2019-04-25",
            closest_tenor_pillar="10Y", issue_amount=1000.0, currency="EUR",
        )
        # Deliverable into an FBTP contract around a 2020-09 delivery
        # (FBTP admits 8y6m..11y remaining maturity — see test_delivery_basket.py).
        _insert_bond(
            db,
            bond_id="IT0001", user_friendly_id="itasep030", issuer="ITA",
            coupon=3.5, maturity="2030-09-10", issue_date="2019-09-10",
            closest_tenor_pillar="10Y", issue_amount=1000.0, currency="EUR",
        )

    ycs_db = tmp_path / "ycs_data.sqlite"
    with sqlite3.connect(ycs_db) as conn:
        conn.execute("CREATE TABLE zero_rates (source TEXT, date TEXT, Y010p0 REAL)")
        conn.execute("INSERT INTO zero_rates VALUES ('FRA', '2020-01-02', 0.5)")
        conn.execute("INSERT INTO zero_rates VALUES ('ITA', '2020-01-02', 0.5)")
        conn.commit()

    return AppSettings(
        config_path=tmp_path / "cqfi.yaml",
        ycs_db_path=ycs_db,
        ycs_semantics_dir=tmp_path,
        bond_analytics_db_path=bond_analytics_db,
        bond_analytics_semantics_dir=Path("semantics"),
        bond_analytics_semantics_path=Path("semantics/bond_analytics.yaml"),
        quant_cache_db_path=tmp_path / "quant_cache.sqlite",
        quant_cache_semantics_dir=Path("semantics"),
        quant_cache_semantics_path=Path("semantics/quant_cache.yaml"),
        sessions_dir=tmp_path / "sessions",
        mcp_datasets={},
    )


@pytest.fixture(autouse=True)
def _clear_bond_manager():
    BondManager.instance().clear()
    yield
    BondManager.instance().clear()


@pytest.fixture
def active_settings(settings, monkeypatch, tmp_path):
    """Make *settings* the process-wide AppSettings and runtime settings fresh."""
    import cqfi.config as cqfi_config

    monkeypatch.setattr(cqfi_config, "_settings", settings)
    runtime_path = tmp_path / "runtime.json"
    monkeypatch.setenv("CQFI_RUNTIME_CONFIG", str(runtime_path))
    load_runtime_settings(runtime_path)
    return settings


def test_build_batch_launch_request_cache_off_by_default(active_settings):
    parsed = parse_batch_command("/batch fra 2020-01-01 2020-01-31")
    request = build_batch_launch_request(parsed)

    assert request.also_cache is False
    assert request.settings is active_settings
    assert request.plan.total_cells == 1
    assert request.plan.issuer_plans[0].issuer == "FRA"
    assert "cache: off" in request.args_summary


def test_build_batch_launch_request_reflects_cache_on(active_settings):
    get_runtime_settings().update(use_quant_cache=True)

    parsed = parse_batch_command("/batch fra 2020-01-01 2020-01-31")
    request = build_batch_launch_request(parsed)

    assert request.also_cache is True
    assert "cache: on" in request.args_summary


def test_build_batch_launch_request_default_workers(active_settings):
    parsed = parse_batch_command("/batch fra 2020-01-01 2020-01-31")
    request = build_batch_launch_request(parsed)
    assert request.workers >= 1


# ── build_future_batch_launch_request ───────────────────────────────────────


def test_build_future_batch_launch_request_cache_off_by_default(active_settings):
    parsed = parse_batch_command("/batch fbtp U 2020-01-01 2020-01-31")
    request = build_future_batch_launch_request(parsed)

    assert request.also_cache is False
    assert request.settings is active_settings
    assert request.plan.total_cells == 1
    assert request.plan.series_plans[0].future_code == "FBTP"
    assert "cache: off" in request.args_summary
    assert "delivery: U" in request.args_summary


def test_build_future_batch_launch_request_reflects_cache_on(active_settings):
    get_runtime_settings().update(use_quant_cache=True)

    parsed = parse_batch_command("/batch fbtp U 2020-01-01 2020-01-31")
    request = build_future_batch_launch_request(parsed)

    assert request.also_cache is True
    assert "cache: on" in request.args_summary


def test_execute_future_batch_command_zero_cells_never_launches_gui(active_settings, monkeypatch):
    from cqfi.agent import cli as agent_cli

    monkeypatch.setattr(
        agent_cli, "run_future_gui_standalone", lambda *a, **k: pytest.fail("GUI must not launch")
    )

    # No zero_rates rows for ITA in 2025 in this fixture -> zero trade dates.
    parsed = parse_batch_command("/batch fbtp U 2025-01-01 2025-01-31")
    result = execute_batch_command(parsed)
    assert "nothing to compute" in result


# ── execute_batch_command: only the no-GUI short-circuit is safe to unit test ─


def test_execute_batch_command_zero_cells_never_launches_gui(active_settings, monkeypatch):
    from cqfi.agent import cli as agent_cli

    monkeypatch.setattr(
        agent_cli, "run_gui_standalone", lambda *a, **k: pytest.fail("GUI must not launch")
    )

    # No zero_rates rows in 2025 for FRA in this fixture -> zero trade dates.
    parsed = parse_batch_command("/batch fra 2025-01-01 2025-01-31")
    result = execute_batch_command(parsed)
    assert "nothing to compute" in result
