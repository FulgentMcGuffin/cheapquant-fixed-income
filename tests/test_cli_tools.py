"""Tests for CLI bond lookup tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from cheapquant_fi.bond_manager import BondManager
from cheapquant_fi.cli_tools import get_bond
from cheapquant_fi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    create_schema,
    load_semantics,
    open_sink,
)


@pytest.fixture
def bond_db(tmp_path: Path):
    db_path = tmp_path / "bond_analytics.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        db.execute(
            """
            INSERT INTO bond_universe (
                bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
                first_coupon_date, accrual_start_date, closest_tenor_pillar,
                issue_amount, currency, is_green
            ) VALUES (
                'US0001', 'usa10y001', 'USA', 2.5, '2030-01-15', '2020-01-15',
                NULL, NULL, '10Y', 1000.0, 'USD', 0
            )
            """
        )
    return db_path


@pytest.fixture(autouse=True)
def _clear_manager():
    BondManager.instance().clear()
    yield
    BondManager.instance().clear()


def test_get_bond_returns_json(bond_db: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "cheapquant_fi.bond_manager.get_settings",
        lambda: type("S", (), {"bond_analytics_db_path": bond_db})(),
    )

    result = get_bond("usa10y001")

    assert result["status"] == "success"
    assert '"bond_id": "US0001"' in result["bond_json"]
    assert result["bond"]["issuer"] == "USA"


def test_get_bond_not_found(bond_db: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "cheapquant_fi.bond_manager.get_settings",
        lambda: type("S", (), {"bond_analytics_db_path": bond_db})(),
    )

    result = get_bond("missing")

    assert result["status"] == "not_found"
