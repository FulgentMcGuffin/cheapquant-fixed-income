"""Tests for forward-starting CompositeTenor."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from cqfi.composite_tenor import CompositeTenor, split_combined_tenor
from cqfi.issuers import ISSUERS
from cqfi.tenor import Tenor


def test_str_french_forward_tenor():
    ct = CompositeTenor(ISSUERS["FRA"], Tenor.parse("10y"), Tenor.parse("2y"))
    assert str(ct) == "fra10y2y"


def test_from_strings():
    ct = CompositeTenor.from_strings("fra", "10y", "2y")
    assert ct.issuer_profile is ISSUERS["FRA"]
    assert str(ct) == "fra10y2y"


def test_init_accepts_string_parts():
    ct = CompositeTenor("FRA", "10y", "2y")
    assert str(ct) == "fra10y2y"


def test_forward_dates_on_calendar():
    fra = ISSUERS["FRA"]
    start_tenor = Tenor(years=10)
    forward_tenor = Tenor(years=2)
    ct = CompositeTenor(fra, start_tenor, forward_tenor)
    anchor = date(2024, 1, 15)
    start = ct.forward_start_date(anchor)
    end = ct.forward_end_date(anchor)
    assert start == start_tenor.add_to(anchor, fra)
    assert end == forward_tenor.add_to(start, fra)


def test_forward_dates_preserve_datetime():
    ct = CompositeTenor(ISSUERS["DEU"], Tenor(days=1), Tenor(hours=2))
    anchor = datetime(2024, 1, 15, 9, 30, 0)
    start = ct.forward_start_date(anchor)
    end = ct.forward_end_date(anchor)
    assert isinstance(start, datetime)
    assert isinstance(end, datetime)
    assert start == datetime(2024, 1, 16, 9, 30, 0)
    assert end == datetime(2024, 1, 16, 11, 30, 0)


def test_unknown_issuer_string_raises():
    with pytest.raises(ValueError, match="Unknown issuer"):
        CompositeTenor("UNKNOWN", "1y", "1y")


@pytest.mark.parametrize(
    ("combined", "starting", "forward"),
    [
        ("10y12y", "10y", "12y"),
        ("3w18m", "3w", "18m"),
        ("4m10y3w1y12d", "4m10y3w", "1y12d"),
        ("10m1M12y", "10m", "1M12y"),
        ("10y2m4W", "10y2m", "4W"),
        ("18m5d2y", "18m5d", "2y"),
        ("10y", "", "10y"),
    ],
)
def test_split_combined_tenor(combined: str, starting: str, forward: str):
    assert split_combined_tenor(combined) == (starting, forward)


def test_from_combined_tenor_two_indicators():
    ct = CompositeTenor.from_combined_tenor("fra", "10y12y")
    assert ct.starting_tenor == Tenor.parse("10y")
    assert ct.forward_tenor == Tenor.parse("12y")
    assert str(ct) == "fra10y12y"


def test_from_combined_tenor_single_indicator_starts_immediately():
    ct = CompositeTenor.from_combined_tenor("DEU", "5y")
    assert ct.starting_tenor == Tenor()
    anchor = date(2024, 1, 15)
    assert ct.forward_start_date(anchor) == anchor
    assert ct.forward_end_date(anchor) == Tenor.parse("5y").add_to(anchor, ISSUERS["DEU"])


def test_from_combined_tenor_end_date_roughly_sum_of_legs():
    ct = CompositeTenor.from_combined_tenor("FRA", "10y12y")
    anchor = date(2024, 1, 15)
    end = ct.forward_end_date(anchor)
    via_combined = Tenor.parse("10y12y").add_to(anchor, ISSUERS["FRA"])
    # End is start+12y, not single parse of 10y12y — compare to chained add_to
    expected = Tenor.parse("12y").add_to(Tenor.parse("10y").add_to(anchor, ISSUERS["FRA"]), ISSUERS["FRA"])
    assert end == expected
    assert end != via_combined
