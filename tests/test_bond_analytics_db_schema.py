"""Tests for bond_analytics database keys and indexes."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from cqfi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    EXPECTED_INDEX_NAMES,
    FOREIGN_KEYS,
    TABLE_PRIMARY_KEYS,
    create_schema,
    list_index_names,
    load_semantics,
    open_sink,
)
from cqfi.bond_future_output import BondFutureOutput
from cqfi.instruments import Bond
from cqfi.numeric_term_structure import NumericTermStructure


@pytest.fixture
def semantics() -> dict:
    return load_semantics(DEFAULT_SEMANTICS_PATH)


@pytest.fixture
def sqlite_db(tmp_path: Path, semantics: dict):
    db_path = tmp_path / "bond_analytics.db"
    with open_sink(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        yield db


def test_expected_indexes_exist(sqlite_db) -> None:
    assert EXPECTED_INDEX_NAMES <= list_index_names(sqlite_db)


def test_primary_keys(sqlite_db) -> None:
    for table_name, pk_cols in TABLE_PRIMARY_KEYS.items():
        rows = sqlite_db.execute(f'PRAGMA table_info("{table_name}")')
        pk_flags = [row["name"] for row in rows if row["pk"]]
        assert pk_flags == list(pk_cols)


def test_foreign_keys(sqlite_db) -> None:
    # Test FK constraints for all child tables that have them
    for child_table, child_col, parent_table, parent_col, _on_delete in FOREIGN_KEYS:
        rows = sqlite_db.execute(f'PRAGMA foreign_key_list("{child_table}")')
        fk_map = {(row["from"], row["table"], row["to"]) for row in rows}
        assert (child_col, parent_table, parent_col) in fk_map


def _explain_plan(sqlite_db, sql: str) -> str:
    rows = sqlite_db.execute(f"EXPLAIN QUERY PLAN {sql}")
    return "\n".join(str(row) for row in rows)


def test_explain_uses_user_friendly_id_index(sqlite_db) -> None:
    sqlite_db.execute(
        """
        INSERT INTO bond_universe (
            bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
            first_coupon_date, accrual_start_date, closest_tenor_pillar,
            issue_amount, currency
        ) VALUES (
            'US0001', 'usa10y001', 'USA', 2.5, '2030-01-15', '2020-01-15',
            NULL, NULL, '10Y', 1000.0, 'USD'
        )
        """
    )
    plan = _explain_plan(
        sqlite_db,
        "SELECT bond_id FROM bond_universe WHERE user_friendly_id = 'usa10y001'",
    )
    assert "idx_bu_user_friendly_id" in plan


def test_explain_uses_bond_trade_date_index(sqlite_db) -> None:
    sqlite_db.execute(
        """
        INSERT INTO bond_universe (
            bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
            first_coupon_date, accrual_start_date, closest_tenor_pillar,
            issue_amount, currency
        ) VALUES (
            'US0001', 'usa10y001', 'USA', 2.5, '2030-01-15', '2020-01-15',
            NULL, NULL, '10Y', 1000.0, 'USD'
        )
        """
    )
    sqlite_db.execute(
        """
        INSERT INTO bond_analytics (
            analytic_id, bond_id, created_at, trade_date, settlement_date,
            curve_used
        ) VALUES (
            'A1', 'US0001', '2025-01-01T00:00:00Z', '2025-01-02', '2025-01-03', 1
        )
        """
    )
    plan = _explain_plan(
        sqlite_db,
        """
        SELECT analytic_id FROM bond_analytics
        WHERE bond_id = 'US0001'
          AND trade_date >= '2025-01-01'
          AND trade_date <= '2025-01-31'
        """,
    )
    assert "idx_ba_bond_trade" in plan or "idx_ba_bond_id" in plan


def test_bond_future_outputs_has_per_bond_repo_columns(sqlite_db) -> None:
    """Per-bond repo overrides must be representable without touching other columns."""
    rows = sqlite_db.execute('PRAGMA table_info("bond_future_outputs")')
    columns = {row["name"] for row in rows}
    assert {"repo_rate", "repo_term_structure_json"} <= columns
    # Every pre-existing BondFutureOutput field must still be there, untouched.
    assert {
        "conversion_factor",
        "clean_price",
        "accrued_interest",
        "forward_clean_price",
        "implied_repo_rate",
        "gross_basis",
        "net_basis",
        "delta",
        "gamma",
        "implied_fair_futures_price",
        "index",
    } <= columns


def test_bond_future_output_row_round_trips_with_per_bond_repo_override(
    sqlite_db,
) -> None:
    """A per-bond repo override must persist and reconstruct without breaking

    the ability to rebuild a `BondFutureOutput` from the row.
    """
    sqlite_db.execute(
        """
        INSERT INTO bond_universe (
            bond_id, user_friendly_id, issuer, coupon, maturity, issue_date,
            currency, is_green
        ) VALUES ('IT0001', 'itasep035', 'ITA', 4.0, '2035-09-10', '2024-09-10', 'EUR', 0)
        """
    )
    sqlite_db.execute(
        """
        INSERT INTO bond_future_conventions (convention_id, exchange, issuer)
        VALUES ('FBTP', 'EUREX', 'ITA')
        """
    )
    trade_date = "2026-05-15"
    sqlite_db.execute(
        f"""
        INSERT INTO bond_future_basket_outputs (
            basket_output_id, convention_id, delivery_month, trade_date,
            settlement_date, delivery_date, futures_price,
            futures_price_is_implied, repo_rate, bond_count
        ) VALUES (
            'FBTPU6_2026-05-15', 'FBTP', 'U2026', '{trade_date}',
            '2026-05-18', '2026-09-10', 120.0, 1, 3.0, 2
        )
        """
    )

    # One bond finances at the basket-wide default (no override)...
    sqlite_db.execute(
        """
        INSERT INTO bond_future_outputs (
            future_output_id, basket_output_id, bond_id, "index",
            conversion_factor, clean_price, accrued_interest, repo_rate,
            repo_term_structure_json, forward_clean_price, implied_repo_rate,
            gross_basis, net_basis, delta, gamma, implied_fair_futures_price
        ) VALUES (
            'OUT1', 'FBTPU6_2026-05-15', 'IT0001', 0,
            1.05, 101.0, 0.5, 3.0,
            NULL, 100.8, 3.0,
            0.2, 0.1, -0.05, 0.001, 96.0
        )
        """
    )
    # ...the other carries at its own 1% repo curve instead.
    override_json = json.dumps({"3m": 1.0, "1y": 1.0})
    sqlite_db.execute(
        """
        INSERT INTO bond_future_outputs (
            future_output_id, basket_output_id, bond_id, "index",
            conversion_factor, clean_price, accrued_interest, repo_rate,
            repo_term_structure_json, forward_clean_price, implied_repo_rate,
            gross_basis, net_basis, delta, gamma, implied_fair_futures_price
        ) VALUES (
            'OUT2', 'FBTPU6_2026-05-15', 'IT0001', 1,
            1.05, 101.0, 0.5, 1.0,
            ?, 100.5, 1.0,
            0.5, 0.4, -0.05, 0.001, 95.7
        )
        """,
        (override_json,),
    )

    rows = {
        row["future_output_id"]: row
        for row in sqlite_db.execute(
            'SELECT * FROM bond_future_outputs ORDER BY "index"'
        )
    }

    basket_row = next(
        iter(sqlite_db.execute("SELECT * FROM bond_future_basket_outputs"))
    )
    bond_row = next(iter(sqlite_db.execute("SELECT * FROM bond_universe")))
    bond = Bond.from_row(bond_row)

    # The plain row: no override, financed at the basket-wide default rate.
    plain = rows["OUT1"]
    assert plain["repo_term_structure_json"] is None
    assert plain["repo_rate"] == pytest.approx(basket_row["repo_rate"])
    plain_output = BondFutureOutput(
        bond=bond,
        conversion_factor=plain["conversion_factor"],
        clean_price=plain["clean_price"],
        accrued_interest=plain["accrued_interest"],
        forward_clean_price=plain["forward_clean_price"],
        implied_repo_rate=plain["implied_repo_rate"],
        gross_basis=plain["gross_basis"],
        net_basis=plain["net_basis"],
        index=plain["index"],
        delta=plain["delta"],
        gamma=plain["gamma"],
        implied_fair_futures_price=plain["implied_fair_futures_price"],
    )
    assert plain_output.bond == bond
    assert plain_output.forward_clean_price == pytest.approx(100.8)

    # The overridden row: reconstruct the NumericTermStructure the bond
    # actually financed at, and confirm it explains the stored repo_rate.
    overridden = rows["OUT2"]
    assert overridden["repo_term_structure_json"] is not None
    restored = NumericTermStructure(
        json.loads(overridden["repo_term_structure_json"]),
        as_of=date.fromisoformat(basket_row["trade_date"]),
    )
    delivery = date.fromisoformat(basket_row["delivery_date"])
    assert restored.rate_for(delivery) * 100.0 == pytest.approx(
        overridden["repo_rate"]
    )
    # ...which differs from the basket-wide default the unoverridden bond used.
    assert overridden["repo_rate"] != pytest.approx(basket_row["repo_rate"])

    overridden_output = BondFutureOutput(
        bond=bond,
        conversion_factor=overridden["conversion_factor"],
        clean_price=overridden["clean_price"],
        accrued_interest=overridden["accrued_interest"],
        forward_clean_price=overridden["forward_clean_price"],
        implied_repo_rate=overridden["implied_repo_rate"],
        gross_basis=overridden["gross_basis"],
        net_basis=overridden["net_basis"],
        index=overridden["index"],
        delta=overridden["delta"],
        gamma=overridden["gamma"],
        implied_fair_futures_price=overridden["implied_fair_futures_price"],
    )
    assert overridden_output.forward_clean_price == pytest.approx(100.5)


def test_explain_uses_cmt_issuer_tenor_trade_index(sqlite_db) -> None:
    sqlite_db.execute(
        """
        INSERT INTO cmt_analytics (
            cmt_analytic_id, issuer, tenor_label, created_at, trade_date,
            settlement_date, maturity_date, curve_used
        ) VALUES (
            'C1', 'USA', '10Y', '2025-01-01T00:00:00Z', '2025-01-02',
            '2025-01-03', '2035-01-02', 1
        )
        """
    )
    plan = _explain_plan(
        sqlite_db,
        """
        SELECT cmt_analytic_id FROM cmt_analytics
        WHERE issuer = 'USA'
          AND tenor_label = '10Y'
          AND trade_date >= '2025-01-01'
          AND trade_date <= '2025-01-31'
        """,
    )
    assert "idx_ca_issuer_tenor_trade" in plan
