"""Tests for FutureBatchEngine: process-pool orchestration, single-writer persistence, stop."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import Future
from datetime import date
from pathlib import Path

from cqfi.batch.future_engine import FutureBatchEngine
from cqfi.batch.future_planner import FutureBatchPlan, FutureSeriesPlan, FutureWorkItem
from cqfi.batch.models import (
    BatchDone,
    BatchStarted,
    CellStatus,
    FutureCellDone,
    FutureCellPayload,
    FutureCellsStarted,
)
from cqfi.bond_future_input import BondFutureInput
from cqfi.bond_future_output import BondFutureBasketOutput, BondFutureOutput
from cqfi.bond_futures import BOND_FUTURE_CONVENTIONS, BondFuture
from cqfi.cache.registry import CacheRegistry, reset_cache_registry
from cqfi.config import AppSettings
from cqfi.delivery_basket import BasketMember, DeliveryBasket
from cqfi.instruments import Bond

_FBTP_U0 = BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2020)  # delivers 2020-09-10


def _basket(future: BondFuture, bond_id: str) -> DeliveryBasket:
    bond = Bond(
        issuer=future.convention.issuer_code,
        maturity=date(2030, 9, 10),
        bond_id=bond_id,
        coupon=3.5,
    )
    # Constructed directly (not via .add()) to skip restriction checks —
    # this exercises the engine, not DeliveryBasket's admission rules.
    return DeliveryBasket(bond_future=future, members=(BasketMember(bond),))


def _fake_output(bond: Bond) -> BondFutureOutput:
    return BondFutureOutput(
        bond=bond, conversion_factor=1.0, clean_price=99.0, accrued_interest=0.1,
        forward_clean_price=99.5, implied_repo_rate=1.0, gross_basis=0.1, net_basis=0.05,
        index=0, delta=0.01, gamma=0.001, implied_fair_futures_price=99.6,
    )


def _noop_initializer(config_path: str) -> None:
    """Stand-in ProcessPoolExecutor initializer: skips real settings/QuantLib setup."""


def _fake_compute_future_batch(
    issuer_code: str, trade_date: date, baskets: list[DeliveryBasket], curve_label: str
) -> list[FutureCellPayload]:
    """Stand-in compute_fn: succeeds for every basket except contracts named 'FAIL'."""
    results = []
    for basket in baskets:
        label = str(basket.bond_future)
        if "fail" in label.lower():
            results.append(FutureCellPayload(contract=label, success=False, error="synthetic failure"))
            continue
        request = BondFutureInput.from_basket(basket, trade_date, curve_label=curve_label)
        result = BondFutureBasketOutput(
            bond_future=basket.bond_future,
            trade_date=trade_date,
            settlement_date=trade_date,
            delivery_date=basket.bond_future.delivery_end_date(),
            futures_price=100.0,
            futures_price_is_implied=True,
            repo_rate=1.0,
            outputs=tuple(_fake_output(b) for b in basket.bonds()),
        )
        results.append(FutureCellPayload(contract=label, success=True, request=request, result=result))
    return results


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


# ── Fast, no-subprocess tests: FutureBatchEngine._handle_result in isolation ─


def test_handle_result_persists_success(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = CacheRegistry(settings.bond_analytics_db_path, settings.quant_cache_semantics_path)
    try:
        basket = _basket(_FBTP_U0, "IT0001")
        item = FutureWorkItem(issuer="ITA", trade_date=date(2020, 6, 15), baskets=(basket,))
        payload = _fake_compute_future_batch("ITA", item.trade_date, [basket], "BOND_ZERO")[0]

        future: Future = Future()
        future.set_result([payload])

        events = []
        completed = FutureBatchEngine._handle_result(
            future, item, registry, "BOND_ZERO", 0, 1, events.append
        )

        assert completed == 1
        assert events[0].status == CellStatus.SUCCESS
        assert events[0].detail is not None and "conversion_factor" in events[0].detail
        assert _count_rows(settings.bond_analytics_db_path, "bond_future_basket_outputs") == 1
        assert _count_rows(settings.bond_analytics_db_path, "bond_future_outputs") == 1
    finally:
        registry.close()


def test_handle_result_reports_future_exception_as_failed(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = CacheRegistry(settings.bond_analytics_db_path, settings.quant_cache_semantics_path)
    try:
        basket = _basket(_FBTP_U0, "BROKEN1")
        item = FutureWorkItem(issuer="ITA", trade_date=date(2020, 6, 15), baskets=(basket,))

        future: Future = Future()
        future.set_exception(RuntimeError("worker crashed"))

        events = []
        completed = FutureBatchEngine._handle_result(
            future, item, registry, "BOND_ZERO", 0, 1, events.append
        )

        assert completed == 1
        assert events[0].status == CellStatus.FAILED
        assert "worker crashed" in (events[0].detail or "")
    finally:
        registry.close()


def test_handle_result_also_cache_writes_to_quant_cache_db(tmp_path: Path, monkeypatch):
    import cqfi.config as cqfi_config

    settings = _settings(tmp_path)
    monkeypatch.setattr(cqfi_config, "_settings", settings)
    reset_cache_registry()
    registry = CacheRegistry(settings.bond_analytics_db_path, settings.quant_cache_semantics_path)
    try:
        basket = _basket(_FBTP_U0, "IT0001")
        item = FutureWorkItem(issuer="ITA", trade_date=date(2020, 6, 15), baskets=(basket,))
        payload = _fake_compute_future_batch("ITA", item.trade_date, [basket], "BOND_ZERO")[0]

        future: Future = Future()
        future.set_result([payload])

        completed = FutureBatchEngine._handle_result(
            future, item, registry, "BOND_ZERO", 0, 1, lambda e: None, also_cache=True
        )

        assert completed == 1
        assert _count_rows(settings.bond_analytics_db_path, "bond_future_basket_outputs") == 1
        assert _count_rows(settings.quant_cache_db_path, "bond_future_basket_outputs") == 1
    finally:
        registry.close()
        reset_cache_registry()


# ── Full-orchestration tests: real ProcessPoolExecutor + a cheap stand-in ───


def test_run_persists_success(tmp_path: Path):
    settings = _settings(tmp_path)
    basket = _basket(_FBTP_U0, "IT0001")
    trade_date = date(2020, 6, 15)
    work_item = FutureWorkItem(issuer="ITA", trade_date=trade_date, baskets=(basket,))
    plan = FutureBatchPlan(
        series_plans=(
            FutureSeriesPlan(
                future_code="FBTP",
                contracts=(_FBTP_U0,),
                trade_dates=(trade_date,),
                work_items=(work_item,),
            ),
        )
    )

    events = []
    FutureBatchEngine().run(
        plan,
        settings,
        workers=1,
        curve_label="BOND_ZERO",
        on_event=events.append,
        compute_fn=_fake_compute_future_batch,
        worker_initializer=_noop_initializer,
    )

    assert isinstance(events[0], BatchStarted)
    assert events[0].total_cells == 1
    assert isinstance(events[1], FutureCellsStarted)

    cell_done = [e for e in events if isinstance(e, FutureCellDone)]
    assert len(cell_done) == 1
    assert cell_done[0].status == CellStatus.SUCCESS

    done = events[-1]
    assert isinstance(done, BatchDone)
    assert done.completed == 1 and done.total == 1 and done.cancelled is False

    assert _count_rows(settings.bond_analytics_db_path, "bond_future_basket_outputs") == 1
    assert not settings.quant_cache_db_path.exists()


def test_run_with_zero_cells_emits_started_and_done_only(tmp_path: Path):
    plan = FutureBatchPlan(series_plans=())
    events = []
    FutureBatchEngine().run(
        plan,
        _settings(tmp_path),
        workers=1,
        curve_label="BOND_ZERO",
        on_event=events.append,
    )
    assert [type(e) for e in events] == [BatchStarted, BatchDone]
    assert events[-1].total == 0


def test_run_stopped_before_dispatch_cancels_everything(tmp_path: Path):
    settings = _settings(tmp_path)
    basket = _basket(_FBTP_U0, "IT0001")
    trade_date = date(2020, 6, 15)
    work_item = FutureWorkItem(issuer="ITA", trade_date=trade_date, baskets=(basket,))
    plan = FutureBatchPlan(
        series_plans=(
            FutureSeriesPlan(
                future_code="FBTP",
                contracts=(_FBTP_U0,),
                trade_dates=(trade_date,),
                work_items=(work_item,),
            ),
        )
    )

    stop_event = threading.Event()
    stop_event.set()

    events = []
    FutureBatchEngine().run(
        plan,
        settings,
        workers=1,
        curve_label="BOND_ZERO",
        on_event=events.append,
        stop_event=stop_event,
        compute_fn=_fake_compute_future_batch,
        worker_initializer=_noop_initializer,
    )

    done = events[-1]
    assert isinstance(done, BatchDone)
    assert done.cancelled is True
