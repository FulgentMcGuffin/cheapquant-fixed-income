"""Tests for day-of-month specifications and their holiday adjustment."""

from __future__ import annotations

from datetime import date, timedelta

import QuantLib as ql
import pytest

from cqfi.day_of_month import (
    CalendarDayRule,
    DayOfMonthSpec,
    DayOfMonthSpecError,
    HolidayAdjustmentRule,
)

TARGET = ql.TARGET()


# --------------------------------------------------------------------------- #
# Calendar day rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("spec", "year", "month", "expected"),
    [
        ("C1", 2026, 1, date(2026, 1, 1)),
        ("C20", 2026, 1, date(2026, 1, 20)),
        ("C31", 2026, 1, date(2026, 1, 31)),
        # L is 0-based from the last day.
        ("L0", 2026, 1, date(2026, 1, 31)),
        ("L0", 2026, 2, date(2026, 2, 28)),
        ("L0", 2024, 2, date(2024, 2, 29)),
        ("L9", 2026, 2, date(2026, 2, 19)),
        ("L9", 2024, 2, date(2024, 2, 20)),
        ("L9", 2026, 1, date(2026, 1, 22)),
        # Nth weekday of the month.
        ("WED3", 2026, 1, date(2026, 1, 21)),
        ("FRI1", 2026, 1, date(2026, 1, 2)),
        ("MON1", 2026, 6, date(2026, 6, 1)),
        ("SUN4", 2026, 3, date(2026, 3, 22)),
    ],
)
def test_calendar_day_rule_resolves(spec, year, month, expected):
    assert CalendarDayRule.parse(spec).day_in_month(year, month) == expected


@pytest.mark.parametrize("spec", ["WED5", "FRI5", "MON9"])
def test_weekday_ordinal_of_five_or_more_is_rejected(spec):
    with pytest.raises(DayOfMonthSpecError, match="ordinal must be 1..4"):
        CalendarDayRule.parse(spec)


@pytest.mark.parametrize(
    ("spec", "year", "month"),
    [
        ("C31", 2026, 4),  # April has 30 days
        ("C30", 2026, 2),
        ("L28", 2026, 2),  # 28 - 28 == 0
        ("L31", 2026, 1),
    ],
)
def test_calendar_day_out_of_range_raises(spec, year, month):
    with pytest.raises(DayOfMonthSpecError, match="out of range"):
        CalendarDayRule.parse(spec).day_in_month(year, month)


@pytest.mark.parametrize(
    "text", ["", "C", "C0", "L", "FRI0", "SATX", "X3", "1C", "CC1"]
)
def test_malformed_calendar_day_rule_raises(text):
    with pytest.raises(DayOfMonthSpecError):
        CalendarDayRule.parse(text)


def test_calendar_day_rule_is_case_insensitive():
    assert CalendarDayRule.parse("wed3") == CalendarDayRule.parse("WED3")
    assert CalendarDayRule.parse(" c10 ") == CalendarDayRule.parse("C10")


# --------------------------------------------------------------------------- #
# Holiday adjustment rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["", "1", "-2", "3X", "BD", "FRI", "1FRIX"])
def test_malformed_adjustment_raises(text):
    with pytest.raises(DayOfMonthSpecError):
        HolidayAdjustmentRule.parse(text)


def test_zero_bd_is_identity():
    rule = HolidayAdjustmentRule.parse("0BD")
    assert rule.is_identity
    # A holiday is returned untouched.
    assert rule.adjust(date(2026, 1, 1), TARGET) == date(2026, 1, 1)


# --------------------------------------------------------------------------- #
# Full specifications
# --------------------------------------------------------------------------- #
def test_business_day_base_is_never_adjusted():
    """The adjustment fires only on weekends and holidays."""
    # 2026-01-20 is an ordinary Tuesday.
    for adjustment in ("0BD", "1BD", "-1BD", "2BD", "1FRI", "1FRIBD"):
        spec = DayOfMonthSpec.parse("C20", adjustment)
        assert spec.resolve(2026, 1, TARGET) == date(2026, 1, 20)


@pytest.mark.parametrize(
    ("day", "adjustment", "year", "month", "expected"),
    [
        # 2026-01-01 is a Thursday and a TARGET holiday.
        ("C1", "0BD", 2026, 1, date(2026, 1, 1)),
        ("C1", "1BD", 2026, 1, date(2026, 1, 2)),
        ("C1", "2BD", 2026, 1, date(2026, 1, 5)),
        ("C1", "-1BD", 2026, 1, date(2025, 12, 31)),
        # 2026-05-01 is a Friday and a TARGET holiday.
        ("C1", "1BD", 2026, 5, date(2026, 5, 4)),
        # 2026-03-28 is a Saturday; the next Friday is Good Friday 2026-04-03.
        ("C28", "0BD", 2026, 3, date(2026, 3, 28)),
        ("C28", "1FRI", 2026, 3, date(2026, 4, 3)),
        ("C28", "1FRIBD", 2026, 3, date(2026, 4, 10)),
        ("C28", "-1WEDBD", 2026, 3, date(2026, 3, 25)),
        ("C28", "2BD", 2026, 3, date(2026, 3, 31)),
        ("C28", "-1BD", 2026, 3, date(2026, 3, 27)),
    ],
)
def test_spec_resolution(day, adjustment, year, month, expected):
    spec = DayOfMonthSpec.parse(day, adjustment)
    assert spec.resolve(year, month, TARGET) == expected


def test_1fri_and_1fribd_differ_across_good_friday():
    """The BD suffix is what steps past a holiday Friday."""
    plain = DayOfMonthSpec.parse("C28", "1FRI").resolve(2026, 3, TARGET)
    business = DayOfMonthSpec.parse("C28", "1FRIBD").resolve(2026, 3, TARGET)
    assert plain == date(2026, 4, 3)
    assert not TARGET.isBusinessDay(ql.Date(3, 4, 2026))
    assert business == plain + timedelta(days=7)


def test_adjustment_may_leave_the_month():
    """``L0 1BD`` on a weekend month end deliberately rolls into the next month."""
    # 2026-01-31 is a Saturday.
    assert DayOfMonthSpec.parse("L0", "1BD").resolve(2026, 1, TARGET) == date(
        2026, 2, 2
    )
    # The backwards form is what contracts use to stay inside the month.
    assert DayOfMonthSpec.parse("L0", "-1BD").resolve(2026, 1, TARGET) == date(
        2026, 1, 30
    )


def test_resolve_for_uses_the_month_of_the_given_date():
    spec = DayOfMonthSpec.parse("C10", "1BD")
    assert spec.resolve_for(date(2026, 5, 27), TARGET) == spec.resolve(2026, 5, TARGET)


# --------------------------------------------------------------------------- #
# Parsing and round-tripping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text", ["C10 1BD", "C1 0BD", "L0 -1BD", "WED3 0BD", "C28 -1WEDBD", "C28 1FRI"]
)
def test_from_string_round_trips(text):
    assert str(DayOfMonthSpec.from_string(text)) == text


def test_from_string_defaults_to_no_adjustment():
    assert DayOfMonthSpec.from_string("C10") == DayOfMonthSpec.parse("C10", "0BD")
    assert str(DayOfMonthSpec.from_string("WED3")) == "WED3 0BD"


@pytest.mark.parametrize("text", ["", "C10 1BD extra", "   "])
def test_from_string_rejects_wrong_token_count(text):
    with pytest.raises(DayOfMonthSpecError):
        DayOfMonthSpec.from_string(text)


def test_as_dict_exposes_both_components():
    assert DayOfMonthSpec.from_string("C10 1BD").as_dict() == {
        "calendar_rule": "C10",
        "adjustment": "1BD",
    }


def test_spec_is_hashable_and_frozen():
    spec = DayOfMonthSpec.from_string("C10 1BD")
    assert spec in {spec}
    with pytest.raises(Exception):
        spec.calendar_rule = CalendarDayRule.parse("C1")
