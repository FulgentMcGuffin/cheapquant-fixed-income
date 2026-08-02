"""Exchange-specific conversion factors for bond future delivery baskets.

A conversion factor is the price, per unit nominal, at which a deliverable bond
yields the contract's notional coupon on the delivery month's reference date.
Each exchange publishes its own algebra, so :data:`CONVERSION_FACTOR_FUNCTIONS`
dispatches on :class:`~cqfi.bond_futures.ConversionFactorMethod`.

Coupon schedules here are always the *regular* schedule generated backwards
from maturity, which is what the exchanges standardise on — an irregular first
coupon affects pricing and accrued interest but not the published factor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

import QuantLib as ql

from cqfi.bond_futures import (
    MONTHS_PER_YEAR,
    BondFuture,
    BondFutureConvention,
    ConversionFactorMethod,
)
from cqfi.date_utils import (
    add_months,
    to_ql_date,
    whole_months_between,
)
from cqfi.instruments import Bond
from cqfi.issuers import IssuerProfile

# CME's published formula is fixed at a 6% notional yield and semi-annual
# coupons; the quarter-rounding below is meaningless at any other rate.
_CME_SEMIANNUAL_YIELD = 0.03
_MONTHS_PER_QUARTER = 3


class ConversionFactorError(ValueError):
    """Raised when a conversion factor cannot be computed for a bond."""


@dataclass(frozen=True)
class CouponTiming:
    """Where a reference date sits within a bond's regular coupon schedule.

    Attributes:
        periods_to_next: Fraction of a coupon period until the next coupon,
            in ``(0, 1]``.  Exactly ``1`` when the reference date is itself a
            coupon date.
        full_periods_after_next: Whole coupon periods between the next coupon
            and maturity, so ``full_periods_after_next + 1`` coupons remain.
        next_coupon_date: The next coupon date strictly after the reference.
    """

    periods_to_next: float
    full_periods_after_next: int
    next_coupon_date: date


def coupon_dates_after(reference: date, maturity: date, frequency: int) -> list[date]:
    """Return the regular coupon dates strictly after *reference*.

    Dates are generated backwards from *maturity*, the convention every
    exchange uses when standardising a deliverable's schedule.

    Args:
        reference: Date to generate forward from.
        maturity: Bond redemption date.
        frequency: Coupon payments per year.

    Returns:
        Ascending coupon dates, ending at *maturity*.

    Raises:
        ConversionFactorError: If *maturity* is not after *reference*.
    """
    if maturity <= reference:
        raise ConversionFactorError(
            f"maturity {maturity.isoformat()} is not after the reference date "
            f"{reference.isoformat()}"
        )
    step = MONTHS_PER_YEAR // frequency
    dates: list[date] = []
    current = maturity
    while current > reference:
        dates.append(current)
        current = add_months(current, -step)
    return list(reversed(dates))


def coupon_timing(reference: date, maturity: date, frequency: int) -> CouponTiming:
    """Locate *reference* within a bond's regular coupon schedule."""
    upcoming = coupon_dates_after(reference, maturity, frequency)
    next_coupon = upcoming[0]
    previous = add_months(next_coupon, -(MONTHS_PER_YEAR // frequency))
    period_days = (next_coupon - previous).days
    return CouponTiming(
        periods_to_next=(next_coupon - reference).days / period_days,
        full_periods_after_next=len(upcoming) - 1,
        next_coupon_date=next_coupon,
    )


def price_at_notional_yield(
    coupon_pct: float,
    notional_coupon_pct: float,
    frequency: int,
    timing: CouponTiming,
    *,
    next_coupon_received: bool = True,
) -> float:
    """Return the clean price per unit nominal at the notional yield.

    This is the conversion factor for every exchange whose factor is defined
    by discounting (Eurex, ICE, JGB); only the inputs differ.

    Args:
        coupon_pct: The bond's actual coupon, in percent.
        notional_coupon_pct: The contract's notional coupon, in percent.
        frequency: Coupon payments per year.
        timing: Where the reference date sits in the coupon schedule.
        next_coupon_received: ``False`` when the reference date falls inside
            an ex-dividend window, so the buyer forgoes the next coupon.

    Returns:
        Clean price per unit nominal, i.e. ``1.0`` for a bond priced at par.
    """
    periodic_yield = notional_coupon_pct / 100.0 / frequency
    periodic_coupon = coupon_pct / 100.0 / frequency
    fraction = timing.periods_to_next
    remaining = timing.full_periods_after_next

    discount_to_next = (1.0 + periodic_yield) ** -fraction
    discount_remaining = (1.0 + periodic_yield) ** -remaining
    annuity = (
        remaining
        if periodic_yield == 0.0
        else (1.0 - discount_remaining) / periodic_yield
    )

    # Coupons fall at fraction, fraction+1, ... fraction+remaining periods; the
    # first is dropped when the bond has gone ex-dividend.
    coupon_leg = periodic_coupon * (annuity + (1.0 if next_coupon_received else 0.0))
    dirty = discount_to_next * (coupon_leg + discount_remaining)

    # Accrued is positive over the elapsed part of the period, and negative
    # inside an ex-dividend window where the coupon is rebated to the seller.
    accrued = (
        periodic_coupon * (1.0 - fraction)
        if next_coupon_received
        else -periodic_coupon * fraction
    )
    return dirty - accrued


def round_conversion_factor(value: float, decimals: int) -> float:
    """Round half-up to *decimals*, as exchanges publish factors."""
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# Per-exchange formulas
# --------------------------------------------------------------------------- #
def cme_conversion_factor(
    bond: Bond, convention: BondFutureConvention, reference_date: date
) -> float:
    """Return the CME published conversion factor.

    CME defines the factor by a closed form rather than by discounting: the
    bond's remaining term is truncated to whole years plus whole quarters,
    then priced to yield 6% semi-annually.

    Args:
        bond: The deliverable bond.
        convention: The contract terms.
        reference_date: The unadjusted first day of the delivery month.

    Returns:
        The factor, rounded to the contract's published precision.
    """
    coupon = _coupon_rate(bond)
    term_months = whole_months_between(reference_date, bond.maturity)
    whole_years = term_months // MONTHS_PER_YEAR
    # Residual months, truncated down to a whole quarter: 0, 3, 6 or 9.
    quarters = (
        term_months % MONTHS_PER_YEAR // _MONTHS_PER_QUARTER
    ) * _MONTHS_PER_QUARTER

    offset = quarters if quarters < 7 else quarters - 6
    discount_offset = (1.0 + _CME_SEMIANNUAL_YIELD) ** (-offset / 6.0)
    accrued = (coupon / 2.0) * (6 - offset) / 6.0
    periods = 2 * whole_years + (0 if quarters < 7 else 1)
    discount_term = (1.0 + _CME_SEMIANNUAL_YIELD) ** -periods
    annuity = (coupon / (2.0 * _CME_SEMIANNUAL_YIELD)) * (1.0 - discount_term)

    factor = discount_offset * (coupon / 2.0 + discount_term + annuity) - accrued
    return round_conversion_factor(factor, convention.cf_decimals)


def eurex_conversion_factor(
    bond: Bond, convention: BondFutureConvention, reference_date: date
) -> float:
    """Return the Eurex conversion factor by exact discounting to delivery."""
    issuer = convention.issuer()
    timing = coupon_timing(reference_date, bond.maturity, issuer.frequency)
    factor = price_at_notional_yield(
        _coupon_rate(bond) * 100.0,
        convention.notional_coupon,
        issuer.frequency,
        timing,
    )
    return round_conversion_factor(factor, convention.cf_decimals)


def ice_conversion_factor(
    bond: Bond, convention: BondFutureConvention, reference_date: date
) -> float:
    """Return the ICE gilt price factor, honouring the ex-dividend window."""
    issuer = convention.issuer()
    timing = coupon_timing(reference_date, bond.maturity, issuer.frequency)
    factor = price_at_notional_yield(
        _coupon_rate(bond) * 100.0,
        convention.notional_coupon,
        issuer.frequency,
        timing,
        next_coupon_received=not _is_ex_dividend(issuer, reference_date, timing),
    )
    return round_conversion_factor(factor, convention.cf_decimals)


def jgb_conversion_factor(
    bond: Bond, convention: BondFutureConvention, reference_date: date
) -> float:
    """Return the Osaka Exchange JGB conversion factor.

    Osaka truncates the residual term to whole months before discounting, so
    the coupon timing is derived from that rounded term rather than from the
    bond's actual dates.
    """
    issuer = convention.issuer()
    truncated_maturity = add_months(
        reference_date, whole_months_between(reference_date, bond.maturity)
    )
    timing = coupon_timing(reference_date, truncated_maturity, issuer.frequency)
    factor = price_at_notional_yield(
        _coupon_rate(bond) * 100.0,
        convention.notional_coupon,
        issuer.frequency,
        timing,
    )
    return round_conversion_factor(factor, convention.cf_decimals)


CONVERSION_FACTOR_FUNCTIONS: dict[
    ConversionFactorMethod, Callable[[Bond, BondFutureConvention, date], float]
] = {
    ConversionFactorMethod.CME: cme_conversion_factor,
    ConversionFactorMethod.EUREX: eurex_conversion_factor,
    ConversionFactorMethod.ICE: ice_conversion_factor,
    ConversionFactorMethod.JGB: jgb_conversion_factor,
}


def conversion_factor(bond: Bond, bond_future: BondFuture) -> float:
    """Return the conversion factor for *bond* delivered into *bond_future*.

    Args:
        bond: The deliverable bond.
        bond_future: The dated contract, which supplies both the formula and
            the reference date.

    Returns:
        The factor, rounded to the contract's published precision.

    Raises:
        ConversionFactorError: If the bond matures before the reference date.
    """
    convention = bond_future.convention
    compute = CONVERSION_FACTOR_FUNCTIONS[convention.conversion_factor_method]
    return compute(bond, convention, bond_future.reference_date())


def _coupon_rate(bond: Bond) -> float:
    """Return the bond's coupon as a decimal rate, treating ``None`` as zero."""
    return 0.0 if bond.coupon is None else bond.coupon / 100.0


def _is_ex_dividend(
    issuer: IssuerProfile, reference_date: date, timing: CouponTiming
) -> bool:
    """Whether *reference_date* falls inside the issuer's ex-dividend window.

    Uses the issuer's own ex-dividend convention so the window is defined in
    exactly one place.
    """
    convention = issuer.ex_dividend
    if convention is None:
        return False
    ex_date = convention.calendar().advance(
        to_ql_date(timing.next_coupon_date),
        -convention.period,
        convention.convention,
        convention.end_of_month,
    )
    return to_ql_date(reference_date) >= ex_date
