"""QuantLib yield-curve construction from pillar rates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

import QuantLib as ql
import polars as pl

from cheapquant_fi.issuers import IssuerProfile, RateType


class QLZeroInterp(str, Enum):
    """Interpolation/bootstrap/fitting method for yield-curve construction.

    Members supported by **ZERO** input rates (``InterpolatedZeroCurve``):

    =========================================  =====================================
    Enum member                                QuantLib class
    =========================================  =====================================
    ``LINEAR_ZERO``                            ``ZeroCurve`` (linear on zero rates)
    ``CUBIC_ZERO`` *(ZERO default)*            ``CubicZeroCurve``
    ``NATURAL_CUBIC_ZERO``                     ``NaturalCubicZeroCurve``
    ``MONOTONE_CUBIC_ZERO``                    ``MonotonicCubicZeroCurve``
    =========================================  =====================================

    Members supported by **PAR** input rates (``PiecewiseYieldCurve``):

    =========================================  =====================================
    Enum member                                QuantLib class
    =========================================  =====================================
    ``LINEAR_ZERO`` *(PAR default)*            ``PiecewiseLinearZero``
    ``CUBIC_ZERO``                             ``PiecewiseCubicZero``
    ``NATURAL_CUBIC_ZERO``                     ``PiecewiseNaturalCubicZero``
    ``KRUGER_ZERO``                            ``PiecewiseKrugerZero``
    ``CONVEX_MONOTONE_ZERO``                   ``PiecewiseConvexMonotoneZero``
    ``LOG_LINEAR_DISCOUNT``                    ``PiecewiseLogLinearDiscount``
    ``LOG_CUBIC_DISCOUNT``                     ``PiecewiseLogCubicDiscount``
    ``NATURAL_LOG_CUBIC_DISCOUNT``             ``PiecewiseNaturalLogCubicDiscount``
    ``KRUGER_LOG_DISCOUNT``                    ``PiecewiseKrugerLogDiscount``
    ``SPLINE_CUBIC_DISCOUNT``                  ``PiecewiseSplineCubicDiscount``
    ``LINEAR_FORWARD``                         ``PiecewiseLinearForward``
    ``FLAT_FORWARD``                           ``PiecewiseFlatForward``
    =========================================  =====================================

    Members supported by **PAR** input rates (``FittedBondDiscountCurve``):

    These use a parametric *fitting* approach: the curve is not forced to reprice
    every pillar instrument exactly; instead it finds the smooth functional form
    that minimises aggregate pricing error.  They are all PAR-only because they
    require bond prices to calibrate against.

    =========================================  =====================================
    Enum member                                QuantLib fitting method
    =========================================  =====================================
    ``NELSON_SIEGEL``                          ``NelsonSiegelFitting``
    ``SVENSSON``                               ``SvenssonFitting``
    ``EXPONENTIAL_SPLINES``                    ``ExponentialSplinesFitting``
    ``SIMPLE_POLYNOMIAL``                      ``SimplePolynomialFitting`` (deg 3)
    ``CUBIC_BSPLINES``                         ``CubicBSplinesFitting``
    =========================================  =====================================

    ``SIMPLE_POLYNOMIAL`` degree is controlled by the *poly_degree* argument of
    :func:`build_zero_curve` (default 3).  ``CUBIC_BSPLINES`` knots are
    auto-generated from the pillar tenors unless *bspline_knots* is supplied.

    ``LINEAR_ZERO``, ``CUBIC_ZERO``, and ``NATURAL_CUBIC_ZERO`` work with both
    rate types.  All other members are restricted to one rate type; passing an
    incompatible combination raises ``ValueError``.

    Pass ``interpolation=None`` (the default) to get the type-appropriate
    default: ``CUBIC_ZERO`` for zero-rate inputs and ``LINEAR_ZERO`` for par
    inputs (preserving prior behaviour).
    """

    # works for ZERO and PAR
    LINEAR_ZERO = "linear_zero"
    CUBIC_ZERO = "cubic_zero"
    NATURAL_CUBIC_ZERO = "natural_cubic_zero"
    # ZERO only
    MONOTONE_CUBIC_ZERO = "monotone_cubic_zero"
    # PAR only — zero-rate basis (piecewise bootstrap)
    KRUGER_ZERO = "kruger_zero"
    CONVEX_MONOTONE_ZERO = "convex_monotone_zero"
    # PAR only — discount-factor basis (piecewise bootstrap)
    LOG_LINEAR_DISCOUNT = "log_linear_discount"
    LOG_CUBIC_DISCOUNT = "log_cubic_discount"
    NATURAL_LOG_CUBIC_DISCOUNT = "natural_log_cubic_discount"
    KRUGER_LOG_DISCOUNT = "kruger_log_discount"
    SPLINE_CUBIC_DISCOUNT = "spline_cubic_discount"
    # PAR only — instantaneous-forward basis (piecewise bootstrap)
    LINEAR_FORWARD = "linear_forward"
    FLAT_FORWARD = "flat_forward"
    # PAR only — parametric fitted curves (FittedBondDiscountCurve)
    NELSON_SIEGEL = "nelson_siegel"
    SVENSSON = "svensson"
    EXPONENTIAL_SPLINES = "exponential_splines"
    SIMPLE_POLYNOMIAL = "simple_polynomial"
    CUBIC_BSPLINES = "cubic_bsplines"


# Maps for direct zero curves (RateType.ZERO).
_QL_ZERO_CURVE_CLS: dict[QLZeroInterp, type] = {
    QLZeroInterp.LINEAR_ZERO: ql.ZeroCurve,
    QLZeroInterp.CUBIC_ZERO: ql.CubicZeroCurve,
    QLZeroInterp.NATURAL_CUBIC_ZERO: ql.NaturalCubicZeroCurve,
    QLZeroInterp.MONOTONE_CUBIC_ZERO: ql.MonotonicCubicZeroCurve,
}

# Maps for piecewise bootstrap curves (RateType.PAR).
_QL_PAR_CURVE_CLS: dict[QLZeroInterp, type] = {
    QLZeroInterp.LINEAR_ZERO: ql.PiecewiseLinearZero,
    QLZeroInterp.CUBIC_ZERO: ql.PiecewiseCubicZero,
    QLZeroInterp.NATURAL_CUBIC_ZERO: ql.PiecewiseNaturalCubicZero,
    QLZeroInterp.KRUGER_ZERO: ql.PiecewiseKrugerZero,
    QLZeroInterp.CONVEX_MONOTONE_ZERO: ql.PiecewiseConvexMonotoneZero,
    QLZeroInterp.LOG_LINEAR_DISCOUNT: ql.PiecewiseLogLinearDiscount,
    QLZeroInterp.LOG_CUBIC_DISCOUNT: ql.PiecewiseLogCubicDiscount,
    QLZeroInterp.NATURAL_LOG_CUBIC_DISCOUNT: ql.PiecewiseNaturalLogCubicDiscount,
    QLZeroInterp.KRUGER_LOG_DISCOUNT: ql.PiecewiseKrugerLogDiscount,
    QLZeroInterp.SPLINE_CUBIC_DISCOUNT: ql.PiecewiseSplineCubicDiscount,
    QLZeroInterp.LINEAR_FORWARD: ql.PiecewiseLinearForward,
    QLZeroInterp.FLAT_FORWARD: ql.PiecewiseFlatForward,
}

# Fitted-curve members — handled via FittedBondDiscountCurve, not piecewise.
_QL_FITTED_MEMBERS: frozenset[QLZeroInterp] = frozenset({
    QLZeroInterp.NELSON_SIEGEL,
    QLZeroInterp.SVENSSON,
    QLZeroInterp.EXPONENTIAL_SPLINES,
    QLZeroInterp.SIMPLE_POLYNOMIAL,
    QLZeroInterp.CUBIC_BSPLINES,
})

_QL_ZERO_DEFAULT = QLZeroInterp.CUBIC_ZERO
_QL_PAR_DEFAULT = QLZeroInterp.LINEAR_ZERO


@dataclass(frozen=True)
class ZeroCurveBuildOptions:
    """Keyword arguments shared by :func:`ql_build_zero_curve` call sites."""

    rate_type: RateType = RateType.ZERO
    interpolation: QLZeroInterp | None = None
    bspline_knots: list[float] | None = None
    poly_degree: int = 3

    def build(
        self,
        issuer: IssuerProfile,
        valuation_date: date,
        rates_df: pl.DataFrame,
    ) -> tuple[ql.YieldTermStructureHandle, pl.DataFrame]:
        """Build a curve using these options."""
        return ql_build_zero_curve(
            issuer,
            valuation_date,
            rates_df,
            rate_type=self.rate_type,
            interpolation=self.interpolation,
            bspline_knots=self.bspline_knots,
            poly_degree=self.poly_degree,
        )


def _ql_auto_bspline_knots(tenor_years: list[float]) -> list[float]:
    """Generate a sensible cubic B-spline knot vector from pillar tenors.

    The standard convention (QuantLib's own example) uses negative pre-knots so
    that the basis functions are nonzero at ``t = 0`` (required when
    ``constrainAtZero=True``).  Two negative knots are prepended at fractions of
    the max maturity, and two post-knots are appended beyond it.
    """
    t_max = max(tenor_years)
    pre = [-t_max * 2 / 3, -t_max / 3]
    interior = [0.0] + sorted(tenor_years)
    post = [t_max * 4 / 3, t_max * 5 / 3]
    return pre + interior + post


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _ql_pillar_dates(
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


def ql_build_zero_curve(
    issuer: IssuerProfile,
    valuation_date: date,
    rates_df: pl.DataFrame,
    rate_type: RateType = RateType.ZERO,
    interpolation: QLZeroInterp | None = None,
    *,
    bspline_knots: list[float] | None = None,
    poly_degree: int = 3,
) -> tuple[ql.YieldTermStructureHandle, pl.DataFrame]:
    """Build a yield curve from pillar rates with a selectable interpolation method.

    Parameters
    ----------
    issuer:
        Issuer conventions (calendar, day count, settlement days, …).
    valuation_date:
        Pricing date; QuantLib's global evaluation date is set to this value.
    rates_df:
        DataFrame with ``tenor_years`` and ``rate_pct`` columns.
    rate_type:
        Whether ``rates_df`` contains zero rates or par rates.
    interpolation:
        Curve construction method.  ``None`` (default) picks the type-appropriate
        default: ``CUBIC_ZERO`` for zero inputs, ``LINEAR_ZERO`` for par inputs.
        See :class:`ZeroInterp` for the full catalogue.
    bspline_knots:
        Explicit knot vector for ``CUBIC_BSPLINES``.  When ``None`` the knots are
        auto-generated from the pillar tenors using :func:`_auto_bspline_knots`.
        Ignored for all other methods.
    poly_degree:
        Polynomial degree for ``SIMPLE_POLYNOMIAL`` (default 3).  Ignored for
        all other methods.

    Returns
    -------
    tuple[YieldTermStructureHandle, pl.DataFrame]
        The curve handle and a diagnostics DataFrame of pillar zero rates.

    Raises
    ------
    ValueError
        When *interpolation* is not supported for the given *rate_type*.
    """
    if interpolation is None:
        interp = _QL_ZERO_DEFAULT if rate_type == RateType.ZERO else _QL_PAR_DEFAULT
    else:
        interp = QLZeroInterp(interpolation)

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
    pillar_dates = _ql_pillar_dates(settlement, calendar, tenor_years)
    # QuantLib interpolated zero curves use dates[0] as the reference date.
    dates = [settlement] + pillar_dates
    curve_rates = [rates[0]] + rates

    if rate_type == RateType.ZERO:
        cls = _QL_ZERO_CURVE_CLS.get(interp)
        if cls is None:
            valid = ", ".join(k.value for k in _QL_ZERO_CURVE_CLS)
            raise ValueError(
                f"ZeroInterp.{interp.name} is not supported for ZERO rate inputs. "
                f"Valid choices: {valid}"
            )
        curve = cls(dates, curve_rates, issuer.day_count, calendar)
    else:
        # Build bond helpers — shared by both piecewise and fitted branches.
        helpers: list[ql.RateHelper] = []
        ex_args = (
            issuer.ex_dividend.ex_coupon_args()
            if issuer.ex_dividend is not None
            else (ql.Period(), ql.NullCalendar(), ql.Unadjusted, False)
        )
        for yrs, par_rate, maturity in zip(tenor_years, rates, pillar_dates, strict=True):
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
                    ql.NullCalendar(),
                    *ex_args,
                )
            )

        if interp in _QL_FITTED_MEMBERS:
            # --- FittedBondDiscountCurve branch ---
            if interp == QLZeroInterp.NELSON_SIEGEL:
                fitting_method = ql.NelsonSiegelFitting()
            elif interp == QLZeroInterp.SVENSSON:
                fitting_method = ql.SvenssonFitting()
            elif interp == QLZeroInterp.EXPONENTIAL_SPLINES:
                fitting_method = ql.ExponentialSplinesFitting(True)
            elif interp == QLZeroInterp.SIMPLE_POLYNOMIAL:
                fitting_method = ql.SimplePolynomialFitting(poly_degree)
            elif interp == QLZeroInterp.CUBIC_BSPLINES:
                knots = bspline_knots if bspline_knots is not None else _ql_auto_bspline_knots(tenor_years)
                fitting_method = ql.CubicBSplinesFitting(knots, True)
            else:  # pragma: no cover
                raise AssertionError(f"Unhandled fitted method: {interp}")
            curve = ql.FittedBondDiscountCurve(settlement, helpers, issuer.day_count, fitting_method)
        else:
            # --- PiecewiseYieldCurve branch ---
            cls = _QL_PAR_CURVE_CLS.get(interp)
            if cls is None:
                valid = ", ".join(k.value for k in _QL_PAR_CURVE_CLS)
                raise ValueError(
                    f"ZeroInterp.{interp.name} is not supported for PAR rate inputs. "
                    f"Valid choices: {valid}"
                )
            curve = cls(settlement, helpers, issuer.day_count)

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
