"""Orchestrates a batch bond-future analytics run: process-pool compute, single-writer persist.

Structural sibling of ``engine.BatchEngine`` — see that module's docstring
and ``.mex/patterns/batch-analytics-parallel-writer.md`` for the constraints
that shaped both (QuantLib process-global state, DuckDB single-writer). Kept
as a separate class rather than unified via inheritance/generics: the work
item shape (bonds vs. contract baskets) and persistence call
(``persist_bond_compute`` vs. ``persist_bond_future_compute``) differ enough
that sharing would need extra indirection for little real savings.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait

from cqfi.batch.future_planner import FutureBatchPlan, FutureWorkItem
from cqfi.batch.future_worker import compute_future_batch
from cqfi.batch.models import (
    BatchDone,
    BatchStarted,
    CellStatus,
    FutureBatchEvent,
    FutureCellDone,
    FutureCellPayload,
    FutureCellsStarted,
    Stopping,
)
from cqfi.batch.pool import terminate_pool_workers
from cqfi.batch.worker import worker_init
from cqfi.cache.registry import CacheRegistry, get_cache_registry
from cqfi.config import AppSettings

FutureBatchEventCallback = Callable[[FutureBatchEvent], None]

_OWNER = "QuantLibBondFutureCalculator"
_METHOD = "compute_bond_future_analytics"


class FutureBatchEngine:
    """Runs a :class:`~cqfi.batch.future_planner.FutureBatchPlan`.

    Dispatches one (issuer, trade_date) batch per worker process — every
    contract still valid (not yet delivered) that day — and writes every
    successful result straight into ``bond_analytics_db`` from a single
    connection held here — never from a worker.
    """

    def run(
        self,
        plan: FutureBatchPlan,
        settings: AppSettings,
        *,
        workers: int,
        curve_label: str,
        on_event: FutureBatchEventCallback,
        stop_event: threading.Event | None = None,
        also_cache: bool = False,
        compute_fn: Callable[..., list[FutureCellPayload]] = compute_future_batch,
        worker_initializer: Callable[[str], None] = worker_init,
    ) -> None:
        """Run the plan to completion (or until *stop_event* is set).

        See ``BatchEngine.run`` for the meaning of ``also_cache``,
        ``compute_fn``/``worker_initializer`` and the stop semantics — this
        mirrors it exactly, just for bond-future work items.
        """
        total = plan.total_cells
        on_event(BatchStarted(total_cells=total))
        if total == 0:
            on_event(BatchDone(cancelled=False, completed=0, total=0))
            return

        registry = CacheRegistry(
            settings.bond_analytics_db_path, settings.quant_cache_semantics_path
        )
        self._ensure_conventions(registry, plan)
        work_items = [item for item in plan.work_items if item.baskets]
        completed = 0
        cancelled = False
        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=worker_initializer,
                initargs=(str(settings.config_path),),
            ) as executor:
                future_to_item: dict[Future, FutureWorkItem] = {}
                in_flight: set[Future] = set()
                next_idx = 0

                def _submit_available() -> None:
                    """Keep at most ``workers`` futures in flight.

                    See ``BatchEngine.run`` — same ProcessPoolExecutor cancel
                    limitation and Stop UX; feed work lazily and kill the
                    uncancellable in-flight tail on stop.
                    """
                    nonlocal next_idx
                    while (
                        next_idx < len(work_items)
                        and len(in_flight) < workers
                        and not (stop_event is not None and stop_event.is_set())
                    ):
                        item = work_items[next_idx]
                        next_idx += 1
                        future = executor.submit(
                            compute_fn,
                            item.issuer,
                            item.trade_date,
                            list(item.baskets),
                            curve_label,
                        )
                        future_to_item[future] = item
                        in_flight.add(future)
                        on_event(
                            FutureCellsStarted(
                                issuer=item.issuer,
                                trade_date=item.trade_date,
                                contracts=tuple(
                                    str(b.bond_future) for b in item.baskets
                                ),
                            )
                        )

                _submit_available()
                if stop_event is not None and stop_event.is_set() and not in_flight:
                    cancelled = True

                while in_flight:
                    if stop_event is not None and stop_event.is_set():
                        cancelled = True
                        next_idx = len(work_items)
                        still_running = {f for f in in_flight if not f.cancel()}
                        in_flight.clear()
                        tail_total = len(still_running)
                        if tail_total:
                            on_event(Stopping(remaining=tail_total, total=tail_total))
                            terminate_pool_workers(executor)
                            pending_tail = set(still_running)
                            while pending_tail:
                                done, pending_tail = wait(pending_tail, timeout=0.5)
                                for future in done:
                                    try:
                                        future.result()
                                    except Exception:
                                        pass
                                on_event(
                                    Stopping(
                                        remaining=len(pending_tail), total=tail_total
                                    )
                                )
                        break

                    done, in_flight = wait(
                        in_flight, timeout=0.2, return_when=FIRST_COMPLETED
                    )
                    for future in done:
                        completed = self._handle_result(
                            future,
                            future_to_item[future],
                            registry,
                            curve_label,
                            completed,
                            total,
                            on_event,
                            also_cache,
                        )
                    _submit_available()

                executor.shutdown(wait=True, cancel_futures=True)
        finally:
            registry.close()

        on_event(BatchDone(cancelled=cancelled, completed=completed, total=total))

    @staticmethod
    def _ensure_conventions(registry: CacheRegistry, plan: FutureBatchPlan) -> None:
        """Upsert every plan contract's convention row before any basket write.

        ``bond_future_basket_outputs.convention_id`` is a foreign key into
        ``bond_future_conventions``. That table is normally populated as a
        side effect of ``CacheRegistry`` connecting (see
        ``_materialize_bond_futures_conventions_table`` / ROUTER.md "Known
        issues" for the schema-drift reason that can leave it empty for a
        registry opened with ``quant_cache`` semantics) — this is a
        best-effort backstop so a basket write never fails on a missing
        convention row, using the same call ``session_finalize`` already
        relies on for the same reason.
        """
        seen: set[str] = set()
        for series in plan.series_plans:
            if series.future_code in seen:
                continue
            seen.add(series.future_code)
            try:
                registry.upsert_bond_future_convention(series.future_code)
            except Exception:
                pass

    @staticmethod
    def _handle_result(
        future: Future,
        item: FutureWorkItem,
        registry: CacheRegistry,
        curve_label: str,
        completed: int,
        total: int,
        on_event: FutureBatchEventCallback,
        also_cache: bool = False,
    ) -> int:
        """Resolve one finished (issuer, trade_date) future: persist + emit per-contract events."""
        try:
            payloads = future.result()
        except Exception as exc:
            payloads = [
                FutureCellPayload(contract=str(basket.bond_future), success=False, error=str(exc))
                for basket in item.baskets
            ]

        for payload in payloads:
            status = CellStatus.FAILED
            detail = payload.error
            if payload.success and not payload.result.outputs:
                detail = "Empty delivery basket; nothing to persist."
            elif payload.success:
                try:
                    db_rows = registry.persist_bond_future_compute(
                        owner=_OWNER,
                        method=_METHOD,
                        request=payload.request,
                        result=payload.result,
                    )
                    status = CellStatus.SUCCESS
                    warning: str | None = None
                    if also_cache:
                        try:
                            cache_registry = get_cache_registry()
                            try:
                                cache_registry.upsert_bond_future_convention(
                                    payload.result.bond_future.convention.name
                                )
                            except Exception:
                                pass
                            cache_registry.persist_bond_future_compute(
                                owner=_OWNER,
                                method=_METHOD,
                                request=payload.request,
                                result=payload.result,
                            )
                        except Exception as exc:
                            warning = f"not written to quant_cache_db: {exc}"
                    detail = json.dumps(
                        {"rows": db_rows, **({"_warning": warning} if warning else {})}
                    )
                except Exception as exc:
                    detail = f"Failed to write to bond_analytics_db: {exc}"
            completed += 1
            on_event(
                FutureCellDone(
                    issuer=item.issuer,
                    trade_date=item.trade_date,
                    contract=payload.contract,
                    status=status,
                    detail=detail,
                    completed=completed,
                    total=total,
                )
            )
        return completed
