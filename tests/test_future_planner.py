"""Tests for batch bond-future planning: delivery-letter parsing, plan building."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from cqfi.batch.future_planner import (
    build_future_plan,
    build_future_series_plan,
    parse_delivery_letters,
)
from cqfi.bond_futures import BondFutureError
from cqfi.bond_manager import BondManager
from cqfi.config import AppSettings
from cqfi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    create_schema,
    load_semantics,
    open_sink,
)

# ── parse_delivery_letters ───────────────────────────────────────────────────


def test_parse_delivery_letters_basic():
    assert parse_delivery_letters("HMUZ") == ["H", "M", "U", "Z"]


def test_parse_delivery_letters_dedupes_preserving_order():
    assert parse_delivery_letters("hHmM") == ["H", "M"]


def test_parse_delivery_letters_rejects_unknown_letter():
    with pytest.raises(BondFutureError):
        parse_delivery_letters("HAI")  # 'A' and 'I' are not month codes


def test_parse_delivery_letters_rejects_empty():
    with pytest.raises(BondFutureError):
        parse_delivery_letters("")


# ── build_future_series_plan / build_future_plan ─────────────────────────────

# FBTP (Italian 10Y) admits 8y6m..11y remaining maturity at delivery
# (see tests/test_delivery_basket.py).


@pytest.fixture
def bond_analytics_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bond_analytics.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        bonds = [
            # Deliverable into a ~2020-09 FBTP contract (9y6m / 10y remaining).
            ("IT0001", "itamar030", 3.5, "2030-03-10", "2019-03-10"),
            ("IT0002", "itasep030", 4.0, "2030-09-10", "2019-09-10"),
            # Too long-dated (~12y remaining) — excluded from the basket.
            ("IT0003", "itasep032", 4.5, "2032-09-10", "2021-09-10"),
        ]
        for bond_id, friendly, coupon, maturity, issue in bonds:
            db.execute(
                "INSERT INTO bond_universe (bond_id, user_friendly_id, issuer, "
                "coupon, maturity, issue_date, currency, is_green) VALUES "
                f"('{bond_id}', '{friendly}', 'ITA', {coupon}, '{maturity}', "
                f"'{issue}', 'EUR', 0)"
            )
    return db_path


@pytest.fixture
def ycs_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ycs_data.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE zero_rates (source TEXT, date TEXT, Y010p0 REAL)")
        for d in (
            "2020-01-02", "2020-06-15", "2020-09-08", "2020-09-11",
            "2020-12-31", "2021-01-04",
        ):
            conn.execute("INSERT INTO zero_rates VALUES ('ITA', ?, 0.5)", (d,))
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


def test_build_future_series_plan_one_year_one_letter(settings: AppSettings):
    plan = build_future_series_plan("FBTP", ["U"], date(2020, 1, 1), date(2020, 12, 31), settings)

    assert plan.future_code == "FBTP"
    assert len(plan.contracts) == 1
    assert plan.contracts[0].delivery_month == 9
    assert plan.contracts[0].delivery_year == 2020
    assert len(plan.trade_dates) == 5  # every zero_rates row in [start, end] (excludes 2021-01-04)


def test_build_future_series_plan_excludes_dates_after_delivery(settings: AppSettings):
    plan = build_future_series_plan("FBTP", ["U"], date(2020, 1, 1), date(2020, 12, 31), settings)
    contract = plan.contracts[0]
    delivery_date = contract.delivery_end_date()

    assert plan.work_items  # sanity: the fixture's dates produced work
    for item in plan.work_items:
        if item.trade_date <= delivery_date:
            assert len(item.baskets) == 1
        else:
            assert len(item.baskets) == 0


def test_build_future_series_plan_multiple_letters_and_years(settings: AppSettings):
    plan = build_future_series_plan(
        "FBTP", ["H", "M", "U", "Z"], date(2020, 1, 1), date(2021, 1, 4), settings
    )
    # H, M, U, Z in both 2020 and 2021 -> 8 contracts.
    assert len(plan.contracts) == 8
    deliveries = [c.delivery_end_date() for c in plan.contracts]
    assert deliveries == sorted(deliveries)


def test_build_future_series_plan_basket_has_deliverable_bonds(settings: AppSettings):
    plan = build_future_series_plan("FBTP", ["U"], date(2020, 1, 1), date(2020, 9, 8), settings)
    item = plan.work_items[0]
    assert len(item.baskets) == 1
    basket = item.baskets[0]
    assert len(basket) > 0
    assert all(bond.issuer == "ITA" for bond in basket.bonds())


def test_build_future_plan_dedupes_by_canonical_name(settings: AppSettings):
    plan = build_future_plan(["fbtp", "FBTP"], "U", date(2020, 1, 1), date(2020, 12, 31), settings)
    assert len(plan.series_plans) == 1
    assert plan.series_plans[0].future_code == "FBTP"


def test_build_future_plan_total_cells(settings: AppSettings):
    plan = build_future_plan(["FBTP"], "U", date(2020, 1, 1), date(2020, 12, 31), settings)
    assert plan.total_cells == sum(len(item.baskets) for item in plan.work_items)
    assert plan.total_cells > 0
