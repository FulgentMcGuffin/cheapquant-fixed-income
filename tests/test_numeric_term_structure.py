"""Tests for NumericTermStructure."""

from __future__ import annotations

import json
from datetime import date

import pytest

from cqfi.numeric_term_structure import NumericTermStructure


def test_repo_term_structure_orders_by_maturity():
    as_of = date(2024, 1, 15)
    structure = NumericTermStructure(
        [("30d", 4.5), ("1m", 4.25), ("7d", 4.0)],
        as_of,
    )
    assert list(structure.to_dict()) == ["1w", "4w2d", "1m"]
    assert structure.to_dict() == {"1w": 0.04, "4w2d": 0.045, "1m": 0.0425}


def test_repo_term_structure_simplifies_and_detects_duplicates():
    as_of = date(2024, 1, 15)
    with pytest.raises(ValueError, match="Duplicate tenor"):
        NumericTermStructure([("52w", 4.0), ("1y", 4.1)], as_of)


def test_repo_term_structure_to_json():
    as_of = date(2024, 1, 15)
    structure = NumericTermStructure([("7d", 4.0), ("1m", 4.25)], as_of)
    payload = json.loads(structure.to_json())
    assert payload == {"1w": 0.04, "1m": 0.0425}


def test_repo_term_structure_from_dict():
    as_of = date(2024, 1, 15)
    structure = NumericTermStructure(
        {"1w": 4.0, "4w2d": 4.5, "1m": 4.25},
        as_of,
    )
    assert structure.to_dict() == {"1w": 0.04, "4w2d": 0.045, "1m": 0.0425}

    round_trip = NumericTermStructure(structure.to_dict(), as_of, to_decimal=False)
    assert round_trip.to_dict() == structure.to_dict()


def test_a_single_tenor_is_a_flat_rate_held_for_eternity():
    """A one-point term structure — e.g. from a bare CLI number — should

    extrapolate flat no matter how far out the requested date is.
    """
    as_of = date(2024, 1, 15)
    structure = NumericTermStructure({"1d": 3.0}, as_of)

    assert structure.rate_for(date(2024, 1, 16)) == pytest.approx(0.03)
    assert structure.rate_for(date(2054, 1, 15)) == pytest.approx(0.03)
    assert structure.rate_for(date(2124, 6, 1)) == pytest.approx(0.03)
