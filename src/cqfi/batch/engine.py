"""Orchestrates a batch bond-analytics run: process-pool compute, single-writer persist.

UI-agnostic: the CLI drives :meth:`BatchEngine.run` synchronously on the main
thread; the GUI drives it from a background ``QThread`` and turns each event
into a Qt signal. No compute or persistence logic is duplicated between the
two front ends.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait

from cqfi.batch.models import (
    BatchDone,
    BatchEvent,
    BatchStarted,
    BondCellPayload,
    CellDone,
    CellsStarted,
    CellStatus,
)
from cqfi.batch.planner import BatchPlan, WorkItem
from cqfi.batch.worker import compute_batch, worker_init
from cqfi.cache.registry import CacheRegistry, get_cache_registry
from cqfi.config import AppSettings

BatchEventCallback = Callable[[BatchEvent], None]

_OWNER = "QuantLibAnalyticsCalculator"
_METHOD = "compute_bond_analytics"


class BatchEngine:
    """Runs a :class:`~cqfi.batch.planner.BatchPlan`.

    Dispatches one (issuer, trade_date) batch per worker process and writes
    every successful result straight into ``bond_analytics_db`` from a single
    connection held here — never from a worker (see ``worker.py`` module
    docstring for why: QuantLib global state and DuckDB's single-writer
    restriction both rule out writing from multiple processes).
    """

    def run(
        self,
        plan: BatchPlan,
        settings: AppSettings,
        *,
        workers: int,
        curve_label: str,
        on_event: BatchEventCallback,
        stop_event: threading.Event | None = None,
        also_cache: bool = False,
        compute_fn: Callable[..., list[BondCellPayload]] = compute_batch,
        worker_initializer: Callable[[str], None] = worker_init,
    ) -> None:
        """Run the plan to completion (or until *stop_event* is set).

        ``compute_fn``/``worker_initializer`` default to the real per-process
        compute path (``worker.compute_batch`` / ``worker.worker_init``) and
        only need overriding in tests, where a real ``ProcessPoolExecutor``
        still runs but with a cheap, QuantLib-free stand-in.

        ``also_cache``: when true, every successful result is *also* persisted
        to ``quant_cache_db`` (via the same shared registry ``/calc`` uses),
        in addition to the unconditional ``bond_analytics_db`` write. Workers
        never do this themselves — see ``worker.py`` — so it happens here,
        from the single calling process/thread, same as the primary write.
        Standalone-script runs (``batch_bond_analytics.py``) always pass
        ``False``; the ``/batch`` command passes the session's current
        ``use_quant_cache`` runtime setting.
        """
        total = plan.total_cells
        on_event(BatchStarted(total_cells=total))
        if total == 0:
            on_event(BatchDone(cancelled=False, completed=0, total=0))
            return

        registry = CacheRegistry(
            settings.bond_analytics_db_path, settings.quant_cache_semantics_path
        )
        completed = 0
        cancelled = False
        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=worker_initializer,
                initargs=(str(settings.config_path),),
            ) as executor:
                future_to_item: dict[Future, WorkItem] = {}
                for item in plan.work_items:
                    if not item.bonds:
                        continue
                    future = executor.submit(
                        compute_fn,
                        item.issuer,
                        item.trade_date,
                        [bond.as_dict() for bond in item.bonds],
                        curve_label,
                    )
                    future_to_item[future] = item
                    on_event(
                        CellsStarted(
                            issuer=item.issuer,
                            trade_date=item.trade_date,
                            bond_keys=tuple(str(bond) for bond in item.bonds),
                        )
                    )

                pending = set(future_to_item)
                while pending:
                    if stop_event is not None and stop_event.is_set():
                        cancelled = True
                        # All futures were submitted upfront, so most of
                        # ``pending`` is likely still sitting in the executor's
                        # internal queue rather than actually running.
                        # future.cancel() succeeds for those (they never start,
                        # never get a result) and fails (returns False) for
                        # ones a worker has already picked up — those we still
                        # have to wait for since we can't interrupt them mid-run.
                        still_running = {f for f in pending if not f.cancel()}
                        while still_running:
                            done, still_running = wait(still_running, timeout=0.5)
                            for future in done:
                                completed = self._handle_result(
                                    future, future_to_item[future], registry,
                                    curve_label, completed, total, on_event, also_cache,
                                )
                        break
                    done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        completed = self._handle_result(
                            future, future_to_item[future], registry,
                            curve_label, completed, total, on_event, also_cache,
                        )

                executor.shutdown(wait=True, cancel_futures=True)
        finally:
            registry.close()

        on_event(BatchDone(cancelled=cancelled, completed=completed, total=total))

    @staticmethod
    def _handle_result(
        future: Future,
        item: WorkItem,
        registry: CacheRegistry,
        curve_label: str,
        completed: int,
        total: int,
        on_event: BatchEventCallback,
        also_cache: bool = False,
    ) -> int:
        """Resolve one finished (issuer, trade_date) future: persist + emit per-bond events."""
        try:
            payloads = future.result()
        except Exception as exc:
            payloads = [
                BondCellPayload(bond_key=str(bond), success=False, error=str(exc))
                for bond in item.bonds
            ]

        for payload in payloads:
            status = CellStatus.FAILED
            detail = payload.error
            if payload.success:
                try:
                    registry.persist_bond_compute(
                        owner=_OWNER,
                        method=_METHOD,
                        request=payload.request,
                        curve_label=curve_label,
                        bond_metrics=payload.bond_metrics,
                        mm_cmt_metrics=payload.mm_cmt_metrics,
                        mm_fc_cmt_metrics=payload.mm_fc_cmt_metrics,
                    )
                    status = CellStatus.SUCCESS
                    detail = payload.bond_metrics.as_json(indent=2)
                except Exception as exc:
                    detail = f"Failed to write to bond_analytics_db: {exc}"

                if status == CellStatus.SUCCESS and also_cache:
                    try:
                        get_cache_registry().persist_bond_compute(
                            owner=_OWNER,
                            method=_METHOD,
                            request=payload.request,
                            curve_label=curve_label,
                            bond_metrics=payload.bond_metrics,
                            mm_cmt_metrics=payload.mm_cmt_metrics,
                            mm_fc_cmt_metrics=payload.mm_fc_cmt_metrics,
                        )
                    except Exception as exc:
                        detail = f"{detail}\n\n(Warning: not written to quant_cache_db: {exc})"
            completed += 1
            on_event(
                CellDone(
                    issuer=item.issuer,
                    trade_date=item.trade_date,
                    bond_key=payload.bond_key,
                    status=status,
                    detail=detail,
                    completed=completed,
                    total=total,
                )
            )
        return completed
