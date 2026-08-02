"""Tests for QuantLib bond future basis analytics."""

from __future__ import annotations

import json
from datetime import date

import QuantLib as ql
import pytest

from cqfi.bond_future_calculator import BondFutureCalculator
from cqfi.bond_future_input import BondFutureInput
from cqfi.bond_future_output import BOND_FUTURE_OUTPUT_COLUMNS
from cqfi.bond_futures import BOND_FUTURE_CONVENTIONS, BondFuture
from cqfi.date_utils import to_ql_date
from cqfi.delivery_basket import DeliveryBasket
from cqfi.instruments import Bond
from cqfi.numeric_term_structure import NumericTermStructure
from cqfi.quantlib.quantlib_bond_future_calculator import (
    BondFutureAnalyticsError,
    QuantLibBondFutureCalculator,
)
from cqfi.quantlib.quantlib_market_context import (
    QuantLibCurveCollection,
    QuantlibMarketContext,
)

TRADE_DATE = date(2026, 5, 15)
FLAT_RATE = 0.03

# FBTP September 2026 delivers 2026-09-10 and admits 8y6m..11y remaining.
FBTP_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2026)

# Deliverable maturities inside that window, with a range of coupons.
_DELIVERABLES = [
    (2.50, date(2035, 4, 1)),
    (4.00, date(2036, 2, 1)),
    (3.25, date(2037, 8, 1)),
]


@pytest.fixture
def basket() -> DeliveryBasket:
    basket = DeliveryBasket(bond_future=FBTP_U6)
    for coupon, maturity in _DELIVERABLES:
        basket.add(
            Bond(
                issuer="ITA",
                maturity=maturity,
                bond_id=f"IT{maturity:%Y%m}",
                user_friendly_id=f"ita{maturity:%Y%m}",
                coupon=coupon,
                issue_date=date(2015, 4, 1),
            )
        )
    return basket


@pytest.fixture
def market() -> QuantlibMarketContext:
    """A flat 3% curve, so every analytic has a closed-form expectation."""
    ql.Settings.instance().evaluationDate = to_ql_date(TRADE_DATE)
    curve = ql.YieldTermStructureHandle(
        ql.FlatForward(
            to_ql_date(TRADE_DATE),
            FLAT_RATE,
            ql.ActualActual(ql.ActualActual.ISDA),
            ql.Compounded,
            ql.Annual,
        )
    )
    collection = QuantLibCurveCollection(TRADE_DATE)
    collection.set_bond_curve("ITA", curve)
    context = QuantlibMarketContext()
    context.set_curve_collection(collection, label="BOND_ZERO")
    return context


@pytest.fixture
def flat_repo() -> NumericTermStructure:
    return NumericTermStructure(
        {"1m": 3.0, "3m": 3.0, "6m": 3.0, "1y": 3.0}, TRADE_DATE
    )


def _analyse(basket, market, **kwargs):
    request = BondFutureInput(
        delivery_basket=basket,
        bond_future=FBTP_U6,
        trade_date=TRADE_DATE,
        **kwargs,
    )
    return QuantLibBondFutureCalculator().compute_bond_future_analytics(request, market)


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
def test_calculator_satisfies_the_protocol():
    assert isinstance(QuantLibBondFutureCalculator(), BondFutureCalculator)


def test_empty_basket_is_rejected(market):
    with pytest.raises(BondFutureAnalyticsError, match="empty"):
        _analyse(DeliveryBasket(bond_future=FBTP_U6), market)


def test_evaluation_date_is_restored(basket, market):
    ql.Settings.instance().evaluationDate = ql.Date(1, 1, 2020)
    _analyse(basket, market)
    assert ql.Settings.instance().evaluationDate == ql.Date(1, 1, 2020)


def test_trade_date_after_delivery_is_rejected(basket):
    with pytest.raises(ValueError, match="after the delivery date"):
        BondFutureInput(
            delivery_basket=basket, bond_future=FBTP_U6, trade_date=date(2027, 1, 1)
        )


@pytest.mark.parametrize(
    ("field", "value"), [("shift_bp", 0.0), ("shift_bp", -1.0), ("futures_price", -5.0)]
)
def test_invalid_request_fields_are_rejected(basket, field, value):
    with pytest.raises(ValueError):
        BondFutureInput(
            delivery_basket=basket,
            bond_future=FBTP_U6,
            trade_date=TRADE_DATE,
            **{field: value},
        )


# --------------------------------------------------------------------------- #
# Implied futures price
# --------------------------------------------------------------------------- #
def test_implied_price_pins_the_ctd_net_basis_to_zero(basket, market, flat_repo):
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    assert result.futures_price_is_implied
    assert result.ctd().net_basis == pytest.approx(0.0, abs=1e-10)


def test_implied_price_is_the_cheapest_forward_over_its_factor(
    basket, market, flat_repo
):
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    assert result.futures_price == pytest.approx(
        min(o.implied_fair_futures_price for o in result.outputs)
    )
    assert result.futures_price == pytest.approx(
        result.ctd().implied_fair_futures_price
    )


def test_supplied_price_is_used_verbatim(basket, market, flat_repo):
    result = _analyse(
        basket, market, repo_term_structure=flat_repo, futures_price=120.0
    )
    assert not result.futures_price_is_implied
    assert result.futures_price == 120.0


def test_every_other_bond_has_a_positive_net_basis(basket, market, flat_repo):
    """The CTD is cheapest, so nothing else can beat it."""
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    assert all(output.net_basis >= -1e-10 for output in result.outputs)
    assert any(output.net_basis > 0 for output in result.outputs[1:])


# --------------------------------------------------------------------------- #
# Basis and implied repo
# --------------------------------------------------------------------------- #
def test_ctd_implied_repo_equals_the_financing_rate(basket, market, flat_repo):
    """Under a flat 3% repo the CTD earns exactly the repo rate."""
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    assert result.repo_rate == pytest.approx(3.0)
    assert result.ctd().implied_repo_rate == pytest.approx(3.0, abs=1e-8)


def test_gross_basis_is_clean_price_less_the_invoice(basket, market, flat_repo):
    result = _analyse(
        basket, market, repo_term_structure=flat_repo, futures_price=120.0
    )
    for output in result.outputs:
        expected = output.clean_price - 120.0 * output.conversion_factor
        assert output.gross_basis == pytest.approx(expected, abs=1e-12)


def test_net_basis_is_the_forward_price_less_the_invoice(basket, market, flat_repo):
    result = _analyse(
        basket, market, repo_term_structure=flat_repo, futures_price=120.0
    )
    for output in result.outputs:
        expected = output.forward_clean_price - 120.0 * output.conversion_factor
        assert output.net_basis == pytest.approx(expected, abs=1e-12)


def test_implied_repo_falls_as_the_futures_price_falls(basket, market, flat_repo):
    """A cheaper future means a worse return on delivering into it."""
    high = _analyse(basket, market, repo_term_structure=flat_repo, futures_price=126.0)
    low = _analyse(basket, market, repo_term_structure=flat_repo, futures_price=120.0)
    assert low.ctd().implied_repo_rate < high.ctd().implied_repo_rate


def test_curve_forward_is_used_when_no_repo_curve_is_supplied(basket, market):
    result = _analyse(basket, market)
    assert result.repo_rate == pytest.approx(FLAT_RATE * 100.0, abs=0.1)
    assert result.ctd().implied_repo_rate == pytest.approx(result.repo_rate, abs=1e-8)


# --------------------------------------------------------------------------- #
# Per-bond repo term structure overrides
# --------------------------------------------------------------------------- #
def test_per_bond_repo_override_changes_only_that_bonds_carry(basket, market, flat_repo):
    """A bond-specific repo curve should reprice just that bond's forward."""
    special = NumericTermStructure(
        {"1m": 1.0, "3m": 1.0, "6m": 1.0, "1y": 1.0}, TRADE_DATE
    )
    baseline = _analyse(basket, market, repo_term_structure=flat_repo, futures_price=120.0)

    overridden = DeliveryBasket(bond_future=FBTP_U6)
    target_bond = basket.members[0].bond
    for member in basket.members:
        overridden.add(
            member.bond,
            repo_term_structure=special if member.bond is target_bond else None,
        )
    request = BondFutureInput(
        delivery_basket=overridden,
        bond_future=FBTP_U6,
        trade_date=TRADE_DATE,
        repo_term_structure=flat_repo,
        futures_price=120.0,
    )
    result = QuantLibBondFutureCalculator().compute_bond_future_analytics(request, market)

    by_id = {o.bond.user_friendly_id: o for o in result.outputs}
    baseline_by_id = {o.bond.user_friendly_id: o for o in baseline.outputs}
    changed = by_id[target_bond.user_friendly_id]
    unchanged_id = next(
        m.bond.user_friendly_id for m in basket.members if m.bond is not target_bond
    )

    # The overridden bond's forward price (and hence net basis) moves...
    assert changed.forward_clean_price != pytest.approx(
        baseline_by_id[target_bond.user_friendly_id].forward_clean_price
    )
    # ...but every other bond still carries at the basket-wide 3% repo.
    assert by_id[unchanged_id].forward_clean_price == pytest.approx(
        baseline_by_id[unchanged_id].forward_clean_price
    )
    # The basket-level reported repo_rate still reflects the basket-wide default.
    assert result.repo_rate == pytest.approx(3.0)


def test_basket_wide_repo_still_applies_when_no_member_has_an_override(basket, market, flat_repo):
    """Existing single-term-structure behaviour is unchanged."""
    plain = _analyse(basket, market, repo_term_structure=flat_repo)

    no_override_basket = DeliveryBasket(bond_future=FBTP_U6)
    for member in basket.members:
        no_override_basket.add(member.bond)
    request = BondFutureInput(
        delivery_basket=no_override_basket,
        bond_future=FBTP_U6,
        trade_date=TRADE_DATE,
        repo_term_structure=flat_repo,
    )
    same = QuantLibBondFutureCalculator().compute_bond_future_analytics(request, market)

    for a, b in zip(plain.outputs, same.outputs):
        assert a.forward_clean_price == pytest.approx(b.forward_clean_price)


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def test_index_zero_is_the_cheapest_to_deliver(basket, market, flat_repo):
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    assert result.ctd() is result.outputs[0]
    assert result.ctd().index == 0
    assert result.ctd().implied_repo_rate == max(
        o.implied_repo_rate for o in result.outputs
    )


def test_outputs_are_ordered_by_descending_implied_repo(basket, market, flat_repo):
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    rates = [o.implied_repo_rate for o in result.outputs]
    assert rates == sorted(rates, reverse=True)
    assert [o.index for o in result.outputs] == list(range(len(result.outputs)))


def test_ctd_on_an_empty_result_explains_itself():
    from cqfi.bond_future_output import BondFutureBasketOutput

    empty = BondFutureBasketOutput(
        bond_future=FBTP_U6,
        trade_date=TRADE_DATE,
        settlement_date=TRADE_DATE,
        delivery_date=date(2026, 9, 10),
        futures_price=100.0,
        futures_price_is_implied=True,
        repo_rate=3.0,
        outputs=(),
    )
    with pytest.raises(ValueError, match="empty delivery basket"):
        empty.ctd()


# --------------------------------------------------------------------------- #
# Sensitivities
# --------------------------------------------------------------------------- #
def test_delta_is_negative_and_gamma_positive(basket, market, flat_repo):
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    for output in result.outputs:
        assert output.delta < 0
        assert output.gamma > 0


def test_longer_bonds_have_larger_delta(basket, market, flat_repo):
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    by_maturity = sorted(result.outputs, key=lambda o: o.bond.maturity)
    deltas = [abs(o.delta) for o in by_maturity]
    assert deltas == sorted(deltas)


def test_delta_is_insensitive_to_the_shift_size(basket, market, flat_repo):
    """A central difference should be stable across sensible bump sizes."""
    one_bp = _analyse(basket, market, repo_term_structure=flat_repo, shift_bp=1.0)
    five_bp = _analyse(basket, market, repo_term_structure=flat_repo, shift_bp=5.0)
    assert one_bp.ctd().delta == pytest.approx(five_bp.ctd().delta, rel=1e-3)


# --------------------------------------------------------------------------- #
# Conversion factor overrides
# --------------------------------------------------------------------------- #
def test_hard_coded_conversion_factors_are_used_verbatim(basket, market, flat_repo):
    overridden = DeliveryBasket(bond_future=FBTP_U6)
    for member in basket.members:
        overridden.add(member.bond, 1.5)

    result = _analyse(overridden, market, repo_term_structure=flat_repo)
    assert {o.conversion_factor for o in result.outputs} == {1.5}


def test_computed_and_overridden_factors_can_be_mixed(basket, market, flat_repo):
    mixed = DeliveryBasket(bond_future=FBTP_U6)
    members = list(basket.members)
    mixed.add(members[0].bond, 0.99)
    for member in members[1:]:
        mixed.add(member.bond)

    result = _analyse(mixed, market, repo_term_structure=flat_repo)
    factors = {o.bond.user_friendly_id: o.conversion_factor for o in result.outputs}
    assert factors[members[0].bond.user_friendly_id] == 0.99
    assert factors[members[1].bond.user_friendly_id] != 0.99


def test_non_positive_conversion_factor_is_rejected(basket, market, flat_repo):
    broken = DeliveryBasket(bond_future=FBTP_U6)
    broken.add(basket.members[0].bond, 0.0)
    with pytest.raises(BondFutureAnalyticsError, match="must be positive"):
        _analyse(broken, market, repo_term_structure=flat_repo)


# --------------------------------------------------------------------------- #
# JGB domestic vs international financing
# --------------------------------------------------------------------------- #
def test_domestic_and_international_jgb_repo_bases_differ():
    """Act/365F accrues over more of a year than Act/360, so rates differ."""
    trade = date(2026, 5, 15)
    ql.Settings.instance().evaluationDate = to_ql_date(trade)
    curve = ql.YieldTermStructureHandle(
        ql.FlatForward(
            to_ql_date(trade),
            FLAT_RATE,
            ql.ActualActual(ql.ActualActual.ISDA),
            ql.Compounded,
            ql.Annual,
        )
    )
    collection = QuantLibCurveCollection(trade)
    collection.set_bond_curve("JPN", curve)
    context = QuantlibMarketContext()
    context.set_curve_collection(collection, label="BOND_ZERO")

    calculator = QuantLibBondFutureCalculator()
    repo = NumericTermStructure({"3m": 3.0, "6m": 3.0, "1y": 3.0}, trade)
    results = {}
    for name in ("FJGB", "FJGB_DOM"):
        future = BondFuture(BOND_FUTURE_CONVENTIONS[name], 9, 2026)
        basket = DeliveryBasket(bond_future=future)
        basket.add(
            Bond(
                issuer="JPN",
                maturity=date(2035, 9, 20),
                bond_id="JP0001",
                coupon=1.5,
                issue_date=date(2015, 9, 20),
            )
        )
        results[name] = calculator.compute_bond_future_analytics(
            BondFutureInput(
                delivery_basket=basket,
                bond_future=future,
                trade_date=trade,
                repo_term_structure=repo,
                futures_price=140.0,
            ),
            context,
        )

    international, domestic = results["FJGB"], results["FJGB_DOM"]
    # The conversion factor is a contract term, unaffected by financing.
    assert international.ctd().conversion_factor == domestic.ctd().conversion_factor
    # Act/365F fractions are 360/365 of Act/360 ones, so the implied rate scales.
    assert domestic.ctd().implied_repo_rate == pytest.approx(
        international.ctd().implied_repo_rate * 365.0 / 360.0, rel=1e-9
    )


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
def test_dataframe_columns_and_order(basket, market, flat_repo):
    frame = _analyse(basket, market, repo_term_structure=flat_repo).to_polars()
    assert tuple(frame.columns) == BOND_FUTURE_OUTPUT_COLUMNS
    assert frame["index"].to_list() == [0, 1, 2]
    assert frame.height == 3


def test_json_nests_each_bond_with_its_analytics(basket, market, flat_repo):
    result = _analyse(basket, market, repo_term_structure=flat_repo)
    payload = json.loads(result.as_json())

    assert payload["contract"] == "IKU6"
    assert payload["trade_date"] == "2026-05-15"
    assert payload["delivery_date"] == "2026-09-10"
    assert payload["futures_price_is_implied"] is True
    assert payload["bond_count"] == 3

    entries = payload["analytics"]
    assert [entry["index"] for entry in entries] == [0, 1, 2]
    first = entries[0]
    assert first["bond"]["user_friendly_id"] == result.ctd().bond.user_friendly_id
    assert first["bond"]["issuer"] == "ITA"
    assert first["net_basis"] == pytest.approx(0.0, abs=1e-10)


def test_len_reports_the_basket_size(basket, market, flat_repo):
    assert len(_analyse(basket, market, repo_term_structure=flat_repo)) == 3
