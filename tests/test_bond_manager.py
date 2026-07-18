"""Tests for BondManager singleton registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from cheapquant_fi.bond_manager import BondManager
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
        db.execute(
            """
            INSERT INTO bond_universe (
                bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
                first_coupon_date, accrual_start_date, closest_tenor_pillar,
                issue_amount, currency, is_green
            ) VALUES (
                'DE0001', 'deu10y001', 'DEU', 1.0, '2031-06-30', '2021-06-30',
                NULL, NULL, '10Y', 500.0, 'EUR', 1
            )
            """
        )
    return db_path


@pytest.fixture(autouse=True)
def _clear_manager():
    BondManager.instance().clear()
    yield
    BondManager.instance().clear()


def test_manager_is_singleton():
    assert BondManager.instance() is BondManager()


def test_get_loads_by_user_friendly_id(bond_db: Path):
    manager = BondManager.instance()
    bond = manager.get("usa10y001", db_path=bond_db)

    assert bond is not None
    assert bond.bond_id == "US0001"
    assert bond.user_friendly_id == "usa10y001"
    assert bond.issuer == "USA"


def test_get_loads_by_bond_id(bond_db: Path):
    manager = BondManager.instance()
    bond = manager.get("DE0001", db_path=bond_db)

    assert bond is not None
    assert bond.user_friendly_id == "deu10y001"
    assert bond.issuer == "DEU"


def test_get_registers_both_identifiers(bond_db: Path):
    manager = BondManager.instance()
    bond = manager.get("usa10y001", db_path=bond_db)

    assert bond is not None
    assert manager.get("US0001", db_path=bond_db) is bond


def test_get_returns_cached_without_db_access(bond_db: Path):
    manager = BondManager.instance()
    first = manager.get("usa10y001", db_path=bond_db)

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("Database should not be queried for cached bond")

    import cheapquant_fi.bond_manager as bond_manager_module

    original = bond_manager_module._fetch_bond_row
    bond_manager_module._fetch_bond_row = fail_fetch
    try:
        second = manager.get("US0001", db_path=bond_db)
    finally:
        bond_manager_module._fetch_bond_row = original

    assert second is first


def test_get_returns_none_for_unknown_id(bond_db: Path):
    assert BondManager.instance().get("missing-id", db_path=bond_db) is None


def test_has_bond_delegates_to_get(bond_db: Path):
    manager = BondManager.instance()
    assert manager.has_bond("usa10y001", db_path=bond_db) is True
    assert manager.has_bond("missing-id", db_path=bond_db) is False


def test_user_friendly_id_takes_priority_over_bond_id(bond_db: Path):
    manager = BondManager.instance()
    manager.clear()

    db_path = bond_db.parent / "priority.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        db.execute(
            """
            INSERT INTO bond_universe (
                bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
                first_coupon_date, accrual_start_date, closest_tenor_pillar,
                issue_amount, currency
            ) VALUES (
                'ID_AS_BOND', 'other', 'USA', 2.0, '2030-01-15', '2020-01-15',
                NULL, NULL, '10Y', 100.0, 'USD'
            )
            """
        )
        db.execute(
            """
            INSERT INTO bond_universe (
                bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
                first_coupon_date, accrual_start_date, closest_tenor_pillar,
                issue_amount, currency
            ) VALUES (
                'another', 'ID_AS_BOND', 'DEU', 1.0, '2031-06-30', '2021-06-30',
                NULL, NULL, '10Y', 100.0, 'EUR'
            )
            """
        )

    bond = manager.get("ID_AS_BOND", db_path=db_path)
    assert bond is not None
    assert bond.issuer == "DEU"
    assert bond.user_friendly_id == "ID_AS_BOND"
