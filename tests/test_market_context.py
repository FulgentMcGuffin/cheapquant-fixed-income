"""Tests for MarketContext, CurveCollection, and FXC."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import QuantLib as ql
import polars as pl
import pytest

from cheapquant_fi.issuers import RateType, resolve_issuer
from cheapquant_fi.quantlib.quantlib_curve import ql_build_zero_curve
from cheapquant_fi.quantlib.quantlib_market_context import (
    FXC,
    QuantLibCurveCollection,
    QuantlibMarketContext,
    ql_build_curve_collections,
    ql_build_market_context,
)


def _sample_rates_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "tenor_column": ["Y001p0", "Y005p0", "Y010p0"],
            "tenor_label": ["1Y", "5Y", "10Y"],
            "tenor_years": [1.0, 5.0, 10.0],
            "rate_pct": [1.5, 2.0, 2.5],
        }
    )


def test_market_context_usa_curve_and_fx():
    as_of = date(2020, 1, 2)
    issuer = resolve_issuer("USA")

    rates_df = _sample_rates_df()
    curve_handle, _ = ql_build_zero_curve(issuer, as_of, rates_df)

    curves = QuantLibCurveCollection(as_of=as_of)
    curves.set_bond_curve("USA", curve_handle)

    fx = FXC(as_of=as_of)
    fx.set_rate("AUD", "USD", 1.45)

    mktctx = QuantlibMarketContext()
    mktctx.add_curve_collection(curves)
    mktctx.add_fxc(fx)

    usa_curve = mktctx.curve_collection().bond_curve("USA")
    assert isinstance(usa_curve, ql.YieldTermStructureHandle)
    assert mktctx.curve_collection().bond_issuers() == ["USA"]
    assert mktctx.fxc().rate("AUD", "USD") == 1.45


def test_ql_build_curve_collections_groups_by_date_and_orders():
    usa = resolve_issuer("USA")
    deu = resolve_issuer("DEU")
    d1 = date(2020, 1, 2)
    d2 = date(2020, 1, 3)
    rates = _sample_rates_df()

    pairs = [
        (d2, deu),
        (d1, usa),
        (d1, deu),
        (d2, usa),
    ]

    def fake_load_curve_rates(_db_path, _issuer, _valuation_date, **_kwargs):
        return rates

    with patch(
        "cheapquant_fi.quantlib.quantlib_market_context.load_curve_rates",
        side_effect=fake_load_curve_rates,
    ):
        collections = ql_build_curve_collections(pairs, "/fake/input_data.db")

    assert len(collections) == 2
    assert [c.as_of for c in collections] == [d1, d2]
    assert collections[0].bond_issuers() == ["DEU", "USA"]
    assert collections[1].bond_issuers() == ["DEU", "USA"]
    assert isinstance(collections[0].bond_curve("USA"), ql.YieldTermStructureHandle)


def test_ql_build_curve_collections_rejects_duplicate_pairs():
    usa = resolve_issuer("USA")
    d1 = date(2020, 1, 2)
    pairs = [(d1, usa), (d1, usa)]

    with pytest.raises(ValueError, match="Duplicate"):
        ql_build_curve_collections(pairs, "/fake/input_data.db")


def test_ql_build_market_context_registers_bond_zero_label():
    trade_date = date(2020, 1, 2)
    rates = _sample_rates_df()

    def fake_load_curve_rates(_db_path, _issuer, _valuation_date, **_kwargs):
        return rates

    with patch(
        "cheapquant_fi.quantlib.quantlib_market_context.load_curve_rates",
        side_effect=fake_load_curve_rates,
    ):
        context = ql_build_market_context(
            trade_date,
            ["USA", "DEU"],
            "/fake/ycs_data.duckdb",
        )

    assert context.curve_collection_labels() == ["BOND_ZERO"]
    collection = context.curve_collection("BOND_ZERO")
    assert collection.as_of == trade_date
    assert collection.bond_issuers() == ["DEU", "USA"]
    assert isinstance(collection.bond_curve("USA"), ql.YieldTermStructureHandle)


def test_ql_build_market_context_registers_bond_par_label():
    trade_date = date(2020, 1, 2)
    rates = _sample_rates_df()

    with patch(
        "cheapquant_fi.quantlib.quantlib_market_context.load_curve_rates",
        return_value=rates,
    ):
        context = ql_build_market_context(
            trade_date,
            ["USA"],
            rate_type=RateType.PAR,
        )

    assert context.curve_collection("BOND_PAR").bond_issuers() == ["USA"]


def test_ql_build_market_context_requires_issuers():
    with pytest.raises(ValueError, match="At least one issuer"):
        ql_build_market_context(date(2020, 1, 2), [])
