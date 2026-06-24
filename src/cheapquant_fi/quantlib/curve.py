"""QuantLib yield-curve construction from pillar rates."""

from __future__ import annotations

from datetime import date

import QuantLib as ql
import polars as pl

from cheapquant_fi.issuers import IssuerProfile, RateType


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _pillar_dates(
    settlement: ql.Date,
    calendar: ql.Calendar,
    tenor_years: list[float],
) -> list[ql.Date]:
    dates: list[ql.Date] = []
    for years in tenor_years:
        if years < 1.0:
            days = int(round(years * 365))
            maturity = calendar.advance(settlement, days, ql.Days, ql.ModifiedFollowing)
        else:
            maturity = calendar.advance(
                settlement,
                ql.Period(int(round(years * 12)), ql.Months),
                ql.ModifiedFollowing,
            )
        dates.append(maturity)
    return dates


def build_zero_curve(
    issuer: IssuerProfile,
    valuation_date: date,
    rates_df: pl.DataFrame,
    rate_type: RateType = RateType.ZERO,
) -> tuple[ql.YieldTermStructureHandle, pl.DataFrame]:
    """Bootstrap an interpolated zero curve from pillar rates.

    ``rates_df`` must contain ``tenor_years`` and ``rate_pct`` columns.
    Returns the curve handle and a diagnostics DataFrame of pillar zero rates.
    """
    calendar = issuer.calendar()
    ql.Settings.instance().evaluationDate = _to_ql_date(valuation_date)

    settlement = calendar.advance(
        _to_ql_date(valuation_date),
        issuer.settlement_days,
        ql.Days,
        ql.ModifiedFollowing,
    )

    tenor_years = rates_df["tenor_years"].to_list()
    rates = [r / 100.0 for r in rates_df["rate_pct"].to_list()]
    pillar_dates = _pillar_dates(settlement, calendar, tenor_years)
    # QuantLib interpolated zero curves use dates[0] as the reference date.
    dates = [settlement] + pillar_dates
    curve_rates = [rates[0]] + rates

    if rate_type == RateType.ZERO:
        curve = ql.CubicZeroCurve(
            dates,
            curve_rates,
            issuer.day_count,
            calendar,
        )
    else:
        helpers: list[ql.RateHelper] = []
        for yrs, par_rate, maturity in zip(tenor_years, rates, dates, strict=True):
            schedule = ql.Schedule(
                settlement,
                maturity,
                ql.Period(issuer.frequency),
                calendar,
                ql.ModifiedFollowing,
                ql.ModifiedFollowing,
                ql.DateGeneration.Backward,
                False,
            )
            helpers.append(
                ql.FixedRateBondHelper(
                    ql.QuoteHandle(ql.SimpleQuote(100.0)),
                    issuer.settlement_days,
                    100.0,
                    schedule,
                    [par_rate],
                    issuer.day_count,
                    ql.ModifiedFollowing,
                    100.0,
                    settlement,
                )
            )
        curve = ql.PiecewiseLinearZero(
            settlement,
            helpers,
            issuer.day_count,
        )

    curve_handle = ql.YieldTermStructureHandle(curve)
    curve.enableExtrapolation()

    diagnostics: list[dict] = []
    for yrs, input_rate in zip(tenor_years, rates_df["rate_pct"].to_list(), strict=True):
        zero = curve.zeroRate(
            yrs,
            ql.Compounded,
            issuer.frequency,
        ).rate()
        diagnostics.append(
            {
                "tenor_years": yrs,
                "input_rate_pct": input_rate,
                "curve_zero_pct": zero * 100.0,
            }
        )

    return curve_handle, pl.DataFrame(diagnostics)
