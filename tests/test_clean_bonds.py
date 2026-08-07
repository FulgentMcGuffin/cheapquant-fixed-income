"""Tests for /clean bonds deduplication."""

from __future__ import annotations

from pathlib import Path

import pytest

from cqfi.clean.bonds import clean_bond_analytics_duplicates
from cqfi.clean.command import (
    execute_clean_bonds,
    handle_clean_command,
    parse_clean_command,
)
from cqfi.config import AppSettings
from cqfi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    create_schema,
    load_semantics,
    open_sink,
)


@pytest.fixture
def clean_db(tmp_path: Path):
    db_path = tmp_path / "bond_analytics.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        db.execute(
            """
            INSERT INTO bond_universe (
                bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
                currency
            ) VALUES (
                'FR0001', 'fra10y001', 'FRA', 2.5, '2030-01-15', '2020-01-15', 'EUR'
            )
            """
        )
        for analytic_id, created_at, clean_price in (
            ("old-row", "2024-01-01 10:00:00.000", 98.0),
            ("mid-row", "2024-01-02 10:00:00.000", 99.0),
            ("new-row", "2024-01-03 10:00:00.000", 100.0),
        ):
            db.execute(
                """
                INSERT INTO bond_analytics (
                    analytic_id, bond_id, created_at, trade_date, settlement_date,
                    curve_used, clean_price
                ) VALUES (?, 'FR0001', ?, '2020-01-02', '2020-01-04', 1, ?)
                """,
                (analytic_id, created_at, clean_price),
            )
        db.execute(
            """
            INSERT INTO bond_analytics (
                analytic_id, bond_id, created_at, trade_date, settlement_date,
                curve_used, clean_price
            ) VALUES (
                'other-date', 'FR0001', '2024-01-01 10:00:00.000',
                '2020-01-03', '2020-01-05', 1, 97.5
            )
            """
        )
    return db_path


def test_parse_clean_command():
    assert parse_clean_command("hello") is None
    assert parse_clean_command("/clean").kind == "help"
    assert parse_clean_command("/clean bonds").kind == "run_bonds"
    invalid = parse_clean_command("/clean futures")
    assert invalid is not None and invalid.kind == "invalid"


def test_handle_clean_command_help():
    text = handle_clean_command("/clean")
    assert text is not None and "/clean bonds" in text


def test_clean_bond_analytics_duplicates_keeps_latest(clean_db: Path):
    result = clean_bond_analytics_duplicates(clean_db, DEFAULT_SEMANTICS_PATH)
    assert result.rows_before == 4
    assert result.rows_after == 2
    assert result.duplicates_removed == 2
    assert result.duplicate_groups == 1

    with open_sink(clean_db) as db:
        rows = list(
            db.execute(
                """
                SELECT analytic_id, trade_date, clean_price
                FROM bond_analytics
                ORDER BY trade_date
                """
            )
        )
    assert [(r["analytic_id"], r["trade_date"], r["clean_price"]) for r in rows] == [
        ("new-row", "2020-01-02", 100.0),
        ("other-date", "2020-01-03", 97.5),
    ]


def test_clean_bond_analytics_noop_when_unique(tmp_path: Path):
    db_path = tmp_path / "bond_analytics.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(db_path) as db:
        create_schema(db, semantics)
        db.execute(
            """
            INSERT INTO bond_universe (
                bond_id, issuer, coupon, maturity, issue_date, currency
            ) VALUES (
                'FR0001', 'FRA', 2.5, '2030-01-15', '2020-01-15', 'EUR'
            )
            """
        )
        db.execute(
            """
            INSERT INTO bond_analytics (
                analytic_id, bond_id, created_at, trade_date, settlement_date,
                curve_used
            ) VALUES (
                'only-one', 'FR0001', '2024-01-01 10:00:00.000',
                '2020-01-02', '2020-01-04', 1
            )
            """
        )

    result = clean_bond_analytics_duplicates(db_path, DEFAULT_SEMANTICS_PATH)
    assert result.duplicates_removed == 0
    assert result.rows_before == result.rows_after == 1


def test_execute_clean_bonds_uses_settings(clean_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = AppSettings(
        config_path=tmp_path / "cqfi.yaml",
        ycs_db_path=tmp_path / "ycs.duckdb",
        ycs_semantics_dir=Path("semantics"),
        bond_analytics_db_path=clean_db,
        bond_analytics_semantics_dir=Path("semantics"),
        bond_analytics_semantics_path=DEFAULT_SEMANTICS_PATH,
        quant_cache_db_path=tmp_path / "quant_cache.sqlite",
        quant_cache_semantics_dir=Path("semantics"),
        quant_cache_semantics_path=Path("semantics/quant_cache.yaml"),
        sessions_dir=tmp_path / "sessions",
        mcp_datasets={},
    )
    monkeypatch.setattr("cqfi.clean.command.get_settings", lambda: settings)
    message = execute_clean_bonds()
    assert "Removed 2 duplicate" in message
