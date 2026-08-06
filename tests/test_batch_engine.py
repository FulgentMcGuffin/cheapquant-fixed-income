"""Tests for BatchEngine: process-pool orchestration, single-writer persistence, stop."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import Future
from datetime import date
from pathlib import Path

from cqfi.analytics_input import BondAnalyticsInput
from cqfi.analytics_output import FixedIncomeAnalyticsOutput
from cqfi.batch.engine import BatchEngine
from cqfi.batch.models import (
    BatchDone,
    BatchStarted,
    BondCellPayload,
    CellDone,
    CellsStarted,
    CellStatus,
)
from cqfi.batch.planner import BatchPlan, IssuerPlan, WorkItem
from cqfi.cache.registry import CacheRegistry, reset_cache_registry
from cqfi.config import AppSettings
from cqfi.instruments import Bond

# ── Module-level stand-ins (must be top-level to be picklable across the
#    ProcessPoolExecutor process boundary) ──────────────────────────────────


def _noop_initializer(config_path: str) -> None:
    """Stand-in ProcessPoolExecutor initializer: skips real settings/QuantLib setup."""


def _fake_compute_batch(
    issuer_code: str, trade_date: date, bond_rows: list[dict], curve_label: str
) -> list[BondCellPayload]:
    """Stand-in compute_fn: succeeds for every bond except ids containing 'fail'."""
    results = []
    for row in bond_rows:
        bond = Bond.from_row(row)
        key = str(bond)
        if "fail" in key.lower():
            results.append(BondCellPayload(bond_key=key, success=False, error="synthetic failure"))
            continue
        request = BondAnalyticsInput(
            issuer=issuer_code,
            coupon=bond.coupon or 0.0,
            maturity_date=bond.maturity,
            settlement_date=trade_date,
            trade_date=trade_date,
            bond_id=bond.bond_id,
        )
        metrics = FixedIncomeAnalyticsOutput(clean_price=99.5, yield_to_maturity=1.23)
        results.append(
            BondCellPayload(bond_key=key, success=True, request=request, bond_metrics=metrics)
        )
    return results


def _bond(bond_id: str, maturity: date) -> Bond:
    return Bond(issuer="FRA", maturity=maturity, bond_id=bond_id, coupon=1.0)


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        config_path=tmp_path / "cqfi.yaml",
        ycs_db_path=tmp_path / "ycs.sqlite",
        ycs_semantics_dir=tmp_path,
        bond_analytics_db_path=tmp_path / "bond_analytics.sqlite",
        bond_analytics_semantics_dir=Path("semantics"),
        bond_analytics_semantics_path=Path("semantics/bond_analytics.yaml"),
        quant_cache_db_path=tmp_path / "quant_cache.sqlite",
        quant_cache_semantics_dir=Path("semantics"),
        quant_cache_semantics_path=Path("semantics/quant_cache.yaml"),
        sessions_dir=tmp_path / "sessions",
        mcp_datasets={},
    )


def _count_rows(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


# ── Fast, no-subprocess tests: BatchEngine._handle_result in isolation ──────


def test_handle_result_persists_success(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = CacheRegistry(settings.bond_analytics_db_path, settings.quant_cache_semantics_path)
    try:
        bond = _bond("OK1", date(2029, 1, 1))
        item = WorkItem(issuer="FRA", trade_date=date(2020, 1, 2), bonds=(bond,))
        payload = _fake_compute_batch("FRA", item.trade_date, [bond.as_dict()], "BOND_ZERO")[0]

        future: Future = Future()
        future.set_result([payload])

        events = []
        completed = BatchEngine._handle_result(
            future, item, registry, "BOND_ZERO", 0, 1, events.append
        )

        assert completed == 1
        assert isinstance(events[0], CellDone)
        assert events[0].status == CellStatus.SUCCESS
        assert events[0].detail is not None and "clean_price" in events[0].detail
        assert _count_rows(settings.bond_analytics_db_path, "bond_analytics") == 1
    finally:
        registry.close()


def test_handle_result_reports_future_exception_as_failed(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = CacheRegistry(settings.bond_analytics_db_path, settings.quant_cache_semantics_path)
    try:
        bond = _bond("BROKEN1", date(2029, 1, 1))
        item = WorkItem(issuer="FRA", trade_date=date(2020, 1, 2), bonds=(bond,))

        future: Future = Future()
        future.set_exception(RuntimeError("worker crashed"))

        events = []
        completed = BatchEngine._handle_result(
            future, item, registry, "BOND_ZERO", 0, 1, events.append
        )

        assert completed == 1
        assert events[0].status == CellStatus.FAILED
        assert "worker crashed" in (events[0].detail or "")
    finally:
        registry.close()


# ── Full-orchestration tests: real ProcessPoolExecutor + a cheap stand-in ───


def test_run_persists_success_and_failure(tmp_path: Path):
    settings = _settings(tmp_path)
    bonds = (_bond("OK1", date(2029, 1, 1)), _bond("FAIL1", date(2029, 1, 1)))
    trade_date = date(2020, 1, 2)
    work_item = WorkItem(issuer="FRA", trade_date=trade_date, bonds=bonds)
    plan = BatchPlan(
        issuer_plans=(
            IssuerPlan(
                issuer="FRA", trade_dates=(trade_date,), bonds=bonds, work_items=(work_item,)
            ),
        )
    )

    events = []
    BatchEngine().run(
        plan,
        settings,
        workers=1,
        curve_label="BOND_ZERO",
        on_event=events.append,
        compute_fn=_fake_compute_batch,
        worker_initializer=_noop_initializer,
    )

    assert isinstance(events[0], BatchStarted)
    assert events[0].total_cells == 2
    assert isinstance(events[1], CellsStarted)

    cell_done = [e for e in events if isinstance(e, CellDone)]
    assert {(e.bond_key, e.status) for e in cell_done} == {
        ("OK1", CellStatus.SUCCESS),
        ("FAIL1", CellStatus.FAILED),
    }

    done = events[-1]
    assert isinstance(done, BatchDone)
    assert done.completed == 2 and done.total == 2 and done.cancelled is False

    assert _count_rows(settings.bond_analytics_db_path, "bond_analytics") == 1
    assert not settings.quant_cache_db_path.exists()


def test_run_with_zero_cells_emits_started_and_done_only(tmp_path: Path):
    plan = BatchPlan(issuer_plans=())
    events = []
    BatchEngine().run(
        plan,
        _settings(tmp_path),
        workers=1,
        curve_label="BOND_ZERO",
        on_event=events.append,
    )
    assert [type(e) for e in events] == [BatchStarted, BatchDone]
    assert events[-1].total == 0
    assert not settings_db_created(tmp_path)


def settings_db_created(tmp_path: Path) -> bool:
    return (tmp_path / "bond_analytics.sqlite").exists()


def test_run_stopped_before_dispatch_cancels_everything(tmp_path: Path):
    settings = _settings(tmp_path)
    bonds = (_bond("OK1", date(2029, 1, 1)),)
    trade_date = date(2020, 1, 2)
    work_item = WorkItem(issuer="FRA", trade_date=trade_date, bonds=bonds)
    plan = BatchPlan(
        issuer_plans=(
            IssuerPlan(
                issuer="FRA", trade_dates=(trade_date,), bonds=bonds, work_items=(work_item,)
            ),
        )
    )

    stop_event = threading.Event()
    stop_event.set()  # already stopped before run() drains any futures

    events = []
    BatchEngine().run(
        plan,
        settings,
        workers=1,
        curve_label="BOND_ZERO",
        on_event=events.append,
        stop_event=stop_event,
        compute_fn=_fake_compute_batch,
        worker_initializer=_noop_initializer,
    )

    done = events[-1]
    assert isinstance(done, BatchDone)
    assert done.cancelled is True


# ── also_cache: dual write to quant_cache_db (used by the /batch command) ──


def test_handle_result_also_cache_writes_to_quant_cache_db(tmp_path: Path, monkeypatch):
    import cqfi.config as cqfi_config

    settings = _settings(tmp_path)
    monkeypatch.setattr(cqfi_config, "_settings", settings)
    reset_cache_registry()
    registry = CacheRegistry(settings.bond_analytics_db_path, settings.quant_cache_semantics_path)
    try:
        bond = _bond("OK1", date(2029, 1, 1))
        item = WorkItem(issuer="FRA", trade_date=date(2020, 1, 2), bonds=(bond,))
        payload = _fake_compute_batch("FRA", item.trade_date, [bond.as_dict()], "BOND_ZERO")[0]

        future: Future = Future()
        future.set_result([payload])

        events = []
        completed = BatchEngine._handle_result(
            future, item, registry, "BOND_ZERO", 0, 1, events.append, also_cache=True
        )

        assert completed == 1
        assert events[0].status == CellStatus.SUCCESS
        assert _count_rows(settings.bond_analytics_db_path, "bond_analytics") == 1
        assert _count_rows(settings.quant_cache_db_path, "bond_analytics") == 1
    finally:
        registry.close()
        reset_cache_registry()


def test_handle_result_also_cache_false_never_touches_quant_cache_db(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = CacheRegistry(settings.bond_analytics_db_path, settings.quant_cache_semantics_path)
    try:
        bond = _bond("OK1", date(2029, 1, 1))
        item = WorkItem(issuer="FRA", trade_date=date(2020, 1, 2), bonds=(bond,))
        payload = _fake_compute_batch("FRA", item.trade_date, [bond.as_dict()], "BOND_ZERO")[0]

        future: Future = Future()
        future.set_result([payload])

        BatchEngine._handle_result(
            future, item, registry, "BOND_ZERO", 0, 1, lambda e: None, also_cache=False
        )

        assert not settings.quant_cache_db_path.exists()
    finally:
        registry.close()


def test_run_also_cache_true_writes_both_databases(tmp_path: Path, monkeypatch):
    import cqfi.config as cqfi_config

    settings = _settings(tmp_path)
    monkeypatch.setattr(cqfi_config, "_settings", settings)
    reset_cache_registry()
    try:
        bonds = (_bond("OK1", date(2029, 1, 1)),)
        trade_date = date(2020, 1, 2)
        work_item = WorkItem(issuer="FRA", trade_date=trade_date, bonds=bonds)
        plan = BatchPlan(
            issuer_plans=(
                IssuerPlan(
                    issuer="FRA", trade_dates=(trade_date,), bonds=bonds, work_items=(work_item,)
                ),
            )
        )

        events = []
        BatchEngine().run(
            plan,
            settings,
            workers=1,
            curve_label="BOND_ZERO",
            on_event=events.append,
            also_cache=True,
            compute_fn=_fake_compute_batch,
            worker_initializer=_noop_initializer,
        )

        done = events[-1]
        assert isinstance(done, BatchDone)
        assert done.completed == 1 and done.cancelled is False
        assert _count_rows(settings.bond_analytics_db_path, "bond_analytics") == 1
        assert _count_rows(settings.quant_cache_db_path, "bond_analytics") == 1
    finally:
        reset_cache_registry()
