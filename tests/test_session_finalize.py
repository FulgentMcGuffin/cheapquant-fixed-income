"""Tests for session-end quant cache finalize / merge behaviour."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cheapquant_fi.cache.registry import CacheRegistry, reset_cache_registry
from cheapquant_fi.cache.session_finalize import finalize_quant_cache_session
from cheapquant_fi.config import get_runtime_settings, load_runtime_settings


def _copy_semantics(name: str, tmp_path: Path) -> Path:
    src = Path("semantics") / name
    dst = tmp_path / name
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _insert_bond_row(registry: CacheRegistry, analytic_id: str, bond_id: str) -> None:
    registry.upsert_row(
        "bond_analytics",
        {
            "analytic_id": analytic_id,
            "bond_id": bond_id,
            "created_at": "2024-01-15 12:00:00.000",
            "trade_date": "2024-01-15",
            "settlement_date": "2024-01-17",
            "curve_used": 1,
            "curve_settings": "DEU-BOND_ZERO",
            "clean_price": 99.5,
        },
    )


def _insert_cmt_row(registry: CacheRegistry, cmt_analytic_id: str) -> None:
    registry.upsert_row(
        "cmt_analytics",
        {
            "cmt_analytic_id": cmt_analytic_id,
            "issuer": "DEU",
            "tenor_label": "10Y",
            "created_at": "2024-01-15 12:00:00.000",
            "trade_date": "2024-01-15",
            "settlement_date": "2024-01-17",
            "coupon": 3.5,
            "is_fixed_coupon": 1,
            "maturity_date": "2034-01-15",
            "curve_used": 1,
            "curve_settings": "DEU-BOND_ZERO",
            "clean_price": 100.0,
        },
    )


def _count_rows(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


@pytest.fixture
def finalize_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_cache_registry()
    quant_cache_db = tmp_path / "quant_cache.sqlite"
    bond_analytics_db = tmp_path / "bond_analytics.sqlite"
    quant_sem = _copy_semantics("quant_cache.yaml", tmp_path)
    bond_sem = _copy_semantics("bond_analytics.yaml", tmp_path)

    runtime_path = tmp_path / "runtime.json"
    monkeypatch.setenv("CQFI_RUNTIME_CONFIG", str(runtime_path))
    load_runtime_settings(runtime_path)

    settings = type(
        "S",
        (),
        {
            "quant_cache_db_path": quant_cache_db,
            "quant_cache_semantics_path": quant_sem,
            "bond_analytics_db_path": bond_analytics_db,
            "bond_analytics_semantics_path": bond_sem,
        },
    )()

    cache_reg = CacheRegistry(quant_cache_db, quant_sem)
    _insert_cmt_row(cache_reg, "cmt-001")
    _insert_bond_row(cache_reg, "ba-001", "US0001")
    cache_reg.close()
    reset_cache_registry()

    yield settings, quant_cache_db, bond_analytics_db
    reset_cache_registry()


def test_finalize_discards_when_save_cache_off(finalize_env):
    settings, quant_cache_db, bond_analytics_db = finalize_env
    runtime = get_runtime_settings()
    runtime.update(save_quant_cache_to_bond_analytics_after_session=False)

    finalize_quant_cache_session(settings)

    assert _count_rows(quant_cache_db, "bond_analytics") == 0
    assert _count_rows(quant_cache_db, "cmt_analytics") == 0
    assert not bond_analytics_db.exists() or _count_rows(
        bond_analytics_db, "bond_analytics"
    ) == 0


def test_finalize_merges_and_clears_when_save_cache_on(finalize_env):
    settings, quant_cache_db, bond_analytics_db = finalize_env
    runtime = get_runtime_settings()
    runtime.update(save_quant_cache_to_bond_analytics_after_session=True)

    finalize_quant_cache_session(settings)

    assert _count_rows(quant_cache_db, "bond_analytics") == 0
    assert _count_rows(quant_cache_db, "cmt_analytics") == 0
    assert _count_rows(bond_analytics_db, "bond_analytics") == 1
    assert _count_rows(bond_analytics_db, "cmt_analytics") == 1


def test_finalize_overwrites_existing_rows_by_id(finalize_env):
    settings, quant_cache_db, bond_analytics_db = finalize_env
    bond_reg = CacheRegistry(bond_analytics_db, settings.quant_cache_semantics_path)
    _insert_bond_row(bond_reg, "ba-001", "OLD_BOND")
    _insert_cmt_row(bond_reg, "cmt-001")
    bond_reg.close()
    reset_cache_registry()

    runtime = get_runtime_settings()
    runtime.update(save_quant_cache_to_bond_analytics_after_session=True)

    finalize_quant_cache_session(settings)

    with sqlite3.connect(bond_analytics_db) as conn:
        bond_id = conn.execute(
            'SELECT bond_id FROM bond_analytics WHERE analytic_id = "ba-001"'
        ).fetchone()[0]
        assert bond_id == "US0001"


def test_finalize_noop_when_quant_cache_db_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime_path = tmp_path / "runtime.json"
    monkeypatch.setenv("CQFI_RUNTIME_CONFIG", str(runtime_path))
    load_runtime_settings(runtime_path)
    get_runtime_settings().update(
        save_quant_cache_to_bond_analytics_after_session=True,
    )

    settings = type(
        "S",
        (),
        {
            "quant_cache_db_path": tmp_path / "missing.sqlite",
            "quant_cache_semantics_path": _copy_semantics("quant_cache.yaml", tmp_path),
            "bond_analytics_db_path": tmp_path / "bond.sqlite",
            "bond_analytics_semantics_path": _copy_semantics("bond_analytics.yaml", tmp_path),
        },
    )()

    finalize_quant_cache_session(settings)
