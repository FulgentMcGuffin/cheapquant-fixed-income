"""Tests for the bond future convention registry and code resolution."""

from __future__ import annotations

import json

import pytest

import cheapquant_fi.bond_futures as bond_futures
from cheapquant_fi.bond_futures import (
    BOND_FUTURE_CONVENTIONS,
    AmbiguousBondFutureError,
    BondFutureError,
    ConversionFactorMethod,
    MaturityRange,
    months,
    resolve_bond_future_convention,
)
from cheapquant_fi.issuers import ISSUERS, RepoMarket

ALL_CONVENTIONS = tuple(BOND_FUTURE_CONVENTIONS.values())


# --------------------------------------------------------------------------- #
# Registry integrity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("convention", ALL_CONVENTIONS, ids=lambda c: c.name)
def test_issuer_code_is_a_known_issuer(convention):
    assert convention.issuer_code in ISSUERS


@pytest.mark.parametrize("convention", ALL_CONVENTIONS, ids=lambda c: c.name)
def test_issuer_has_repo_conventions_configured(convention):
    """A contract cannot price without its issuer's repo day count."""
    day_count, settlement_days = convention.issuer().repo_conventions(
        convention.repo_market
    )
    assert day_count is not None
    assert settlement_days >= 0
    # Reached through the convention, repo data still resolves.
    assert convention.repo_day_count().name() == day_count.name()


@pytest.mark.parametrize("convention", ALL_CONVENTIONS, ids=lambda c: c.name)
def test_convention_is_json_serialisable(convention):
    """``ql.DayCounter`` must never leak into the dataclass."""
    payload = json.loads(convention.as_json())
    assert payload["name"] == convention.name
    assert payload["repo_day_count"] == convention.repo_day_count().name()


@pytest.mark.parametrize("convention", ALL_CONVENTIONS, ids=lambda c: c.name)
def test_convention_is_hashable(convention):
    assert convention in {convention}


def test_registry_keys_match_canonical_names():
    for name, convention in BOND_FUTURE_CONVENTIONS.items():
        assert name == convention.name
        assert name == name.upper()


def test_expected_contracts_are_present():
    expected = {
        # CME
        "ZT",
        "Z3N",
        "ZF",
        "ZN",
        "TN",
        "TWE",
        "ZB",
        "UB",
        # Eurex
        "FGBS",
        "FGBM",
        "FGBL",
        "FGBX",
        "FBTS",
        "FBTM",
        "FBTP",
        "FOAM",
        "FOAT",
        "FBON",
        "CONF",
        "FBEU",
        # Osaka, international and domestic repo legs
        "FJG5",
        "FJGB",
        "FJG2",
        "FJG5_DOM",
        "FJGB_DOM",
        "FJG2_DOM",
        # ICE gilts
        "G",
        "H",
        "R",
        "U",
    }
    assert expected <= set(BOND_FUTURE_CONVENTIONS)


# --------------------------------------------------------------------------- #
# Per-exchange conventions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("name", "exchange", "issuer", "coupon", "cf_decimals", "method"),
    [
        ("ZN", "CME", "USA", 6.0, 4, ConversionFactorMethod.CME),
        ("FGBL", "EUREX", "DEU", 6.0, 6, ConversionFactorMethod.EUREX),
        ("FBTP", "EUREX", "ITA", 6.0, 6, ConversionFactorMethod.EUREX),
        ("FOAT", "EUREX", "FRA", 6.0, 6, ConversionFactorMethod.EUREX),
        ("FBON", "EUREX", "ESP", 6.0, 6, ConversionFactorMethod.EUREX),
        ("CONF", "EUREX", "CHE", 6.0, 6, ConversionFactorMethod.EUREX),
        ("FBEU", "EUREX", "EU", 6.0, 6, ConversionFactorMethod.EUREX),
        ("FJGB", "OSE", "JPN", 6.0, 6, ConversionFactorMethod.JGB),
        ("FJG5", "OSE", "JPN", 3.0, 6, ConversionFactorMethod.JGB),
        ("R", "ICE", "GBR", 4.0, 7, ConversionFactorMethod.ICE),
    ],
)
def test_contract_terms(name, exchange, issuer, coupon, cf_decimals, method):
    convention = BOND_FUTURE_CONVENTIONS[name]
    assert convention.exchange == exchange
    assert convention.issuer_code == issuer
    assert convention.notional_coupon == coupon
    assert convention.cf_decimals == cf_decimals
    assert convention.conversion_factor_method is method


def test_buxl_is_the_eurex_four_percent_outlier():
    assert BOND_FUTURE_CONVENTIONS["FGBX"].notional_coupon == 4.0
    others = [
        c.notional_coupon
        for c in ALL_CONVENTIONS
        if c.exchange == "EUREX" and c.name != "FGBX"
    ]
    assert set(others) == {6.0}


def test_cme_two_and_three_year_notes_are_double_sized():
    assert BOND_FUTURE_CONVENTIONS["ZT"].contract_size == 200_000.0
    assert BOND_FUTURE_CONVENTIONS["Z3N"].contract_size == 200_000.0
    assert BOND_FUTURE_CONVENTIONS["ZN"].contract_size == 100_000.0


def test_conversion_factor_reference_day_is_split_from_delivery_start():
    """CME and ICE compute factors to the *unadjusted* 1st of the month."""
    for name in ("ZN", "R"):
        convention = BOND_FUTURE_CONVENTIONS[name]
        assert str(convention.reference_day) == "C1 0BD"
        assert str(convention.delivery_start) == "C1 1BD"
        assert str(convention.delivery_end) == "L0 -1BD"


def test_eurex_delivers_on_the_tenth():
    convention = BOND_FUTURE_CONVENTIONS["FGBL"]
    assert str(convention.reference_day) == "C10 1BD"
    assert convention.delivery_start == convention.reference_day
    assert convention.delivery_end == convention.reference_day


def test_osaka_delivers_on_the_twentieth():
    assert str(BOND_FUTURE_CONVENTIONS["FJGB"].reference_day) == "C20 1BD"


# --------------------------------------------------------------------------- #
# JGB domestic vs international repo legs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stem", ["FJG5", "FJGB", "FJG2"])
def test_domestic_jgb_variant_differs_only_by_repo_market(stem):
    international = BOND_FUTURE_CONVENTIONS[stem]
    domestic = BOND_FUTURE_CONVENTIONS[f"{stem}_DOM"]

    assert international.repo_market is RepoMarket.INTERNATIONAL
    assert domestic.repo_market is RepoMarket.DOMESTIC
    assert international.repo_day_count().name() == "Actual/360"
    assert domestic.repo_day_count().name() == "Actual/365 (Fixed)"

    # Everything else about the contract is identical.
    assert international.issuer_code == domestic.issuer_code
    assert international.notional_coupon == domestic.notional_coupon
    assert international.contract_size == domestic.contract_size
    assert international.restrictions == domestic.restrictions


# --------------------------------------------------------------------------- #
# Code resolution and collisions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("FGBM", "FGBM"),
        ("OE", "FGBM"),
        ("FBTP", "FBTP"),
        ("IK", "FBTP"),
        ("FOAT", "FOAT"),
        ("FM", "FOAT"),
        ("FOAM", "FOAM"),
        ("FOA", "FOAM"),
        ("ZN", "ZN"),
        ("TY", "ZN"),
        ("ZT", "ZT"),
        ("TU", "ZT"),
        ("Z3N", "Z3N"),
        ("3Y", "Z3N"),
        ("3YR", "Z3N"),
        ("FJGB", "FJGB"),
        ("JB", "FJGB"),
        ("10JGB", "FJGB"),
        ("FJG2", "FJG2"),
        ("JL", "FJG2"),
        ("FJG5", "FJG5"),
        ("JB5", "FJG5"),
        ("CONF", "CONF"),
        ("SW", "CONF"),
        ("FBEU", "FBEU"),
        ("EUB", "FBEU"),
        ("H", "H"),
        ("WX", "H"),
        # Lookup is case- and whitespace-insensitive.
        ("  ik  ", "FBTP"),
        ("fgbl", "FGBL"),
    ],
)
def test_codes_resolve(code, expected):
    assert resolve_bond_future_convention(code).name == expected


def test_canonical_code_wins_over_a_shared_bloomberg_root():
    """'UB' is CME's canonical code, so it never silently means Buxl."""
    assert resolve_bond_future_convention("UB").name == "UB"
    assert resolve_bond_future_convention("UB").exchange == "CME"
    # The other two claimants are reachable by canonical code...
    assert resolve_bond_future_convention("FGBX").name == "FGBX"
    # ...or by qualifying the synonym.
    assert resolve_bond_future_convention("EUREX:UB").name == "FGBX"
    assert resolve_bond_future_convention("UB", exchange="EUREX").name == "FGBX"
    assert resolve_bond_future_convention("UB", exchange="ICE").name == "U"
    assert resolve_bond_future_convention("UB", issuer="DEU").name == "FGBX"


def test_short_gilt_owns_the_bare_g_code():
    """'G' is the Short Gilt screen symbol and also the Long Gilt BBG root."""
    short = resolve_bond_future_convention("G")
    assert short.name == "G"
    assert short.restrictions.remaining_maturity == MaturityRange(
        months(1, 6), months(3, 3)
    )
    # The Long Gilt needs its own canonical code.
    assert resolve_bond_future_convention("R").name == "R"


def test_ambiguous_synonym_raises_and_names_candidates(monkeypatch):
    """A synonym claimed by several non-canonical contracts must not guess."""
    monkeypatch.setitem(bond_futures._SYNONYM_INDEX, "XYZ", ("FGBL", "FBTP"))
    with pytest.raises(AmbiguousBondFutureError) as excinfo:
        resolve_bond_future_convention("XYZ")
    assert excinfo.value.candidates == ("FGBL", "FBTP")
    assert "FGBL" in str(excinfo.value) and "FBTP" in str(excinfo.value)


@pytest.mark.parametrize("code", ["", "   ", "ZZZZ", "NOTACODE"])
def test_unknown_code_raises(code):
    with pytest.raises(BondFutureError):
        resolve_bond_future_convention(code)


def test_exchange_filter_excluding_every_candidate_raises():
    with pytest.raises(BondFutureError):
        resolve_bond_future_convention("FGBL", exchange="CME")


# --------------------------------------------------------------------------- #
# Basket restrictions
# --------------------------------------------------------------------------- #
def test_maturity_range_bounds_are_inclusive():
    window = MaturityRange(months(8, 6), months(10, 6))
    assert window.contains(months(8, 6))
    assert window.contains(months(10, 6))
    assert not window.contains(months(8, 5))
    assert not window.contains(months(10, 7))


def test_unrestricted_maturity_range_admits_everything():
    window = MaturityRange()
    assert window.is_unrestricted
    assert window.contains(0) and window.contains(months(50))


def test_cme_short_notes_cap_the_original_term():
    for name in ("ZT", "Z3N", "ZF"):
        original = BOND_FUTURE_CONVENTIONS[name].restrictions.original_term
        assert original is not None
        assert original.max_months == months(5, 3)
