"""Tests for dated bond future contracts and delivery-month resolution."""

from __future__ import annotations

import json
from datetime import date

import pytest

from cqfi.bond_futures import (
    BOND_FUTURE_CONVENTIONS,
    BondFuture,
    BondFutureError,
    default_delivery_month,
    resolve_bond_future_convention,
    resolve_delivery_month,
)

# Fixed "today" so every relative delivery specifier is deterministic.
TODAY = date(2026, 8, 2)


# --------------------------------------------------------------------------- #
# Delivery month resolution
# --------------------------------------------------------------------------- #
def test_default_never_picks_the_current_month():
    assert default_delivery_month(TODAY) == (2026, 9)
    # From inside September the front contract rolls to December.
    assert default_delivery_month(date(2026, 9, 1)) == (2026, 12)
    assert default_delivery_month(date(2026, 9, 30)) == (2026, 12)
    # December rolls into the next year.
    assert default_delivery_month(date(2026, 12, 5)) == (2027, 3)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (None, (2026, 9)),
        ("", (2026, 9)),
        ("M8", (2028, 6)),  # June of the next year ending in 8
        ("U", (2026, 9)),  # next September
        ("H", (2027, 3)),  # next March
        ("6", (2026, 9)),  # next quarterly month in a year ending in 6
        ("7", (2027, 3)),
        ("2020-09", (2020, 9)),  # historical, ISO form
        ("U2020", (2020, 9)),  # historical, month-code form
        ("h7", (2027, 3)),  # case-insensitive
    ],
)
def test_resolve_delivery_month(token, expected):
    assert resolve_delivery_month(token, today=TODAY) == expected


def test_single_digit_year_respects_the_quarterly_cycle():
    """'6' means the next quarterly delivery in a year ending in 6."""
    assert resolve_delivery_month("6", today=date(2026, 10, 1)) == (2026, 12)
    # Past December 2026 the next year ending in 6 is 2036.
    assert resolve_delivery_month("6", today=date(2027, 1, 1)) == (2036, 3)


@pytest.mark.parametrize("token", ["QQ", "2020-13", "1999-1", "M-8", "ZZZ9", "13"])
def test_invalid_delivery_specifier_raises(token):
    with pytest.raises(BondFutureError):
        resolve_delivery_month(token, today=TODAY)


# --------------------------------------------------------------------------- #
# Parsing and rendering
# --------------------------------------------------------------------------- #
def test_worked_example_from_the_specification():
    """IKU9 is the Italian 10Y future for delivery in September 2029."""
    future = BondFuture.parse("IKU9", today=TODAY)
    assert future.convention.name == "FBTP"
    assert future.convention.issuer_code == "ITA"
    assert (future.delivery_year, future.delivery_month) == (2029, 9)
    assert str(future) == "IKU9"


@pytest.mark.parametrize(
    ("text", "name", "expected_str", "year", "month"),
    [
        ("IKU9", "FBTP", "IKU9", 2029, 9),
        ("FBTPU9", "FBTP", "IKU9", 2029, 9),
        ("IKH7", "FBTP", "IKH7", 2027, 3),
        ("FGBM", "FGBM", "OEU6", 2026, 9),
        ("OE", "FGBM", "OEU6", 2026, 9),
        ("FGBM M8", "FGBM", "OEM8", 2028, 6),
        ("FGBS 2020-09", "FGBS", "DUU0", 2020, 9),
        ("FGBS U2020", "FGBS", "DUU0", 2020, 9),
        ("JBU9", "FJGB", "JBU9", 2029, 9),
        ("JB5", "FJG5", "JB5U6", 2026, 9),  # BBG root ending in a digit
        ("Z3N", "Z3N", "3YU6", 2026, 9),  # canonical code containing a digit
        ("TUZ6", "ZT", "TUZ6", 2026, 12),
        ("G", "G", "GU6", 2026, 9),
        ("R", "R", "RU6", 2026, 9),
    ],
)
def test_parse_and_render(text, name, expected_str, year, month):
    future = BondFuture.parse(text, today=TODAY)
    assert future.convention.name == name
    assert (future.delivery_year, future.delivery_month) == (year, month)
    assert str(future) == expected_str


@pytest.mark.parametrize(
    "text",
    ["IKU9", "OEU6", "TUZ6", "JB5U6", "3YU6", "GU6", "RU6", "FGBXU9", "WNU9"],
)
def test_str_round_trips_through_parse(text):
    """Rendering must never produce a code that parses to another contract."""
    future = BondFuture.parse(text, today=TODAY)
    assert str(BondFuture.parse(str(future), today=TODAY)) == str(future)
    assert BondFuture.parse(str(future), today=TODAY).convention is future.convention


def test_shared_bloomberg_roots_fall_back_to_the_canonical_code():
    """Buxl and the Ultra Gilt share 'UB' with CME, so they render canonically."""
    assert str(BondFuture.parse("FGBX", today=TODAY)) == "FGBXU6"
    assert str(BondFuture.parse("U", today=TODAY)) == "UU6"
    # The CME Ultra Bond keeps its unambiguous Bloomberg root.
    assert str(BondFuture.parse("UB", today=TODAY)) == "WNU6"
    # The Long Gilt's BBG root 'G' belongs to the Short Gilt canonically.
    assert str(BondFuture.parse("R", today=TODAY)) == "RU6"
    assert str(BondFuture.parse("G", today=TODAY)) == "GU6"


@pytest.mark.parametrize("text", ["", "   ", "ZZZZ", "IK U9 extra", "IKQQ"])
def test_invalid_contract_specifier_raises(text):
    with pytest.raises(BondFutureError):
        BondFuture.parse(text, today=TODAY)


def test_month_codes_cover_the_year():
    codes = [
        str(BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], month, 2029))[-2]
        for month in range(1, 13)
    ]
    assert codes == list("FGHJKMNQUVXZ")


def test_invalid_delivery_month_rejected():
    with pytest.raises(BondFutureError):
        BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 13, 2029)


# --------------------------------------------------------------------------- #
# Contract dates
# --------------------------------------------------------------------------- #
def test_eurex_dates_all_fall_on_the_tenth():
    future = BondFuture.parse("IKU9", today=TODAY)
    assert future.reference_date() == date(2029, 9, 10)
    assert future.delivery_start_date() == date(2029, 9, 10)
    assert future.delivery_end_date() == date(2029, 9, 10)


def test_cme_delivery_period_spans_the_month():
    future = BondFuture.parse("ZNU6", today=TODAY)
    assert future.reference_date() == date(2026, 9, 1)
    assert future.delivery_start_date() == date(2026, 9, 1)
    assert future.delivery_end_date() == date(2026, 9, 30)


def test_delivery_day_rolls_off_a_holiday():
    """Osaka delivers on the 20th, rolled forward over the holiday cluster."""
    future = BondFuture.parse("JBU6", today=TODAY)
    # 2026-09-20 is a Sunday followed by Japanese public holidays.
    assert future.delivery_end_date() > date(2026, 9, 20)
    assert future.delivery_end_date().month == 9


def test_notional_bond_maturity_is_delivery_end_plus_contract_years():
    future = BondFuture.parse("IKU9", today=TODAY)
    assert future.delivery_end_date() == date(2029, 9, 10)
    assert future.convention.notional_maturity_years == 10
    assert future.notional_bond_maturity() == date(2039, 9, 10)

    short = BondFuture.parse("FGBS U2020", today=TODAY)
    assert short.notional_bond_maturity() == date(2022, 9, 10)


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def test_repr_is_valid_json_describing_the_whole_structure():
    future = BondFuture.parse("IKU9", today=TODAY)
    payload = json.loads(repr(future))

    assert payload["contract"] == "IKU9"
    assert payload["delivery_year"] == 2029
    assert payload["reference_date"] == "2029-09-10"
    assert payload["notional_bond_maturity"] == "2039-09-10"

    convention = payload["convention"]
    assert convention["issuer"] == "ITA"
    assert convention["currency"] == "EUR"
    assert convention["notional_coupon"] == 6.0
    assert convention["repo_day_count"] == "Actual/360"
    assert convention["restrictions"]["remaining_maturity"]["min_months"] == 102


def test_default_classmethod_matches_module_default():
    convention = resolve_bond_future_convention("FGBM")
    future = BondFuture.default(convention, today=TODAY)
    assert (future.delivery_year, future.delivery_month) == default_delivery_month(
        TODAY
    )
