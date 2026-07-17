"""Tests for MarketContext, CurveCollection, and FXC."""

from __future__ import annotations

from datetime import date

import QuantLib as ql
import polars as pl

from cheapquant_fi.issuers import resolve_issuer
from cheapquant_fi.quantlib.quantlib_curve import ql_build_zero_curve
from cheapquant_fi.quantlib.quantlib_market_context import CurveCollection, FXC, MarketContext


def test_market_context_usa_curve_and_fx():
    as_of = date(2020, 1, 2)
    issuer = resolve_issuer("USA")

    rates_df = pl.DataFrame(
        {
            "tenor_column": ["Y001p0", "Y005p0", "Y010p0"],
            "tenor_label": ["1Y", "5Y", "10Y"],
            "tenor_years": [1.0, 5.0, 10.0],
            "rate_pct": [1.5, 2.0, 2.5],
        }
    )
    curve_handle, _ = ql_build_zero_curve(issuer, as_of, rates_df)

    curves = CurveCollection(as_of=as_of)
    curves.set_bond_curve("USA", curve_handle)

    fx = FXC(as_of=as_of)
    fx.set_rate("AUD", "USD", 1.45)

    mktctx = MarketContext()
    mktctx.add_curve_collection(curves)
    mktctx.add_fxc(fx)

    usa_curve = mktctx.curve_collection().bond_curve("USA")
    assert isinstance(usa_curve, ql.YieldTermStructureHandle)
    assert mktctx.curve_collection().bond_issuers() == ["USA"]
    assert mktctx.fxc().rate("AUD", "USD") == 1.45
