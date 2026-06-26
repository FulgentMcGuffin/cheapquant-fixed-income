"""Tests for build_zero_curve covering every ZeroInterp member.

All tests are self-contained (no external DB required) — they use a small
synthetic rates DataFrame and the DEU issuer profile.
"""

from __future__ import annotations

from datetime import date

import QuantLib as ql
import polars as pl
import pytest

from cheapquant_fi.issuers import ISSUERS, RateType
from cheapquant_fi.quantlib.curve import ZeroInterp, _auto_bspline_knots, build_zero_curve

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ISSUER = ISSUERS["DEU"]  # TARGET / Act/Act ISDA / Annual / T+2
_VAL_DATE = date(2024, 1, 15)

# Synthetic zero-rate pillars (enough for all InterpolatedZeroCurve methods)
_ZERO_DF = pl.DataFrame(
    {
        "tenor_label": ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"],
        "tenor_years": [1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
        "rate_pct": [3.40, 3.55, 3.70, 3.85, 3.80, 3.65],
    }
)

# Synthetic par-rate pillars — more pillars needed by fitted methods
# (CUBIC_BSPLINES auto-generates ~len(tenors)+5 knots, all must be <= n_helpers)
_PAR_DF = pl.DataFrame(
    {
        "tenor_label": ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"],
        "tenor_years": [1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0],
        "rate_pct": [3.40, 3.50, 3.58, 3.68, 3.76, 3.82, 3.80, 3.76, 3.62],
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TENOR_YEARS = _PAR_DF["tenor_years"].to_list()


def _assert_valid_result(
    handle: ql.YieldTermStructureHandle,
    diag: pl.DataFrame,
    tenor_years: list[float],
) -> None:
    """Common sanity checks applied to every build_zero_curve result."""
    assert isinstance(handle, ql.YieldTermStructureHandle)
    assert set(diag.columns) >= {"tenor_years", "input_rate_pct", "curve_zero_pct"}
    assert len(diag) == len(tenor_years)

    for yrs in tenor_years:
        zero = handle.zeroRate(yrs, ql.Compounded, _ISSUER.frequency).rate()
        assert 0.0 < zero < 0.20, f"Implausible zero rate {zero:.5f} at {yrs}y"


# ---------------------------------------------------------------------------
# ZERO rate-type: InterpolatedZeroCurve methods
# ---------------------------------------------------------------------------

_ZERO_METHODS = [
    ZeroInterp.LINEAR_ZERO,
    ZeroInterp.CUBIC_ZERO,
    ZeroInterp.NATURAL_CUBIC_ZERO,
    ZeroInterp.MONOTONE_CUBIC_ZERO,
]


@pytest.mark.parametrize("method", _ZERO_METHODS, ids=lambda m: m.value)
def test_zero_interp_methods(method: ZeroInterp) -> None:
    """All InterpolatedZeroCurve variants build a valid curve from zero rates."""
    handle, diag = build_zero_curve(
        _ISSUER, _VAL_DATE, _ZERO_DF, RateType.ZERO, method
    )
    _assert_valid_result(handle, diag, _ZERO_DF["tenor_years"].to_list())


def test_zero_default_is_cubic_zero() -> None:
    """interpolation=None for ZERO inputs selects CubicZeroCurve."""
    handle_default, _ = build_zero_curve(_ISSUER, _VAL_DATE, _ZERO_DF, RateType.ZERO)
    handle_explicit, _ = build_zero_curve(
        _ISSUER, _VAL_DATE, _ZERO_DF, RateType.ZERO, ZeroInterp.CUBIC_ZERO
    )
    z_default = handle_default.zeroRate(5.0, ql.Compounded, _ISSUER.frequency).rate()
    z_explicit = handle_explicit.zeroRate(5.0, ql.Compounded, _ISSUER.frequency).rate()
    assert abs(z_default - z_explicit) < 1e-12


# ---------------------------------------------------------------------------
# PAR rate-type: PiecewiseYieldCurve methods (bootstrap)
# ---------------------------------------------------------------------------

_PAR_PIECEWISE_METHODS = [
    ZeroInterp.LINEAR_ZERO,
    ZeroInterp.CUBIC_ZERO,
    ZeroInterp.NATURAL_CUBIC_ZERO,
    ZeroInterp.KRUGER_ZERO,
    ZeroInterp.CONVEX_MONOTONE_ZERO,
    ZeroInterp.LOG_LINEAR_DISCOUNT,
    ZeroInterp.LOG_CUBIC_DISCOUNT,
    ZeroInterp.NATURAL_LOG_CUBIC_DISCOUNT,
    ZeroInterp.KRUGER_LOG_DISCOUNT,
    ZeroInterp.SPLINE_CUBIC_DISCOUNT,
    ZeroInterp.LINEAR_FORWARD,
    ZeroInterp.FLAT_FORWARD,
]


@pytest.mark.parametrize("method", _PAR_PIECEWISE_METHODS, ids=lambda m: m.value)
def test_par_piecewise_methods(method: ZeroInterp) -> None:
    """All PiecewiseYieldCurve variants bootstrap a valid curve from par rates."""
    handle, diag = build_zero_curve(
        _ISSUER, _VAL_DATE, _PAR_DF, RateType.PAR, method
    )
    _assert_valid_result(handle, diag, _PAR_DF["tenor_years"].to_list())


def test_par_default_is_linear_zero() -> None:
    """interpolation=None for PAR inputs selects PiecewiseLinearZero."""
    handle_default, _ = build_zero_curve(_ISSUER, _VAL_DATE, _PAR_DF, RateType.PAR)
    handle_explicit, _ = build_zero_curve(
        _ISSUER, _VAL_DATE, _PAR_DF, RateType.PAR, ZeroInterp.LINEAR_ZERO
    )
    z_default = handle_default.zeroRate(10.0, ql.Compounded, _ISSUER.frequency).rate()
    z_explicit = handle_explicit.zeroRate(10.0, ql.Compounded, _ISSUER.frequency).rate()
    assert abs(z_default - z_explicit) < 1e-12


# ---------------------------------------------------------------------------
# PAR rate-type: FittedBondDiscountCurve methods
# ---------------------------------------------------------------------------

_PAR_FITTED_METHODS = [
    ZeroInterp.NELSON_SIEGEL,
    ZeroInterp.SVENSSON,
    ZeroInterp.EXPONENTIAL_SPLINES,
    ZeroInterp.SIMPLE_POLYNOMIAL,
    ZeroInterp.CUBIC_BSPLINES,
]


@pytest.mark.parametrize("method", _PAR_FITTED_METHODS, ids=lambda m: m.value)
def test_par_fitted_methods(method: ZeroInterp) -> None:
    """All FittedBondDiscountCurve variants produce a valid curve from par rates."""
    handle, diag = build_zero_curve(
        _ISSUER, _VAL_DATE, _PAR_DF, RateType.PAR, method
    )
    _assert_valid_result(handle, diag, _PAR_DF["tenor_years"].to_list())


def test_cubic_bsplines_custom_knots() -> None:
    """CUBIC_BSPLINES accepts an explicit knot vector."""
    tenor_years = _PAR_DF["tenor_years"].to_list()
    t_max = max(tenor_years)
    # Slightly wider knot vector than the auto-generated one
    custom_knots = (
        [-t_max, -t_max / 2, 0.0]
        + tenor_years
        + [t_max * 1.5, t_max * 2.0]
    )
    handle, diag = build_zero_curve(
        _ISSUER,
        _VAL_DATE,
        _PAR_DF,
        RateType.PAR,
        ZeroInterp.CUBIC_BSPLINES,
        bspline_knots=custom_knots,
    )
    _assert_valid_result(handle, diag, tenor_years)


def test_simple_polynomial_custom_degree() -> None:
    """SIMPLE_POLYNOMIAL respects the poly_degree kwarg."""
    for degree in (2, 4, 5):
        handle, diag = build_zero_curve(
            _ISSUER,
            _VAL_DATE,
            _PAR_DF,
            RateType.PAR,
            ZeroInterp.SIMPLE_POLYNOMIAL,
            poly_degree=degree,
        )
        _assert_valid_result(handle, diag, _PAR_DF["tenor_years"].to_list())


# ---------------------------------------------------------------------------
# String-value coercion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method_str", [m.value for m in _ZERO_METHODS])
def test_zero_interp_accepts_string_value(method_str: str) -> None:
    """ZeroInterp members can be passed as their string value."""
    handle, diag = build_zero_curve(
        _ISSUER, _VAL_DATE, _ZERO_DF, RateType.ZERO, ZeroInterp(method_str)
    )
    assert isinstance(handle, ql.YieldTermStructureHandle)


# ---------------------------------------------------------------------------
# Cross-type incompatibility — ValueError expected
# ---------------------------------------------------------------------------

_ZERO_ONLY_METHODS = [ZeroInterp.MONOTONE_CUBIC_ZERO]

_PAR_ONLY_METHODS = [
    ZeroInterp.KRUGER_ZERO,
    ZeroInterp.CONVEX_MONOTONE_ZERO,
    ZeroInterp.LOG_LINEAR_DISCOUNT,
    ZeroInterp.LOG_CUBIC_DISCOUNT,
    ZeroInterp.NATURAL_LOG_CUBIC_DISCOUNT,
    ZeroInterp.KRUGER_LOG_DISCOUNT,
    ZeroInterp.SPLINE_CUBIC_DISCOUNT,
    ZeroInterp.LINEAR_FORWARD,
    ZeroInterp.FLAT_FORWARD,
    ZeroInterp.NELSON_SIEGEL,
    ZeroInterp.SVENSSON,
    ZeroInterp.EXPONENTIAL_SPLINES,
    ZeroInterp.SIMPLE_POLYNOMIAL,
    ZeroInterp.CUBIC_BSPLINES,
]


@pytest.mark.parametrize("method", _ZERO_ONLY_METHODS, ids=lambda m: m.value)
def test_zero_only_method_raises_for_par(method: ZeroInterp) -> None:
    """Methods only valid for ZERO inputs raise ValueError when given PAR rates."""
    with pytest.raises(ValueError, match="not supported for PAR"):
        build_zero_curve(_ISSUER, _VAL_DATE, _PAR_DF, RateType.PAR, method)


@pytest.mark.parametrize("method", _PAR_ONLY_METHODS, ids=lambda m: m.value)
def test_par_only_method_raises_for_zero(method: ZeroInterp) -> None:
    """Methods only valid for PAR inputs raise ValueError when given ZERO rates."""
    with pytest.raises(ValueError, match="not supported for ZERO"):
        build_zero_curve(_ISSUER, _VAL_DATE, _ZERO_DF, RateType.ZERO, method)


# ---------------------------------------------------------------------------
# Auto knot generation
# ---------------------------------------------------------------------------

def test_auto_bspline_knots_structure() -> None:
    """_auto_bspline_knots produces a properly structured knot vector."""
    tenors = [1.0, 2.0, 5.0, 10.0, 30.0]
    knots = _auto_bspline_knots(tenors)
    t_max = 30.0

    assert knots[0] < 0 and knots[1] < 0, "First two knots must be negative"
    assert 0.0 in knots, "Zero must be present"
    for t in tenors:
        assert t in knots, f"Pillar tenor {t} must appear in knots"
    assert knots[-2] > t_max and knots[-1] > t_max, "Last two knots must exceed max tenor"
    assert knots == sorted(knots), "Knot vector must be non-decreasing"
    assert len(knots) >= 8, "At least 8 knots required by QuantLib"
