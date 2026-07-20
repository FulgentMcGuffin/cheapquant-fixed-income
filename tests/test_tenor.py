"""Tests for Tenor string parsing and display."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from cheapquant_fi.issuers import ISSUERS
from cheapquant_fi.tenor import Tenor


def test_parse_compound_tenor():
    tenor = Tenor.parse("12y4M3w12d97h1`15``")
    assert tenor.years == 12
    assert tenor.months == 4
    assert tenor.weeks == 3
    assert tenor.days == 12
    assert tenor.hours == 97
    assert tenor.minutes == 1
    assert tenor.seconds == 15
    assert str(tenor) == "12y4m3w12d97h1`15``"


def test_parse_days_only():
    tenor = Tenor.parse("4264d")
    assert tenor.days == 4264
    assert str(tenor) == "4264d"


def test_parse_order_independent():
    tenor = Tenor.parse("4m36y")
    assert tenor.years == 36
    assert tenor.months == 4
    assert str(tenor) == "36y4m"


def test_parse_accumulates_repeated_units():
    tenor = Tenor.parse("2y3y1m")
    assert tenor.years == 5
    assert tenor.months == 1


def test_parse_rejects_invalid_unit():
    with pytest.raises(ValueError, match="Unknown tenor unit"):
        Tenor.parse("12x")


def test_parse_rejects_empty_string():
    with pytest.raises(ValueError, match="must not be empty"):
        Tenor.parse("")


def test_simplify_seconds_to_minutes():
    assert Tenor(seconds=261).simplify() == Tenor(minutes=4, seconds=21)


def test_simplify_hours_to_days():
    assert Tenor(hours=25).simplify() == Tenor(days=1, hours=1)


def test_simplify_days_to_weeks():
    assert Tenor(days=10).simplify() == Tenor(weeks=1, days=3)


def test_simplify_weeks_to_years():
    assert Tenor(weeks=53).simplify() == Tenor(years=1, weeks=1)


def test_simplify_months_to_years():
    assert Tenor(months=14).simplify() == Tenor(years=1, months=2)


def test_simplify_cascades_all_units():
    tenor = Tenor(
        years=1,
        months=13,
        weeks=53,
        days=8,
        hours=25,
        minutes=61,
        seconds=125,
    ).simplify()
    assert tenor == Tenor(years=3, months=1, weeks=2, days=2, hours=2, minutes=3, seconds=5)


def test_simplify_is_idempotent():
    tenor = Tenor.parse("12y14m53w8d25h61`125``")
    simplified = tenor.simplify()
    assert simplified.simplify() == simplified


def test_days_tenor_weeks_only():
    tenor = Tenor.parse("2w").days_tenor(date(2024, 1, 15))
    assert tenor == Tenor(days=14)


def test_days_tenor_ignores_sub_day_units():
    tenor = Tenor.parse("3d12h30`").days_tenor(date(2024, 1, 15))
    assert tenor == Tenor(days=3)


def test_days_tenor_month_from_end_of_month():
    tenor = Tenor(months=1).days_tenor(date(2024, 1, 31))
    assert tenor == Tenor(days=29)


def test_days_tenor_defaults_to_deu_calendar():
    tenor = Tenor(days=5).days_tenor(date(2024, 12, 30))
    assert tenor == Tenor(days=5)

    explicit_deu = Tenor(months=1).days_tenor(date(2024, 1, 31), ISSUERS["DEU"])
    assert explicit_deu == Tenor(days=29)


def test_add_to_date():
    result = Tenor(months=1).add_to(date(2024, 1, 31))
    assert result == date(2024, 2, 29)


def test_add_to_date_ignores_sub_day_units():
    result = Tenor(days=1, hours=5).add_to(date(2024, 1, 15))
    assert result == date(2024, 1, 16)


def test_add_to_datetime():
    start = datetime(2024, 1, 15, 10, 30, 0)
    result = Tenor(days=1, hours=2, minutes=15).add_to(start)
    assert result == datetime(2024, 1, 16, 12, 45, 0)


def test_add_to_defaults_to_deu():
    result = Tenor(months=1).add_to(date(2024, 1, 31))
    assert result == date(2024, 2, 29)


def test_compare_to_orders_by_maturity():
    anchor = date(2024, 1, 15)
    short = Tenor(days=1)
    long = Tenor(days=30)
    assert short.compare_to(long, anchor) < 0
    assert long.compare_to(short, anchor) > 0
    assert short.compare_to(Tenor(days=1), anchor) == 0


def test_compare_to_datetime_includes_sub_day():
    anchor = datetime(2024, 1, 15, 12, 0, 0)
    earlier = Tenor(hours=1)
    later = Tenor(hours=2)
    assert earlier.compare_to(later, anchor) < 0


def test_sort_key_orders_collection():
    anchor = date(2024, 1, 15)
    tenors = [Tenor(days=30), Tenor(days=1), Tenor(days=7)]
    ordered = sorted(tenors, key=Tenor.sort_key(anchor))
    assert [str(t) for t in ordered] == ["1d", "7d", "30d"]
