"""Shared date helpers bridging :mod:`datetime` and QuantLib.

This module deliberately imports nothing from ``cheapquant_fi`` so that any
module may depend on it without risking an import cycle.
"""

from __future__ import annotations

import calendar
from datetime import date

import QuantLib as ql


def to_ql_date(value: date) -> ql.Date:
    """Convert a :class:`datetime.date` to a :class:`QuantLib.Date`."""
    return ql.Date(value.day, value.month, value.year)


def from_ql_date(value: ql.Date) -> date:
    """Convert a :class:`QuantLib.Date` to a :class:`datetime.date`."""
    return date(value.year(), value.month(), value.dayOfMonth())


def days_in_month(year: int, month: int) -> int:
    """Return the number of days in *month* of *year*."""
    return calendar.monthrange(year, month)[1]


def month_add(year: int, month: int, months: int) -> tuple[int, int]:
    """Shift a ``(year, month)`` pair by *months*, which may be negative."""
    index = (year * 12 + month - 1) + months
    return index // 12, index % 12 + 1


def add_months(when: date, months: int) -> date:
    """Shift *when* by *months*, clamping the day to the target month's length."""
    year, month = month_add(when.year, when.month, months)
    return date(year, month, min(when.day, days_in_month(year, month)))


def whole_months_between(start: date, end: date) -> int:
    """Return complete calendar months from *start* to *end* (never negative)."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)
