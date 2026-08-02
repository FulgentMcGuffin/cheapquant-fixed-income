"""Tests for delivery basket construction, restrictions and storage."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from cqfi.bond_futures import (
    BOND_FUTURE_CONVENTIONS,
    BasketRestrictions,
    BondFuture,
    MaturityRange,
    months,
)
from cqfi.bond_manager import BondManager
from cqfi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    create_schema,
    load_semantics,
    open_sink,
)
from cqfi.delivery_basket import (
    DeliveryBasket,
    DeliveryBasketError,
    DeliveryBasketManager,
    parse_dlv_command,
    resolve_basket,
)
from cqfi.instruments import Bond
from cqfi.numeric_term_structure import NumericTermStructure

TODAY = date(2026, 8, 2)

# FBTP (Italian 10Y) delivers 2026-09-10 and admits 8y6m..11y remaining.
FBTP_U6 = BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2026)

# Maturities chosen relative to the 2026-09-10 delivery date.
_ITA_BONDS = [
    # (bond_id, user_friendly_id, coupon, maturity, issue_date) — deliverable
    ("IT0001", "itamar035", 3.5, "2035-03-10", "2025-03-10"),  # 8y6m exactly
    ("IT0002", "itasep035", 4.0, "2035-09-10", "2024-09-10"),  # 9y
    ("IT0003", "itasep037", 4.5, "2037-09-10", "2025-09-10"),  # 11y exactly
    # Outside the window on either side.
    ("IT0004", "itafeb035", 3.0, "2035-02-10", "2024-02-10"),  # 8y5m — too short
    ("IT0005", "itaoct037", 5.0, "2037-10-10", "2025-10-10"),  # 11y1m — too long
]


@pytest.fixture
def bond_db(tmp_path: Path) -> Path:
    """A bond_universe holding Italian and German bonds."""
    db_path = tmp_path / "bond_analytics.db"
    semantics = load_semantics(DEFAULT_SEMANTICS_PATH)
    with open_sink(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        for bond_id, friendly, coupon, maturity, issue in _ITA_BONDS:
            db.execute(
                "INSERT INTO bond_universe (bond_id, user_friendly_id, issuer, "
                "coupon, maturity, issue_date, currency, is_green) VALUES "
                f"('{bond_id}', '{friendly}', 'ITA', {coupon}, '{maturity}', "
                f"'{issue}', 'EUR', 0)"
            )
        # A German bond of the right maturity, to prove issuer filtering.
        db.execute(
            "INSERT INTO bond_universe (bond_id, user_friendly_id, issuer, "
            "coupon, maturity, issue_date, currency, is_green) VALUES "
            "('DE0001', 'deusep035', 'DEU', 2.0, '2035-09-10', '2024-09-10', 'EUR', 0)"
        )
    return db_path


@pytest.fixture(autouse=True)
def _clear_singletons():
    BondManager.instance().clear()
    DeliveryBasketManager.instance().clear()
    yield
    BondManager.instance().clear()
    DeliveryBasketManager.instance().clear()


def _bond(
    maturity: str, *, issuer: str = "ITA", issue_date: str = "2024-01-01"
) -> Bond:
    return Bond(
        issuer=issuer,
        maturity=date.fromisoformat(maturity),
        bond_id=f"X{maturity}",
        coupon=3.0,
        issue_date=date.fromisoformat(issue_date),
    )


# --------------------------------------------------------------------------- #
# BondManager bulk queries
# --------------------------------------------------------------------------- #
def test_get_by_issuer_returns_only_that_issuer(bond_db: Path):
    bonds = BondManager.instance().get_by_issuer("ITA", db_path=bond_db)
    assert len(bonds) == len(_ITA_BONDS)
    assert {b.issuer for b in bonds} == {"ITA"}


def test_get_by_issuer_orders_by_maturity(bond_db: Path):
    bonds = BondManager.instance().get_by_issuer("ITA", db_path=bond_db)
    assert [b.maturity for b in bonds] == sorted(b.maturity for b in bonds)


def test_get_by_issuer_applies_the_maturity_window(bond_db: Path):
    bonds = BondManager.instance().get_by_issuer(
        "ITA",
        maturity_from=date(2035, 3, 10),
        maturity_to=date(2035, 9, 10),
        db_path=bond_db,
    )
    assert {b.user_friendly_id for b in bonds} == {"itamar035", "itasep035"}


def test_get_by_issuer_warms_the_single_bond_cache(bond_db: Path):
    manager = BondManager.instance()
    manager.get_by_issuer("ITA", db_path=bond_db)

    import cqfi.bond_manager as bond_manager_module

    original = bond_manager_module._fetch_bond_row
    bond_manager_module._fetch_bond_row = _fail
    try:
        assert manager.get("itasep035", db_path=bond_db) is not None
        assert manager.get("IT0002", db_path=bond_db) is not None
    finally:
        bond_manager_module._fetch_bond_row = original


def _fail(*_args, **_kwargs):
    raise AssertionError("database should not be queried for a cached bond")


def test_get_by_issuer_is_case_insensitive(bond_db: Path):
    assert BondManager.instance().get_by_issuer("ita", db_path=bond_db)


def test_latest_analytics_trade_date_is_none_without_analytics(bond_db: Path):
    assert BondManager.instance().latest_analytics_trade_date("ITA", bond_db) is None


# --------------------------------------------------------------------------- #
# Automatic basket construction
# --------------------------------------------------------------------------- #
def test_auto_basket_admits_only_bonds_inside_the_window(bond_db: Path):
    basket = DeliveryBasket.auto(FBTP_U6, name="mine", db_path=bond_db)
    assert {b.user_friendly_id for b in basket.bonds()} == {
        "itamar035",
        "itasep035",
        "itasep037",
    }
    assert basket.name == "mine"
    assert len(basket) == 3


def test_auto_basket_excludes_other_issuers(bond_db: Path):
    basket = DeliveryBasket.auto(FBTP_U6, db_path=bond_db)
    assert all(bond.issuer == "ITA" for bond in basket.bonds())


def test_auto_basket_membership_is_fixed_by_the_delivery_month(bond_db: Path):
    """A later delivery month shifts the window, so membership changes."""
    later = BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2027)
    assert {
        b.user_friendly_id for b in DeliveryBasket.auto(later, db_path=bond_db).bonds()
    } != {
        b.user_friendly_id
        for b in DeliveryBasket.auto(FBTP_U6, db_path=bond_db).bonds()
    }


def test_auto_basket_has_no_conversion_factor_overrides(bond_db: Path):
    basket = DeliveryBasket.auto(FBTP_U6, db_path=bond_db)
    assert all(m.conversion_factor_override is None for m in basket.members)


# --------------------------------------------------------------------------- #
# Explicit baskets and overrides
# --------------------------------------------------------------------------- #
def test_from_bond_ids_records_conversion_factors(bond_db: Path):
    basket = DeliveryBasket.from_bond_ids(
        FBTP_U6,
        [("itasep035", 1.0326), ("itasep037", None)],
        name="mine",
        db_path=bond_db,
    )
    assert len(basket) == 2
    lookup = {m.identifier: m.conversion_factor_override for m in basket.members}
    assert lookup == {"itasep035": 1.0326, "itasep037": None}

    bonds = basket.bonds()
    assert basket.override_for(bonds[0]) == 1.0326
    assert basket.override_for(bonds[1]) is None


def test_from_bond_ids_rejects_unknown_identifiers(bond_db: Path):
    with pytest.raises(DeliveryBasketError, match="Unknown bond identifier"):
        DeliveryBasket.from_bond_ids(FBTP_U6, [("nope", None)], db_path=bond_db)


def test_from_bond_ids_rejects_bonds_outside_the_window(bond_db: Path):
    with pytest.raises(DeliveryBasketError, match="not deliverable"):
        DeliveryBasket.from_bond_ids(FBTP_U6, [("itafeb035", None)], db_path=bond_db)


def test_from_bond_ids_rejects_the_wrong_issuer(bond_db: Path):
    with pytest.raises(DeliveryBasketError, match="issued by DEU"):
        DeliveryBasket.from_bond_ids(FBTP_U6, [("deusep035", None)], db_path=bond_db)


def test_duplicate_bonds_are_rejected():
    basket = DeliveryBasket(bond_future=FBTP_U6)
    bond = _bond("2035-09-10")
    basket.add(bond)
    with pytest.raises(DeliveryBasketError, match="already in the basket"):
        basket.add(bond)


# --------------------------------------------------------------------------- #
# Per-bond repo term structure overrides
# --------------------------------------------------------------------------- #
def test_members_have_no_repo_override_by_default():
    basket = DeliveryBasket(bond_future=FBTP_U6)
    bond = _bond("2035-09-10")
    basket.add(bond)
    assert basket.repo_term_structure_for(bond) is None


def test_add_records_a_per_bond_repo_term_structure():
    basket = DeliveryBasket(bond_future=FBTP_U6)
    bond = _bond("2035-09-10")
    repo = NumericTermStructure({"3m": 2.5}, date(2026, 5, 15))
    basket.add(bond, repo_term_structure=repo)
    assert basket.repo_term_structure_for(bond) is repo
    assert basket.members[0].repo_term_structure_override is repo


def test_set_repo_term_structure_updates_an_existing_member():
    basket = DeliveryBasket(bond_future=FBTP_U6)
    bond = _bond("2035-09-10")
    basket.add(bond)
    repo = NumericTermStructure({"3m": 2.5}, date(2026, 5, 15))

    basket.set_repo_term_structure(bond, repo)
    assert basket.repo_term_structure_for(bond) is repo

    basket.set_repo_term_structure(bond, None)
    assert basket.repo_term_structure_for(bond) is None


def test_set_repo_term_structure_rejects_an_unknown_bond():
    basket = DeliveryBasket(bond_future=FBTP_U6)
    repo = NumericTermStructure({"3m": 2.5}, date(2026, 5, 15))
    with pytest.raises(DeliveryBasketError, match="not in the basket"):
        basket.set_repo_term_structure(_bond("2035-09-10"), repo)


# --------------------------------------------------------------------------- #
# Restrictions
# --------------------------------------------------------------------------- #
def test_remaining_maturity_bounds_are_inclusive():
    restrictions = BasketRestrictions(
        remaining_maturity=MaturityRange(months(8, 6), months(11))
    )
    delivery = date(2026, 9, 10)
    assert restrictions.admits(_bond("2035-03-10"), delivery)[0]
    assert restrictions.admits(_bond("2037-09-10"), delivery)[0]
    assert not restrictions.admits(_bond("2035-03-09"), delivery)[0]
    assert not restrictions.admits(_bond("2037-09-11"), delivery)[0]


def test_original_term_restriction_uses_the_issue_date():
    restrictions = BasketRestrictions(
        original_term=MaturityRange(max_months=months(5, 3))
    )
    delivery = date(2026, 9, 10)
    # Issued 2024, matures 2029 -> 5 year original term, admissible.
    assert restrictions.admits(_bond("2029-01-01", issue_date="2024-01-01"), delivery)[
        0
    ]
    # Issued 2014, matures 2029 -> 15 years, rejected.
    admitted, reason = restrictions.admits(
        _bond("2029-01-01", issue_date="2014-01-01"), delivery
    )
    assert not admitted
    assert "original term" in reason


def test_original_term_restriction_needs_an_issue_date():
    restrictions = BasketRestrictions(original_term=MaturityRange(max_months=months(5)))
    bond = Bond(issuer="ITA", maturity=date(2029, 1, 1), bond_id="X", issue_date=None)
    admitted, reason = restrictions.admits(bond, date(2026, 9, 10))
    assert not admitted
    assert "issue_date is missing" in reason


def test_minimum_issue_amount_is_enforced_when_known():
    restrictions = BasketRestrictions(min_issue_amount=1_000.0)
    small = Bond(
        issuer="ITA", maturity=date(2035, 1, 1), bond_id="S", issue_amount=500.0
    )
    large = Bond(
        issuer="ITA", maturity=date(2035, 1, 1), bond_id="L", issue_amount=5_000.0
    )
    unknown = Bond(issuer="ITA", maturity=date(2035, 1, 1), bond_id="U")

    assert not restrictions.admits(small, date(2026, 9, 10))[0]
    assert restrictions.admits(large, date(2026, 9, 10))[0]
    # No data to check against, so the restriction is free.
    assert restrictions.admits(unknown, date(2026, 9, 10))[0]


def test_green_bonds_can_be_excluded():
    restrictions = BasketRestrictions(exclude_green=True)
    green = Bond(issuer="ITA", maturity=date(2035, 1, 1), bond_id="G", is_green=True)
    assert not restrictions.admits(green, date(2026, 9, 10))[0]


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
def test_to_polars_lists_the_deliverable_bonds(bond_db: Path):
    frame = DeliveryBasket.auto(FBTP_U6, db_path=bond_db).to_polars()
    assert frame.height == 3
    assert frame.columns == [
        "bond_id",
        "user_friendly_id",
        "issuer",
        "coupon",
        "maturity",
        "remaining_months",
        "conversion_factor",
    ]
    assert frame["remaining_months"].to_list() == [102, 108, 132]


def test_to_polars_is_empty_but_typed_for_an_empty_basket():
    frame = DeliveryBasket(bond_future=FBTP_U6).to_polars()
    assert frame.height == 0
    assert "conversion_factor" in frame.columns


def test_as_json_round_trips(bond_db: Path):
    basket = DeliveryBasket.from_bond_ids(
        FBTP_U6, [("itasep035", 1.0326)], name="mine", db_path=bond_db
    )
    payload = json.loads(basket.as_json())
    assert payload["name"] == "mine"
    assert payload["contract"] == "IKU6"
    assert payload["delivery_date"] == "2026-09-10"
    assert payload["bond_count"] == 1
    assert payload["bonds"][0]["conversion_factor"] == 1.0326
    assert payload["bonds"][0]["user_friendly_id"] == "itasep035"


# --------------------------------------------------------------------------- #
# Named basket storage
# --------------------------------------------------------------------------- #
def test_manager_is_a_singleton():
    assert DeliveryBasketManager.instance() is DeliveryBasketManager()


def test_store_and_retrieve_by_name(bond_db: Path):
    manager = DeliveryBasketManager.instance()
    basket = DeliveryBasket.auto(FBTP_U6, db_path=bond_db)
    manager.put("mine", basket)

    assert manager.names() == ["mine"]
    assert manager.get("mine").bonds() == basket.bonds()
    assert manager.get("mine").name == "mine"
    assert manager.get("absent") is None


def test_storing_replaces_an_existing_basket(bond_db: Path):
    manager = DeliveryBasketManager.instance()
    manager.put("mine", DeliveryBasket.auto(FBTP_U6, db_path=bond_db))
    manager.put("mine", DeliveryBasket(bond_future=FBTP_U6))
    assert len(manager.get("mine")) == 0


def test_empty_basket_name_is_rejected():
    with pytest.raises(DeliveryBasketError, match="must not be empty"):
        DeliveryBasketManager.instance().put("  ", DeliveryBasket(bond_future=FBTP_U6))


# --------------------------------------------------------------------------- #
# Target resolution
# --------------------------------------------------------------------------- #
def test_resolve_basket_prefers_a_stored_name(bond_db: Path):
    """A stored basket keeps its hard-coded conversion factors."""
    stored = DeliveryBasket.from_bond_ids(
        FBTP_U6, [("itasep035", 1.0326)], db_path=bond_db
    )
    DeliveryBasketManager.instance().put("mine", stored)

    resolved = resolve_basket("mine", today=TODAY, db_path=bond_db)
    assert len(resolved) == 1
    assert resolved.override_for(resolved.bonds()[0]) == 1.0326


def test_resolve_basket_falls_back_to_a_contract_code(bond_db: Path):
    resolved = resolve_basket("IKU6", today=TODAY, db_path=bond_db)
    assert str(resolved.bond_future) == "IKU6"
    assert len(resolved) == 3


def test_resolve_basket_rejects_an_unknown_target(bond_db: Path):
    with pytest.raises(DeliveryBasketError, match="neither a stored basket"):
        resolve_basket("nonsense", today=TODAY, db_path=bond_db)


# --------------------------------------------------------------------------- #
# End-to-end through the command parser
# --------------------------------------------------------------------------- #
def test_parse_then_build_auto_basket(bond_db: Path):
    result = parse_dlv_command("/dlv mybasket IK U6", today=TODAY)
    basket = result.build(today=TODAY, db_path=bond_db)
    assert basket.name == "mybasket"
    assert str(basket.bond_future) == "IKU6"
    assert len(basket) == 3


def test_parse_then_build_explicit_basket_with_overrides(bond_db: Path):
    result = parse_dlv_command(
        "/dlv mine IK itasep035|1.0326 itasep037|1.0291 2026-09", today=TODAY
    )
    basket = result.build(today=TODAY, db_path=bond_db)
    assert [m.conversion_factor_override for m in basket.members] == [1.0326, 1.0291]


def test_help_result_cannot_be_built():
    with pytest.raises(DeliveryBasketError, match="cannot build"):
        parse_dlv_command("/dlv", today=TODAY).build(today=TODAY)
