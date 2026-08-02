"""Tests for persisting bond future analytics into quant_cache_db."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import QuantLib as ql
import pytest

from cqfi.bond_future_input import BondFutureInput
from cqfi.bond_futures import BOND_FUTURE_CONVENTIONS, BondFuture
from cqfi.cache.decorators import cache_bond_future_analytics
from cqfi.cache.registry import CacheRegistry, reset_cache_registry
from cqfi.config import get_runtime_settings, load_runtime_settings
from cqfi.date_utils import to_ql_date
from cqfi.delivery_basket import DeliveryBasket
from cqfi.instruments import Bond
from cqfi.numeric_term_structure import NumericTermStructure
from cqfi.quantlib.quantlib_bond_future_calculator import QuantLibBondFutureCalculator
from cqfi.quantlib.quantlib_market_context import (
    QuantLibCurveCollection,
    QuantlibMarketContext,
)

TRADE_DATE = date(2026, 5, 15)
FBTP_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2026)


@pytest.fixture
def basket() -> DeliveryBasket:
    basket = DeliveryBasket(bond_future=FBTP_U6)
    bond = Bond(
        issuer="ITA",
        maturity=date(2035, 4, 1),
        bond_id="IT203504",
        user_friendly_id="ita203504",
        coupon=2.5,
        issue_date=date(2015, 4, 1),
    )
    basket.add(bond)
    override = NumericTermStructure({"3m": 1.0, "1y": 1.0}, TRADE_DATE)
    basket.set_repo_term_structure(bond, override)
    return basket


@pytest.fixture
def market() -> QuantlibMarketContext:
    ql.Settings.instance().evaluationDate = to_ql_date(TRADE_DATE)
    curve = ql.YieldTermStructureHandle(
        ql.FlatForward(
            to_ql_date(TRADE_DATE),
            0.03,
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


@pytest.fixture
def quant_cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_cache_registry()
    quant_cache_db = tmp_path / "quant_cache.sqlite"
    quant_sem = tmp_path / "quant_cache.yaml"
    quant_sem.write_text(
        Path("semantics/quant_cache.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runtime_path = tmp_path / "runtime.json"
    monkeypatch.setenv("CQFI_RUNTIME_CONFIG", str(runtime_path))
    load_runtime_settings(runtime_path)
    get_runtime_settings().update(use_quant_cache=True)
    yield quant_cache_db, quant_sem
    reset_cache_registry()


def _count_rows(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def test_persist_bond_future_compute_writes_basket_and_outputs(
    quant_cache_env, basket, market
) -> None:
    db_path, semantics = quant_cache_env
    reg = CacheRegistry(db_path, semantics)
    request = BondFutureInput.from_basket(basket, TRADE_DATE)
    result = QuantLibBondFutureCalculator().compute_bond_future_analytics(request, market)
    reg.persist_bond_future_compute(
        owner="Test",
        method="compute_bond_future_analytics",
        request=request,
        result=result,
    )
    reg.close()

    assert _count_rows(db_path, "bond_future_basket_outputs") == 1
    assert _count_rows(db_path, "bond_future_outputs") == 1

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        basket_row = conn.execute(
            "SELECT * FROM bond_future_basket_outputs"
        ).fetchone()
        output_row = conn.execute("SELECT * FROM bond_future_outputs").fetchone()

    assert basket_row["convention_id"] == "FBTP"
    assert basket_row["delivery_month"] == "U2026"
    assert basket_row["trade_date"] == TRADE_DATE.isoformat()
    assert output_row["bond_id"] == "IT203504"
    assert output_row["repo_term_structure_json"] is not None
    override = json.loads(output_row["repo_term_structure_json"])
    assert override["3m"] == pytest.approx(1.0)
    assert output_row["repo_rate"] == pytest.approx(1.0)


def test_cache_bond_future_analytics_decorator_calls_registry(
    monkeypatch, quant_cache_env, basket, market
) -> None:
    db_path, semantics = quant_cache_env
    request = BondFutureInput.from_basket(basket, TRADE_DATE)
    get_runtime_settings().update(use_quant_cache=False)
    expected = QuantLibBondFutureCalculator().compute_bond_future_analytics(request, market)

    mock_reg = MagicMock(wraps=CacheRegistry(db_path, semantics))
    monkeypatch.setattr(
        "cqfi.cache.decorators.get_cache_registry",
        lambda: mock_reg,
    )
    get_runtime_settings().update(use_quant_cache=True)

    def _fake_compute(self, req, mkt, *, curve_label="BOND_ZERO"):
        return expected

    class _Calc:
        compute_bond_future_analytics = cache_bond_future_analytics(_fake_compute)

    _Calc().compute_bond_future_analytics(request, market)
    mock_reg.persist_bond_future_compute.assert_called_once()


def test_reset_analytics_tables_clears_bond_future_rows(
    quant_cache_env, basket, market
) -> None:
    db_path, semantics = quant_cache_env
    reg = CacheRegistry(db_path, semantics)
    request = BondFutureInput.from_basket(basket, TRADE_DATE)
    result = QuantLibBondFutureCalculator().compute_bond_future_analytics(request, market)
    reg.persist_bond_future_compute(
        owner="Test",
        method="compute_bond_future_analytics",
        request=request,
        result=result,
    )
    reg.reset_analytics_tables()
    reg.close()

    assert _count_rows(db_path, "bond_future_basket_outputs") == 0
    assert _count_rows(db_path, "bond_future_outputs") == 0
