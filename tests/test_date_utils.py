"""Tests for shared datetime/QuantLib date helpers."""

from __future__ import annotations

from datetime import date

import pytest

from cqfi.date_utils import (
    add_months,
    days_in_month,
    from_ql_date,
    month_add,
    to_ql_date,
    whole_months_between,
)


def test_ql_date_roundtrip():
    for value in (date(2026, 2, 19), date(2024, 2, 29), date(1999, 12, 31)):
        assert from_ql_date(to_ql_date(value)) == value


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [(2026, 2, 28), (2024, 2, 29), (2000, 2, 29), (1900, 2, 28), (2026, 4, 30)],
)
def test_days_in_month(year, month, expected):
    assert days_in_month(year, month) == expected


@pytest.mark.parametrize(
    ("year", "month", "months", "expected"),
    [
        (2026, 8, 1, (2026, 9)),
        (2026, 12, 1, (2027, 1)),
        (2026, 1, -1, (2025, 12)),
        (2026, 3, 12, (2027, 3)),
        (2026, 3, -15, (2024, 12)),
        (2026, 8, 0, (2026, 8)),
    ],
)
def test_month_add_crosses_year_boundaries(year, month, months, expected):
    assert month_add(year, month, months) == expected


def test_add_months_clamps_to_shorter_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 3, 15), -1) == date(2026, 2, 15)


def test_whole_months_between():
    assert whole_months_between(date(2026, 1, 15), date(2026, 4, 15)) == 3
    assert whole_months_between(date(2026, 1, 15), date(2026, 4, 14)) == 2
    assert whole_months_between(date(2026, 1, 15), date(2029, 1, 15)) == 36
    # Never negative, even when end precedes start.
    assert whole_months_between(date(2026, 4, 15), date(2026, 1, 15)) == 0
