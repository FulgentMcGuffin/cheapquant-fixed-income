"""Tests for RepoTermStructure."""

from __future__ import annotations

import json
from datetime import date

import pytest

from cheapquant_fi.repo_term_structure import RepoTermStructure


def test_repo_term_structure_orders_by_maturity():
    as_of = date(2024, 1, 15)
    structure = RepoTermStructure(
        [("30d", 4.5), ("1m", 4.25), ("7d", 4.0)],
        as_of,
    )
    assert list(structure.to_dict()) == ["1w", "4w2d", "1m"]
    assert structure.to_dict() == {"1w": 4.0, "4w2d": 4.5, "1m": 4.25}


def test_repo_term_structure_simplifies_and_detects_duplicates():
    as_of = date(2024, 1, 15)
    with pytest.raises(ValueError, match="Duplicate tenor"):
        RepoTermStructure([("52w", 4.0), ("1y", 4.1)], as_of)


def test_repo_term_structure_to_json():
    as_of = date(2024, 1, 15)
    structure = RepoTermStructure([("7d", 4.0), ("1m", 4.25)], as_of)
    payload = json.loads(structure.to_json())
    assert payload == {"1w": 4.0, "1m": 4.25}
