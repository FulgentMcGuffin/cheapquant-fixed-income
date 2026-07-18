"""Tests for bond_analytics database keys and indexes."""

from __future__ import annotations

from pathlib import Path

import pytest

from cheapquant_fi.data.create_bond_analytics_db import (
    DEFAULT_SEMANTICS_PATH,
    EXPECTED_INDEX_NAMES,
    FOREIGN_KEYS,
    TABLE_PRIMARY_KEYS,
    create_schema,
    list_index_names,
    load_semantics,
    open_sink,
)


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
    rows = sqlite_db.execute('PRAGMA foreign_key_list("bond_analytics")')
    fk_map = {(row["from"], row["table"], row["to"]) for row in rows}
    for child_table, child_col, parent_table, parent_col, _on_delete in FOREIGN_KEYS:
        assert child_table == "bond_analytics"
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
