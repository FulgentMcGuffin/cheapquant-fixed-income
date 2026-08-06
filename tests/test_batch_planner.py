"""Tests for batch bond-analytics planning: active-bond filter and trade dates."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from cqfi.batch.planner import (
    build_issuer_plan,
    build_plan,
    is_bond_active,
    resolve_issuers,
    resolve_trade_dates,
)
from cqfi.bond_manager import BondManager
from cqfi.config import AppSettings
from cqfi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    create_schema,
    load_semantics,
    open_sink,
)
from cqfi.instruments import Bond
from cqfi.issuers import ISSUERS


def _insert_bond(db, **fields) -> None:
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    db.execute(
        f"INSERT INTO bond_universe ({columns}) VALUES ({placeholders})",
        list(fields.values()),
    )


@pytest.fixture
def bond_analytics_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bond_analytics.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        _insert_bond(
            db,
            bond_id="FR0001", user_friendly_id="fraapr029", issuer="FRA",
            coupon=1.0, maturity="2029-04-25", issue_date="2019-04-25",
            closest_tenor_pillar="10Y", issue_amount=1000.0, currency="EUR",
        )
        # Not yet issued as of any 2020 trade date.
        _insert_bond(
            db,
            bond_id="FR0002", user_friendly_id="frajun040", issuer="FRA",
            coupon=1.5, maturity="2040-06-01", issue_date="2025-06-01",
            closest_tenor_pillar="20Y", issue_amount=1000.0, currency="EUR",
        )
        # Already matured before any 2020 trade date.
        _insert_bond(
            db,
            bond_id="FR0003", user_friendly_id="frajan018", issuer="FRA",
            coupon=2.0, maturity="2018-01-15", issue_date="2008-01-15",
            closest_tenor_pillar="10Y", issue_amount=1000.0, currency="EUR",
        )
    return db_path


@pytest.fixture
def ycs_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ycs_data.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE zero_rates (source TEXT, date TEXT, Y010p0 REAL)")
        for d in ("2020-01-02", "2020-01-03", "2020-01-06", "2020-06-15", "2020-12-31"):
            conn.execute("INSERT INTO zero_rates VALUES ('FRA', ?, 0.5)", (d,))
        conn.commit()
    return db_path


@pytest.fixture
def settings(bond_analytics_db: Path, ycs_db: Path, tmp_path: Path) -> AppSettings:
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


def test_resolve_issuers_dedupes_case_insensitive():
    profiles = resolve_issuers(["fra", "FRA", "ita"])
    assert [p.source_code for p in profiles] == ["FRA", "ITA"]


def test_is_bond_active_boundaries():
    settlement = date(2020, 1, 6)
    trade_date = date(2020, 1, 2)

    issued_before = Bond(
        issuer="FRA", maturity=date(2029, 4, 25), bond_id="B1", issue_date=date(2019, 4, 25)
    )
    assert is_bond_active(issued_before, trade_date, settlement) is True

    issued_same_day = Bond(
        issuer="FRA", maturity=date(2029, 4, 25), bond_id="B2", issue_date=trade_date
    )
    assert is_bond_active(issued_same_day, trade_date, settlement) is True

    issued_after = Bond(
        issuer="FRA", maturity=date(2029, 4, 25), bond_id="B3", issue_date=date(2020, 1, 3)
    )
    assert is_bond_active(issued_after, trade_date, settlement) is False

    matures_on_settlement = Bond(
        issuer="FRA", maturity=settlement, bond_id="B4", issue_date=date(2019, 1, 1)
    )
    assert is_bond_active(matures_on_settlement, trade_date, settlement) is False

    matures_after_settlement = Bond(
        issuer="FRA", maturity=date(2020, 1, 7), bond_id="B5", issue_date=date(2019, 1, 1)
    )
    assert is_bond_active(matures_after_settlement, trade_date, settlement) is True

    no_issue_date = Bond(issuer="FRA", maturity=date(2029, 4, 25), bond_id="B6")
    assert is_bond_active(no_issue_date, trade_date, settlement) is True


def test_resolve_trade_dates_filters_to_range(settings: AppSettings):
    dates = resolve_trade_dates(ISSUERS["FRA"], date(2020, 1, 1), date(2020, 1, 31), settings)
    assert dates == [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]


def test_resolve_trade_dates_rejects_end_before_start(settings: AppSettings):
    with pytest.raises(ValueError):
        resolve_trade_dates(ISSUERS["FRA"], date(2020, 2, 1), date(2020, 1, 1), settings)


def test_build_issuer_plan_excludes_unissued_and_matured_bonds(settings: AppSettings):
    plan = build_issuer_plan(ISSUERS["FRA"], date(2020, 1, 1), date(2020, 1, 31), settings)

    assert plan.trade_dates == (date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6))
    assert {str(b) for b in plan.bonds} == {"FR0001"}
    for item in plan.work_items:
        assert {str(b) for b in item.bonds} == {"FR0001"}
    assert plan.total_cells == 3


def test_build_plan_multiple_issuers(settings: AppSettings):
    plan = build_plan(["fra"], date(2020, 1, 1), date(2020, 1, 31), settings)
    assert len(plan.issuer_plans) == 1
    assert plan.total_cells == 3
