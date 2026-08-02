"""Tests for exchange-specific conversion factor formulas."""

from __future__ import annotations

from datetime import date

import QuantLib as ql
import pytest

from cqfi.bond_futures import BOND_FUTURE_CONVENTIONS, BondFuture
from cqfi.date_utils import add_months, to_ql_date, whole_months_between
from cqfi.instruments import Bond
from cqfi.quantlib.quantlib_conversion_factor import (
    ConversionFactorError,
    conversion_factor,
    coupon_dates_after,
    coupon_timing,
    price_at_notional_yield,
    round_conversion_factor,
)

# Contracts under test, with their reference dates.
ZN_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["ZN"], 9, 2026)  # ref 2026-09-01
FBTP_U6 = BondFuture(
    BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2026
)  # ref 2026-09-10, semiannual
FGBL_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["FGBL"], 9, 2026)  # ref 2026-09-10, annual
FJGB_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["FJGB"], 9, 2026)
R_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["R"], 9, 2026)  # ref 2026-09-01, 4% gilt

ISMA = ql.ActualActual(ql.ActualActual.ISMA)


def _bond(issuer: str, coupon: float, maturity: date) -> Bond:
    return Bond(issuer=issuer, maturity=maturity, bond_id="B", coupon=coupon)


def _quantlib_price_at_yield(
    coupon: float, maturity: date, issue: date, notional_yield: float, frequency: int
) -> float:
    """Price a regularly-scheduled bond at *notional_yield*, per unit nominal."""
    reference = to_ql_date(FBTP_U6.reference_date())
    ql.Settings.instance().evaluationDate = reference
    schedule = ql.Schedule(
        to_ql_date(issue),
        to_ql_date(maturity),
        ql.Period(frequency),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    bond = ql.FixedRateBond(0, 100.0, schedule, [coupon / 100.0], ISMA)
    return (
        ql.BondFunctions.cleanPrice(
            bond, notional_yield, ISMA, ql.Compounded, frequency, reference
        )
        / 100.0
    )


# --------------------------------------------------------------------------- #
# Coupon schedule helpers
# --------------------------------------------------------------------------- #
def test_coupon_dates_are_generated_backwards_from_maturity():
    dates = coupon_dates_after(date(2026, 9, 10), date(2029, 3, 1), frequency=2)
    assert dates == [
        date(2027, 3, 1),
        date(2027, 9, 1),
        date(2028, 3, 1),
        date(2028, 9, 1),
        date(2029, 3, 1),
    ]


def test_reference_on_a_coupon_date_leaves_a_whole_period():
    timing = coupon_timing(date(2026, 9, 10), date(2036, 9, 10), frequency=2)
    assert timing.periods_to_next == pytest.approx(1.0)
    assert timing.next_coupon_date == date(2027, 3, 10)
    assert timing.full_periods_after_next == 19


def test_matured_bond_is_rejected():
    with pytest.raises(ConversionFactorError, match="not after the reference"):
        coupon_dates_after(date(2026, 9, 10), date(2026, 9, 10), frequency=2)
    with pytest.raises(ConversionFactorError):
        conversion_factor(_bond("ITA", 3.0, date(2020, 1, 1)), FBTP_U6)


# --------------------------------------------------------------------------- #
# The discounting formula
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("coupon", [0.0, 0.95, 2.5, 4.75, 6.0, 9.5])
def test_closed_form_matches_quantlib_clean_price(coupon):
    """The published algebra and QuantLib's pricer must agree exactly."""
    maturity, issue = date(2035, 3, 1), date(2015, 3, 1)
    timing = coupon_timing(FBTP_U6.reference_date(), maturity, frequency=2)

    mine = price_at_notional_yield(coupon, 6.0, 2, timing)
    reference = _quantlib_price_at_yield(coupon, maturity, issue, 0.06, ql.Semiannual)
    assert mine == pytest.approx(reference, abs=1e-12)


@pytest.mark.parametrize(
    ("contract", "issuer", "maturity"),
    [
        (FBTP_U6, "ITA", date(2036, 9, 10)),  # semi-annual
        (FGBL_U6, "DEU", date(2036, 9, 10)),  # annual
    ],
)
def test_par_bond_on_a_coupon_anniversary_gives_exactly_one(contract, issuer, maturity):
    """A bond whose coupon equals the notional coupon converts one-for-one."""
    bond = _bond(issuer, contract.convention.notional_coupon, maturity)
    assert conversion_factor(bond, contract) == 1.0


def test_factor_moves_the_right_way_with_the_coupon():
    """Above the notional coupon the factor exceeds one, and vice versa."""
    maturity = date(2036, 9, 10)
    low = conversion_factor(_bond("ITA", 2.0, maturity), FBTP_U6)
    par = conversion_factor(_bond("ITA", 6.0, maturity), FBTP_U6)
    high = conversion_factor(_bond("ITA", 9.0, maturity), FBTP_U6)
    assert low < par == 1.0 < high


def test_zero_notional_yield_degenerates_to_undiscounted_cash():
    """The annuity branch must not divide by zero."""
    timing = coupon_timing(date(2026, 9, 10), date(2029, 9, 10), frequency=1)
    assert price_at_notional_yield(5.0, 0.0, 1, timing) == pytest.approx(1.15)


# --------------------------------------------------------------------------- #
# CME
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("coupon", [0.0, 2.5, 6.0, 8.75])
@pytest.mark.parametrize("extra_months", [0, 6])
def test_cme_reduces_to_exact_discounting_at_whole_half_years(coupon, extra_months):
    """With no quarter truncation, CME's closed form is plain discounting."""
    reference = ZN_U6.reference_date()
    maturity = add_months(
        date(reference.year + 8, reference.month, reference.day), extra_months
    )

    factor = conversion_factor(_bond("USA", coupon, maturity), ZN_U6)
    exact = round_conversion_factor(
        price_at_notional_yield(coupon, 6.0, 2, coupon_timing(reference, maturity, 2)),
        4,
    )
    assert factor == exact


def test_cme_par_bond_converts_one_for_one():
    reference = ZN_U6.reference_date()
    maturity = date(reference.year + 9, reference.month, reference.day)
    assert conversion_factor(_bond("USA", 6.0, maturity), ZN_U6) == 1.0


def test_cme_truncates_the_residual_term_to_whole_quarters():
    """Maturities inside the same quarter share a conversion factor."""
    factors = [
        conversion_factor(
            _bond("USA", 4.0, add_months(date(2036, 10, 1), offset)), ZN_U6
        )
        for offset in range(6)
    ]
    assert factors[0] == factors[1]  # Oct, Nov -> same quarter
    assert factors[2] == factors[3] == factors[4]  # Dec, Jan, Feb -> same quarter
    assert factors[0] != factors[2] != factors[5]  # quarters differ


def test_cme_publishes_four_decimals():
    factor = conversion_factor(_bond("USA", 3.375, date(2036, 5, 15)), ZN_U6)
    assert factor == round(factor, 4)


# --------------------------------------------------------------------------- #
# Eurex, ICE and JGB precision
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("contract", "issuer", "decimals"),
    [
        (FBTP_U6, "ITA", 6),
        (FGBL_U6, "DEU", 6),
        (R_U6, "GBR", 7),
        (FJGB_U6, "JPN", 6),
        (ZN_U6, "USA", 4),
    ],
)
def test_published_precision_is_honoured(contract, issuer, decimals):
    assert contract.convention.cf_decimals == decimals
    factor = conversion_factor(_bond(issuer, 3.375, date(2035, 6, 7)), contract)
    assert factor == round(factor, decimals)


def test_rounding_is_half_up_not_bankers():
    assert round_conversion_factor(1.00005, 4) == 1.0001
    assert round_conversion_factor(1.00015, 4) == 1.0002
    # Python's built-in round() would give 1.0 for both of these.
    assert round_conversion_factor(0.5, 0) == 1.0
    assert round_conversion_factor(1.5, 0) == 2.0


# --------------------------------------------------------------------------- #
# ICE gilts and ex-dividend
# --------------------------------------------------------------------------- #
def test_ice_uses_the_four_percent_notional_coupon():
    """A 4% gilt converts one-for-one into the Long Gilt contract."""
    reference = R_U6.reference_date()
    maturity = date(reference.year + 10, reference.month, reference.day)
    assert R_U6.convention.notional_coupon == 4.0
    assert conversion_factor(_bond("GBR", 4.0, maturity), R_U6) == 1.0


def test_ex_dividend_window_drops_the_next_coupon():
    """Inside the window the buyer forgoes the coupon, lowering the factor."""
    reference = R_U6.reference_date()
    assert reference == date(2026, 9, 1)

    # A gilt paying on 2026-09-07 puts the 2026-09-01 reference date inside
    # the 7-day ex-dividend window; one paying later does not.
    inside = conversion_factor(_bond("GBR", 8.0, date(2036, 9, 7)), R_U6)
    outside = conversion_factor(_bond("GBR", 8.0, date(2036, 9, 30)), R_U6)
    assert inside < outside


def test_ex_dividend_only_applies_to_issuers_that_define_a_window():
    """Italy has no ex-dividend convention, so nothing is ever dropped."""
    from cqfi.issuers import ISSUERS

    assert ISSUERS["ITA"].ex_dividend is None
    assert ISSUERS["GBR"].ex_dividend is not None
    # A BTP maturing days after the reference date still counts its coupon.
    assert conversion_factor(_bond("ITA", 6.0, date(2036, 9, 10)), FBTP_U6) == 1.0


# --------------------------------------------------------------------------- #
# JGB
# --------------------------------------------------------------------------- #
def test_jgb_truncates_the_residual_term_to_whole_months():
    """Maturities sharing a whole-month term share a conversion factor.

    Buckets are anchored on the reference date's day of month, not on
    calendar month boundaries.
    """
    reference = FJGB_U6.reference_date()
    bucket = [date(2035, 3, 24), date(2035, 3, 30), date(2035, 4, 23)]
    assert {whole_months_between(reference, m) for m in bucket} == {102}

    factors = {
        conversion_factor(_bond("JPN", 2.0, maturity), FJGB_U6) for maturity in bucket
    }
    assert len(factors) == 1

    # One day later crosses into the next whole month and the factor moves.
    next_bucket = conversion_factor(_bond("JPN", 2.0, date(2035, 4, 24)), FJGB_U6)
    assert whole_months_between(reference, date(2035, 4, 24)) == 103
    assert next_bucket not in factors


def test_jgb_five_year_uses_a_three_percent_notional_coupon():
    contract = BondFuture(BOND_FUTURE_CONVENTIONS["FJG5"], 9, 2026)
    assert contract.convention.notional_coupon == 3.0
    reference = contract.reference_date()
    maturity = date(reference.year + 5, reference.month, reference.day)
    assert conversion_factor(_bond("JPN", 3.0, maturity), contract) == 1.0


def test_domestic_and_international_jgb_factors_are_identical():
    """The repo market changes financing, never the conversion factor."""
    bond = _bond("JPN", 2.0, date(2035, 9, 20))
    international = BondFuture(BOND_FUTURE_CONVENTIONS["FJGB"], 9, 2026)
    domestic = BondFuture(BOND_FUTURE_CONVENTIONS["FJGB_DOM"], 9, 2026)
    assert conversion_factor(bond, international) == conversion_factor(bond, domestic)


# --------------------------------------------------------------------------- #
# Missing data
# --------------------------------------------------------------------------- #
def test_missing_coupon_is_treated_as_a_zero_coupon_bond():
    stripped = Bond(issuer="ITA", maturity=date(2036, 9, 10), bond_id="Z", coupon=None)
    explicit = _bond("ITA", 0.0, date(2036, 9, 10))
    assert conversion_factor(stripped, FBTP_U6) == conversion_factor(explicit, FBTP_U6)
