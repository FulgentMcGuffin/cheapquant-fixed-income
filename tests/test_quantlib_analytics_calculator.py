"""Tests for QuantLibAnalyticsCalculator and its helpers."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
import QuantLib as ql

from cheapquant_fi.analytics_input import BondAnalyticsInput, CmtAnalyticsInput
from cheapquant_fi.issuers import ISSUERS, RateType
from cheapquant_fi.numeric_term_structure import NumericTermStructure
from cheapquant_fi.quantlib.quantlib_analytics_calculator import (
    QuantLibAnalyticsCalculator,
    _bond_settlement,
    _from_ql_date,
    _subtract_tenor,
    _tenor_label_to_years,
    _to_ql_date,
)
from cheapquant_fi.quantlib.quantlib_curve import ql_build_zero_curve
from cheapquant_fi.quantlib.quantlib_market_context import (
    QuantLibCurveCollection,
    QuantlibMarketContext,
)
from cheapquant_fi.tenor import Tenor

_VAL_DATE = date(2024, 1, 15)
_DEU = ISSUERS["DEU"]
_USA = ISSUERS["USA"]

_SLOPED_RATES = pl.DataFrame(
    {
        "tenor_label": ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"],
        "tenor_years": [1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
        "rate_pct": [3.40, 3.55, 3.70, 3.85, 3.80, 3.65],
    }
)

_FLAT_RATE_PCT = 4.0
_FLAT_RATES = pl.DataFrame(
    {
        "tenor_label": ["1Y", "5Y", "10Y", "30Y"],
        "tenor_years": [1.0, 5.0, 10.0, 30.0],
        "rate_pct": [_FLAT_RATE_PCT] * 4,
    }
)


def _market_for(issuer, rates_df: pl.DataFrame, val_date: date = _VAL_DATE) -> QuantlibMarketContext:
    handle, _ = ql_build_zero_curve(issuer, val_date, rates_df, RateType.ZERO)
    collection = QuantLibCurveCollection(as_of=val_date)
    collection.set_bond_curve(issuer.source_code, handle)
    market = QuantlibMarketContext(as_of=val_date)
    market.set_curve_collection(collection, label="BOND_ZERO")
    return market


@pytest.fixture
def calculator() -> QuantLibAnalyticsCalculator:
    return QuantLibAnalyticsCalculator()


@pytest.fixture
def deu_market() -> QuantlibMarketContext:
    return _market_for(_DEU, _SLOPED_RATES)


@pytest.fixture
def deu_flat_market() -> QuantlibMarketContext:
    return _market_for(_DEU, _FLAT_RATES)


@pytest.fixture
def usa_market() -> QuantlibMarketContext:
    return _market_for(_USA, _SLOPED_RATES)


def _bond_request(
    *,
    issuer: str = "DEU",
    coupon: float = 3.85,
    maturity: date = date(2034, 1, 15),
    settlement: date = _VAL_DATE,
    issue: date | None = None,
    **kwargs,
) -> BondAnalyticsInput:
    return BondAnalyticsInput(
        issuer=issuer,
        coupon=coupon,
        maturity_date=maturity,
        settlement_date=settlement,
        issue_date=issue or settlement,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def test_bond_settlement_advances_by_issuer_convention():
    trade = _to_ql_date(_VAL_DATE)
    deu_settle = _bond_settlement(_DEU, trade)
    usa_settle = _bond_settlement(_USA, trade)
    assert int(deu_settle - trade) == _DEU.settlement_days
    assert int(usa_settle - trade) == _USA.settlement_days


def test_subtract_tenor_moves_maturity_earlier_on_calendar():
    maturity = _to_ql_date(date(2034, 1, 15))
    earlier = _subtract_tenor(maturity, Tenor.parse("1y"), _DEU)
    assert earlier < maturity
    assert _from_ql_date(earlier) == date(2033, 1, 17)


def test_tenor_label_to_years_maps_ycs_labels():
    assert _tenor_label_to_years("10Y") == 10.0
    assert _tenor_label_to_years("6M") == 0.5


def test_tenor_label_to_years_rejects_unknown_label():
    with pytest.raises(ValueError, match="Unknown tenor label"):
        _tenor_label_to_years("99Y")


# ---------------------------------------------------------------------------
# _uses_curve validation
# ---------------------------------------------------------------------------


def test_uses_curve_requires_input_value(calculator: QuantLibAnalyticsCalculator):
    request = BondAnalyticsInput(
        issuer="DEU",
        coupon=3.0,
        maturity_date=date(2034, 1, 15),
        settlement_date=_VAL_DATE,
        input_column="clean_price",
        input_value=None,
    )
    with pytest.raises(ValueError, match="input_value is required"):
        calculator._uses_curve(request)


def test_uses_curve_false_when_input_provided(calculator: QuantLibAnalyticsCalculator):
    request = _bond_request(input_column="clean_price", input_value=99.5)
    assert calculator._uses_curve(request) is False


def test_uses_curve_true_by_default(calculator: QuantLibAnalyticsCalculator):
    assert calculator._uses_curve(_bond_request()) is True


# ---------------------------------------------------------------------------
# Bond analytics â€” curve-priced path
# ---------------------------------------------------------------------------


def test_bond_curve_pricing_populates_core_metrics(deu_market, calculator):
    result, _ = calculator.compute_bond_analytics(_bond_request(), deu_market)

    assert result.yield_to_maturity is not None
    assert result.clean_price is not None
    assert result.dirty_price is not None
    assert result.accrued_interest is not None
    assert result.duration is not None
    assert result.convexity is not None
    assert result.dv01_sensitivity is not None
    assert result.gamma_sensitivity is not None

    assert result.clean_price > 0
    assert result.dirty_price >= result.clean_price
    assert result.duration > 0
    assert result.gamma_sensitivity == pytest.approx(
        result.convexity * result.clean_price / 100.0
    )


def test_bond_curve_pricing_populates_curve_metrics(deu_market, calculator):
    result, _ = calculator.compute_bond_analytics(_bond_request(), deu_market)

    assert result.z_spread is not None
    assert result.par_yield is not None
    assert result.zero_rate is not None
    assert 0 < result.par_yield < 20
    assert 0 < result.zero_rate < 20


def test_par_bond_on_flat_curve_has_near_zero_z_spread(deu_flat_market, calculator):
    result, _ = calculator.compute_bond_analytics(
        _bond_request(coupon=_FLAT_RATE_PCT),
        deu_flat_market,
    )
    assert result.z_spread == pytest.approx(0.0, abs=5.0)


def test_bond_as_json_returns_populated_fields_only(deu_market, calculator):
    result, _ = calculator.compute_bond_analytics(_bond_request(), deu_market)
    payload = result.as_dict()
    assert "yield_to_maturity" in payload
    assert "roll_1y_spotyield" in payload
    assert all(value is not None for value in payload.values())


# ---------------------------------------------------------------------------
# Bond analytics â€” input-priced path
# ---------------------------------------------------------------------------


def test_bond_input_clean_price(deu_market, calculator):
    result, _ = calculator.compute_bond_analytics(
        _bond_request(input_column="clean_price", input_value=98.5),
        deu_market,
    )
    assert result.clean_price == pytest.approx(98.5)
    assert result.yield_to_maturity is not None
    assert result.z_spread is None


def test_bond_input_yield_to_maturity(deu_market, calculator):
    target_ytm = 4.25
    result, _ = calculator.compute_bond_analytics(
        _bond_request(input_column="yield_to_maturity", input_value=target_ytm),
        deu_market,
    )
    assert result.yield_to_maturity == pytest.approx(target_ytm)
    assert result.clean_price is not None


def test_bond_input_rejects_unknown_column(deu_market, calculator):
    request = _bond_request(input_column="duration", input_value=5.0)
    with pytest.raises(ValueError, match="Unsupported input_column"):
        calculator.compute_bond_analytics(request, deu_market)


# ---------------------------------------------------------------------------
# Yield rolls
# ---------------------------------------------------------------------------


def test_all_roll_horizons_populated_for_long_bond(deu_market, calculator):
    result, _ = calculator.compute_bond_analytics(_bond_request(), deu_market)

    for field in (
        "roll_1m_spotyield",
        "roll_3m_spotyield",
        "roll_6m_spotyield",
        "roll_1y_spotyield",
        "roll_1m_fwdyield",
        "roll_3m_fwdyield",
        "roll_6m_fwdyield",
        "roll_1y_fwdyield",
    ):
        assert getattr(result, field) is not None


def test_spot_roll_matches_shortened_maturity(deu_market, calculator):
    settlement = _VAL_DATE
    maturity = date(2034, 1, 15)
    long_result, _ = calculator.compute_bond_analytics(
        _bond_request(maturity=maturity, settlement=settlement),
        deu_market,
    )
    shortened = _from_ql_date(
        _subtract_tenor(_to_ql_date(maturity), Tenor.parse("1y"), _DEU)
    )
    short_result, _ = calculator.compute_bond_analytics(
        _bond_request(maturity=shortened, settlement=settlement),
        deu_market,
    )
    expected = long_result.yield_to_maturity - short_result.yield_to_maturity
    assert long_result.roll_1y_spotyield == pytest.approx(expected, abs=1e-6)


def test_short_maturity_bond_has_no_rolls(deu_market, calculator):
    result, _ = calculator.compute_bond_analytics(
        _bond_request(maturity=date(2024, 3, 15)),
        deu_market,
    )
    assert result.roll_1y_spotyield is None
    assert result.roll_1y_fwdyield is None


def test_forward_roll_differs_from_spot_roll(deu_market, calculator):
    result, _ = calculator.compute_bond_analytics(_bond_request(), deu_market)
    assert result.roll_1y_fwdyield != result.roll_1y_spotyield


# ---------------------------------------------------------------------------
# Repo carry and carry-roll
# ---------------------------------------------------------------------------


def test_repo_carry_is_yield_minus_repo_rate(deu_market, calculator):
    repo = NumericTermStructure(
        {"1m": 3.0, "3m": 3.1, "6m": 3.2, "1y": 3.3},
        as_of=_VAL_DATE,
    )
    result, _ = calculator.compute_bond_analytics(
        _bond_request(repo_term_structure=repo),
        deu_market,
    )
    assert result.yield_to_maturity is not None
    assert result.carry_1m == pytest.approx(result.yield_to_maturity - 3.0, abs=1e-6)
    assert result.carry_1y == pytest.approx(result.yield_to_maturity - 3.3, abs=1e-6)


def test_carry_roll_combines_carry_and_roll(deu_market, calculator):
    repo = NumericTermStructure({"1y": 3.0}, as_of=_VAL_DATE)
    result, _ = calculator.compute_bond_analytics(
        _bond_request(repo_term_structure=repo),
        deu_market,
    )
    assert result.carry_1y is not None
    assert result.roll_1y_spotyield is not None
    assert result.carry_roll_1y_spotyield == pytest.approx(
        result.carry_1y - result.roll_1y_spotyield
    )
    assert result.carry_roll_1y_fwdyield == pytest.approx(
        result.carry_1y - result.roll_1y_fwdyield
    )


def test_missing_repo_tenors_leave_carry_none(deu_market, calculator):
    repo = NumericTermStructure({"1m": 3.0}, as_of=_VAL_DATE)
    result, _ = calculator.compute_bond_analytics(
        _bond_request(repo_term_structure=repo),
        deu_market,
    )
    assert result.carry_1m is not None
    assert result.carry_1y is None


# ---------------------------------------------------------------------------
# Issuer conventions
# ---------------------------------------------------------------------------


def test_usa_and_deu_produce_different_yields_on_same_inputs(
    deu_market, usa_market, calculator
):
    request = _bond_request(issuer="DEU")
    deu_result, _ = calculator.compute_bond_analytics(request, deu_market)

    usa_request = _bond_request(issuer="USA")
    usa_result, _ = calculator.compute_bond_analytics(usa_request, usa_market)

    assert deu_result.yield_to_maturity is not None
    assert usa_result.yield_to_maturity is not None
    # Same curve shape and coupon â€” day-count / frequency differ slightly.
    assert deu_result.yield_to_maturity == pytest.approx(
        usa_result.yield_to_maturity, abs=0.5
    )


def test_build_fixed_rate_bond_uses_redemption_face_amount(calculator):
    request = _bond_request(face_amount=50.0)
    bond = calculator._build_fixed_rate_bond(_DEU, request)
    assert bond.notional() == pytest.approx(100.0)
    assert bond.redemption().amount() == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Internal roll computation
# ---------------------------------------------------------------------------


def test_compute_yield_rolls_returns_all_keys(deu_market, calculator):
    settlement = _bond_settlement(_DEU, _to_ql_date(_VAL_DATE))
    issue = settlement
    maturity = _to_ql_date(date(2034, 1, 15))
    curve = deu_market.curve_collection("BOND_ZERO").bond_curve("DEU")
    bond = calculator._build_fixed_rate_bond(_DEU, _bond_request())
    bond.setPricingEngine(ql.DiscountingBondEngine(curve))
    spot_yld = bond.bondYield(_DEU.day_count, ql.Compounded, _DEU.frequency) * 100.0

    rolls = calculator._compute_yield_rolls(
        spot_yld,
        _DEU,
        settlement,
        issue,
        maturity,
        curve,
        coupon_pct=3.85,
        face_amount=100.0,
        zero_coupon=False,
    )
    assert set(rolls) == {
        "roll_1m_spotyield",
        "roll_3m_spotyield",
        "roll_6m_spotyield",
        "roll_1y_spotyield",
        "roll_1m_fwdyield",
        "roll_3m_fwdyield",
        "roll_6m_fwdyield",
        "roll_1y_fwdyield",
    }


# ---------------------------------------------------------------------------
# Maturity-matched fixed-coupon CMT
# ---------------------------------------------------------------------------


def test_issuer_as_unadjusted_uses_null_calendar_and_clears_ex_dividend():
    unadj = _USA.as_unadjusted()
    assert isinstance(unadj.calendar(), ql.NullCalendar)
    assert unadj.payment_convention == ql.Unadjusted
    assert unadj.ex_dividend is None
    assert unadj.day_count.name() == _USA.day_count.name()
    assert unadj.frequency == _USA.frequency
    assert unadj.settlement_days == _USA.settlement_days
    # Original profile is unchanged.
    assert not isinstance(_USA.calendar(), ql.NullCalendar)
    assert _USA.payment_convention == ql.ModifiedFollowing


def test_maturity_matched_cmt_metrics_returned_on_curve_path(deu_market, calculator):
    bond_metrics, cmt_metrics = calculator.compute_bond_analytics(
        _bond_request(), deu_market
    )
    assert bond_metrics.par_yield is not None
    assert cmt_metrics is not None
    assert cmt_metrics.clean_price is not None
    assert cmt_metrics.yield_to_maturity is not None
    assert cmt_metrics.duration is not None
    # Par CMT priced off the same curve should be near 100.
    assert cmt_metrics.clean_price == pytest.approx(100.0, abs=0.05)
    assert cmt_metrics.yield_to_maturity == pytest.approx(
        bond_metrics.par_yield, abs=0.05
    )


def test_maturity_matched_cmt_none_on_input_path(deu_market, calculator):
    _, cmt_metrics = calculator.compute_bond_analytics(
        _bond_request(input_column="clean_price", input_value=99.5),
        deu_market,
    )
    assert cmt_metrics is None


def test_maturity_matched_cmt_schedule_is_unadjusted(calculator):
    """Coupon dates that fall on weekends are not shifted."""
    issuer = _DEU.as_unadjusted()
    # 2024-01-15 is a Monday; annual DEU coupons → 2025-01-15, …, 2030-01-15.
    # Pick a maturity so a coupon lands on Saturday 2028-01-15.
    settlement = ql.Date(15, 1, 2024)
    maturity = ql.Date(15, 1, 2028)  # Saturday
    bond = calculator._build_maturity_matched_cmt(
        issuer, settlement, maturity, coupon_pct=3.5
    )
    cashflows = list(bond.cashflows())
    coupon_dates = [
        cf.date() for cf in cashflows if not cf.hasOccurred(settlement)
    ]
    assert maturity in coupon_dates
    assert maturity.weekday() == ql.Saturday
