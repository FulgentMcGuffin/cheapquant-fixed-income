"""Tests for CLI bond lookup tool."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import QuantLib as ql

from cqfi.bond_manager import BondManager
from cqfi.bond_futures import BOND_FUTURE_CONVENTIONS, BondFuture
from cqfi.cli_tools import compute_bond_future_analytics, get_bond
from cqfi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    create_schema,
    load_semantics,
    open_sink,
)
from cqfi.date_utils import to_ql_date
from cqfi.delivery_basket import DeliveryBasket
from cqfi.instruments import Bond
from cqfi.quantlib.quantlib_market_context import (
    QuantLibCurveCollection,
    QuantlibMarketContext,
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
        "cqfi.bond_manager.get_settings",
        lambda: type("S", (), {"bond_analytics_db_path": bond_db})(),
    )

    result = get_bond("usa10y001")

    assert result["status"] == "success"
    assert '"bond_id": "US0001"' in result["bond_json"]
    assert result["bond"]["issuer"] == "USA"


def test_get_bond_not_found(bond_db: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "cqfi.bond_manager.get_settings",
        lambda: type("S", (), {"bond_analytics_db_path": bond_db})(),
    )

    result = get_bond("missing")

    assert result["status"] == "not_found"


# --------------------------------------------------------------------------- #
# compute_bond_future_analytics: CLI-level repo term structure input
# --------------------------------------------------------------------------- #
TRADE_DATE = date(2026, 5, 15)
FLAT_RATE = 0.03
FBTP_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2026)


@pytest.fixture
def future_basket() -> DeliveryBasket:
    basket = DeliveryBasket(bond_future=FBTP_U6)
    basket.add(
        Bond(
            issuer="ITA",
            maturity=date(2036, 2, 1),
            bond_id="IT202602",
            user_friendly_id="ita202602",
            coupon=4.0,
            issue_date=date(2015, 4, 1),
        )
    )
    return basket


@pytest.fixture
def future_market() -> QuantlibMarketContext:
    """A flat 3% curve, so the closed-form implied repo is exactly 3%."""
    ql.Settings.instance().evaluationDate = to_ql_date(TRADE_DATE)
    curve = ql.YieldTermStructureHandle(
        ql.FlatForward(
            to_ql_date(TRADE_DATE),
            FLAT_RATE,
            ql.ActualActual(ql.ActualActual.ISDA),
            ql.Compounded,
            ql.Annual,
        )
    )
    collection = QuantLibCurveCollection(TRADE_DATE)
    collection.set_bond_curve("ITA", curve)
    context = QuantlibMarketContext()
    context.set_curve_collection(collection, label="BOND_ZERO")
    return context


def _patch_future_dependencies(
    monkeypatch: pytest.MonkeyPatch, basket: DeliveryBasket, market: QuantlibMarketContext
) -> None:
    monkeypatch.setattr("cqfi.cli_tools.resolve_basket", lambda target: basket)
    monkeypatch.setattr(
        "cqfi.cli_tools._resolve_bond_future_trade_date",
        lambda issuer, trade_date: (TRADE_DATE, None),
    )
    monkeypatch.setattr(
        "cqfi.cli_tools._market_context_for",
        lambda as_of, issuer, curve_label: market,
    )


def test_flat_repo_number_is_a_flat_rate_for_eternity(
    future_basket, future_market, monkeypatch
):
    """A single number should behave exactly like a flat curve at every tenor."""
    _patch_future_dependencies(monkeypatch, future_basket, future_market)

    result = compute_bond_future_analytics("IKU6", numeric_term_structure=3.0)

    assert result["status"] == "success"
    payload = json.loads(result["analytics_json"])
    assert payload["repo_rate"] == pytest.approx(3.0)
    assert payload["analytics"][0]["implied_repo_rate"] == pytest.approx(3.0, abs=1e-8)


def test_repo_dict_matches_the_equivalent_flat_number(
    future_basket, future_market, monkeypatch
):
    _patch_future_dependencies(monkeypatch, future_basket, future_market)

    flat_number = compute_bond_future_analytics("IKU6", numeric_term_structure=3.0)
    flat_dict = compute_bond_future_analytics(
        "IKU6",
        numeric_term_structure={"1m": 3.0, "3m": 3.0, "6m": 3.0, "1y": 3.0},
    )

    assert flat_number["status"] == flat_dict["status"] == "success"
    assert flat_number["analytics_json"] == flat_dict["analytics_json"]


def test_no_repo_falls_back_to_curve_forward(future_basket, future_market, monkeypatch):
    _patch_future_dependencies(monkeypatch, future_basket, future_market)

    result = compute_bond_future_analytics("IKU6")

    assert result["status"] == "success"
    payload = json.loads(result["analytics_json"])
    assert payload["repo_rate"] == pytest.approx(FLAT_RATE * 100.0, abs=0.1)
