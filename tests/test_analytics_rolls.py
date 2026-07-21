"""Tests for yield roll calculations in QuantLibAnalyticsCalculator."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from cheapquant_fi.analytics_input import BondAnalyticsInput
from cheapquant_fi.issuers import ISSUERS, RateType
from cheapquant_fi.quantlib.quantlib_analytics_calculator import (
    QuantLibAnalyticsCalculator,
    _from_ql_date,
    _subtract_tenor,
    _to_ql_date,
)
from cheapquant_fi.quantlib.quantlib_curve import ql_build_zero_curve
from cheapquant_fi.quantlib.quantlib_market_context import (
    QuantLibCurveCollection,
    QuantlibMarketContext,
)
from cheapquant_fi.tenor import Tenor


@pytest.fixture
def deu_market():
    issuer = ISSUERS["DEU"]
    val_date = date(2024, 1, 15)
    rates_df = pl.DataFrame(
        {
            "tenor_label": ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"],
            "tenor_years": [1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
            "rate_pct": [3.40, 3.55, 3.70, 3.85, 3.80, 3.65],
        }
    )
    handle, _ = ql_build_zero_curve(issuer, val_date, rates_df, RateType.ZERO)
    collection = QuantLibCurveCollection(as_of=val_date)
    collection.set_bond_curve("DEU", handle)
    market = QuantlibMarketContext(as_of=val_date)
    market.set_curve_collection(collection, label="BOND_ZERO")
    return market


def test_bond_yield_rolls_are_populated(deu_market):
    request = BondAnalyticsInput(
        issuer="DEU",
        coupon=3.85,
        maturity_date=date(2034, 1, 15),
        settlement_date=date(2024, 1, 15),
        issue_date=date(2024, 1, 15),
    )
    metrics = QuantLibAnalyticsCalculator().compute_bond_analytics(
        request,
        deu_market,
    )

    assert metrics.roll_1y_spotyield is not None
    assert metrics.roll_1y_fwdyield is not None
    assert metrics.roll_1m_spotyield is not None
    assert metrics.roll_3m_fwdyield is not None


def test_spot_roll_matches_shortened_maturity_example(deu_market):
    """10y bond 1y spot roll equals spot YTM minus 9y bond YTM."""
    calculator = QuantLibAnalyticsCalculator()
    settlement = date(2024, 1, 15)
    maturity = date(2034, 1, 15)
    request = BondAnalyticsInput(
        issuer="DEU",
        coupon=3.85,
        maturity_date=maturity,
        settlement_date=settlement,
        issue_date=settlement,
    )
    metrics = calculator.compute_bond_analytics(request, deu_market)

    shortened_maturity = _from_ql_date(
        _subtract_tenor(_to_ql_date(maturity), Tenor.parse("1y"), ISSUERS["DEU"])
    )
    shorter = BondAnalyticsInput(
        issuer="DEU",
        coupon=3.85,
        maturity_date=shortened_maturity,
        settlement_date=settlement,
        issue_date=settlement,
    )
    shorter_metrics = calculator.compute_bond_analytics(shorter, deu_market)

    assert metrics.yield_to_maturity is not None
    assert shorter_metrics.yield_to_maturity is not None
    expected = metrics.yield_to_maturity - shorter_metrics.yield_to_maturity
    assert metrics.roll_1y_spotyield == pytest.approx(expected, abs=1e-6)
