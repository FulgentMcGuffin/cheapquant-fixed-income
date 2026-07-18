"""Tests for QuantlibMarketContextManager singleton registry."""

from __future__ import annotations

from datetime import date

import QuantLib as ql
import pytest

from cheapquant_fi.quantlib.quantlib_market_context import (
    FXC,
    QuantLibCurveCollection,
    QuantlibMarketContext,
)
from cheapquant_fi.quantlib.quantlib_market_context_manager import (
    QuantlibMarketContextManager,
)


@pytest.fixture(autouse=True)
def _clear_manager():
    QuantlibMarketContextManager.instance().clear()
    yield
    QuantlibMarketContextManager.instance().clear()


def _flat_curve_handle(rate: float) -> ql.YieldTermStructureHandle:
    curve = ql.FlatForward(
        0,
        ql.TARGET(),
        ql.QuoteHandle(ql.SimpleQuote(rate)),
        ql.Actual365Fixed(),
    )
    return ql.YieldTermStructureHandle(curve)


def test_manager_is_singleton():
    assert QuantlibMarketContextManager.instance() is QuantlibMarketContextManager()


def test_auto_register_after_set_curve_collection():
    as_of = date(2020, 1, 2)
    context = QuantlibMarketContext()
    curves = QuantLibCurveCollection(as_of=as_of)
    curves.set_bond_curve("USA", _flat_curve_handle(0.01))
    context.set_curve_collection(curves, label="BOND_ZERO")

    manager = QuantlibMarketContextManager.instance()
    stored = manager.get(as_of)

    assert stored is context
    assert stored.curve_collection("BOND_ZERO").bond_issuers() == ["USA"]


def test_mutations_visible_via_manager():
    as_of = date(2020, 1, 2)
    context = QuantlibMarketContext()
    curves = QuantLibCurveCollection(as_of=as_of)
    context.set_curve_collection(curves, label="BOND_ZERO")

    context.curve_collection("BOND_ZERO").set_bond_curve("DEU", _flat_curve_handle(0.02))

    stored = QuantlibMarketContextManager.instance().get(as_of)
    assert stored is context
    assert stored.curve_collection("BOND_ZERO").bond_issuers() == ["DEU"]


def test_merge_same_as_of_combines_labels():
    as_of = date(2020, 1, 2)

    first = QuantlibMarketContext()
    zero_curves = QuantLibCurveCollection(as_of=as_of)
    zero_curves.set_bond_curve("USA", _flat_curve_handle(0.01))
    first.set_curve_collection(zero_curves, label="BOND_ZERO")

    second = QuantlibMarketContext()
    par_curves = QuantLibCurveCollection(as_of=as_of)
    par_curves.set_bond_curve("DEU", _flat_curve_handle(0.02))
    second.set_curve_collection(par_curves, label="BOND_PAR")

    manager = QuantlibMarketContextManager.instance()
    canonical = manager.require(as_of)

    assert canonical is first
    assert canonical.curve_collection_labels() == ["BOND_PAR", "BOND_ZERO"]
    assert canonical.curve_collection("BOND_ZERO").bond_issuers() == ["USA"]
    assert canonical.curve_collection("BOND_PAR").bond_issuers() == ["DEU"]


def test_merge_same_label_uses_or_operator():
    as_of = date(2020, 1, 2)

    first = QuantlibMarketContext()
    left = QuantLibCurveCollection(as_of=as_of)
    left.set_bond_curve("USA", _flat_curve_handle(0.01))
    first.set_curve_collection(left, label="BOND_ZERO")

    second = QuantlibMarketContext()
    right = QuantLibCurveCollection(as_of=as_of)
    right.set_bond_curve("USA", _flat_curve_handle(0.03))
    right.set_bond_curve("DEU", _flat_curve_handle(0.02))
    second.set_curve_collection(right, label="BOND_ZERO")

    canonical = QuantlibMarketContextManager.instance().require(as_of)

    assert canonical is first
    usa_curve = canonical.curve_collection("BOND_ZERO").bond_curve("USA")
    assert usa_curve is right.bond_curve("USA")
    assert canonical.curve_collection("BOND_ZERO").bond_issuers() == ["DEU", "USA"]


def test_merge_fxc_same_label_uses_or_operator():
    as_of = date(2020, 1, 2)

    first = QuantlibMarketContext()
    left_fx = FXC(as_of=as_of)
    left_fx.set_rate("AUD", "USD", 1.45)
    first.set_fxc(left_fx, label="SPOT")

    second = QuantlibMarketContext()
    right_fx = FXC(as_of=as_of)
    right_fx.set_rate("AUD", "USD", 1.50)
    right_fx.set_rate("EUR", "USD", 0.92)
    second.set_fxc(right_fx, label="SPOT")

    canonical = QuantlibMarketContextManager.instance().require(as_of)

    assert canonical is first
    assert canonical.fxc("SPOT").rate("AUD", "USD") == 1.50
    assert canonical.fxc("SPOT").rate("EUR", "USD") == 0.92
