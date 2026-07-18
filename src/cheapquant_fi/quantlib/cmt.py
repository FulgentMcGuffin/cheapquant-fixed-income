"""Constant-maturity treasury (CMT) pricing from a bootstrapped curve."""

from __future__ import annotations

from datetime import date

import QuantLib as ql
import polars as pl

from cheapquant_fi.data.rates_loader import load_curve_rates
from cheapquant_fi.issuers import IssuerProfile, RateType, resolve_issuer
from cheapquant_fi.quantlib.quantlib_curve import QLZeroInterp, ql_build_zero_curve


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def ql_price_cmts_from_rates(
    issuer: IssuerProfile,
    valuation_date: date,
    rates_df: pl.DataFrame,
    rate_type: RateType = RateType.ZERO,
    interpolation: QLZeroInterp | None = None,
    *,
    bspline_knots: list[float] | None = None,
    poly_degree: int = 3,
) -> pl.DataFrame:
    """Price synthetic zero-coupon CMTs at each pillar tenor.

    Returns a polars DataFrame with clean price, yield, and curve-implied zero.

    Parameters
    ----------
    interpolation:
        Curve method passed through to :func:`build_zero_curve`.  ``None`` uses
        the rate-type default (``CUBIC_ZERO`` for zero, ``LINEAR_ZERO`` for par).
    bspline_knots:
        Custom knot vector for ``ZeroInterp.CUBIC_BSPLINES``; auto-generated
        when ``None``.
    poly_degree:
        Polynomial degree for ``ZeroInterp.SIMPLE_POLYNOMIAL`` (default 3).
    """
    calendar = issuer.calendar()
    ql.Settings.instance().evaluationDate = _to_ql_date(valuation_date)

    settlement = calendar.advance(
        _to_ql_date(valuation_date),
        issuer.settlement_days,
        ql.Days,
        ql.ModifiedFollowing,
    )

    curve_handle, _ = ql_build_zero_curve(
        issuer,
        valuation_date,
        rates_df,
        rate_type=rate_type,
        interpolation=interpolation,
        bspline_knots=bspline_knots,
        poly_degree=poly_degree,
    )
    engine = ql.DiscountingBondEngine(curve_handle)

    rows: list[dict] = []
    for record in rates_df.iter_rows(named=True):
        yrs = record["tenor_years"]
        if yrs < 1.0:
            days = int(round(yrs * 365))
            maturity = calendar.advance(settlement, days, ql.Days, ql.ModifiedFollowing)
        else:
            maturity = calendar.advance(
                settlement,
                ql.Period(int(round(yrs * 12)), ql.Months),
                ql.ModifiedFollowing,
            )
        bond = ql.ZeroCouponBond(
            issuer.settlement_days,
            calendar,
            100.0,
            maturity,
            ql.ModifiedFollowing,
            100.0,
            settlement,
        )
        bond.setPricingEngine(engine)

        clean = bond.cleanPrice()
        yld = bond.bondYield(
            issuer.day_count,
            ql.Compounded,
            issuer.frequency,
        )
        curve_zero = (
            curve_handle.zeroRate(
                yrs,
                ql.Compounded,
                issuer.frequency,
            ).rate()
            * 100.0
        )

        rows.append(
            {
                "issuer": issuer.source_code,
                "valuation_date": valuation_date.isoformat(),
                "rate_type": rate_type.value,
                "tenor_label": record["tenor_label"],
                "tenor_years": yrs,
                "input_rate_pct": record["rate_pct"],
                "curve_zero_pct": curve_zero,
                "clean_price": clean,
                "yield_pct": yld * 100.0,
            }
        )

    return pl.DataFrame(rows).sort("tenor_years")


def ql_price_cmts(
    db_path: str,
    source: str,
    valuation_date: str | date,
    rate_type: RateType | str = RateType.ZERO,
    interpolation: QLZeroInterp | str | None = None,
    *,
    bspline_knots: list[float] | None = None,
    poly_degree: int = 3,
) -> pl.DataFrame:
    """End-to-end CMT pricing: load rates from input_data.db and price.

    Parameters
    ----------
    interpolation:
        Curve method.  Accepts a :class:`ZeroInterp` member or its string value
        (e.g. ``"cubic_bsplines"``).  ``None`` uses the rate-type default.
    bspline_knots:
        Custom knot vector for ``ZeroInterp.CUBIC_BSPLINES``; auto-generated
        when ``None``.
    poly_degree:
        Polynomial degree for ``ZeroInterp.SIMPLE_POLYNOMIAL`` (default 3).
    """
    issuer = resolve_issuer(source)
    if isinstance(rate_type, str):
        rate_type = RateType(rate_type.lower())
    if isinstance(valuation_date, str):
        valuation_date = date.fromisoformat(valuation_date)
    if isinstance(interpolation, str):
        interpolation = QLZeroInterp(interpolation)

    rates_df = load_curve_rates(
        db_path,
        issuer,
        valuation_date,
        rate_type=rate_type,
    )
    return ql_price_cmts_from_rates(
        issuer,
        valuation_date,
        rates_df,
        rate_type=rate_type,
        interpolation=interpolation,
        bspline_knots=bspline_knots,
        poly_degree=poly_degree,
    )
