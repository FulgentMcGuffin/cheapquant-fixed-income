"""Per-process compute for batch bond-future analytics.

Runs inside :class:`~concurrent.futures.ProcessPoolExecutor` workers, same
isolation rationale as ``worker.py`` (QuantLib global evaluation-date state,
``QuantlibMarketContextManager`` per-process singleton). Reuses
``worker.worker_init`` verbatim — settings/runtime bootstrap doesn't differ
between bond and bond-future batches. Workers never open
``bond_analytics_db`` — delivery baskets are built once in the main process
(``future_planner.build_future_series_plan``) and shipped in as plain,
already-picklable dataclasses (:class:`~cqfi.delivery_basket.DeliveryBasket`
holds only :class:`~cqfi.instruments.Bond` records and primitives).
"""

from __future__ import annotations

from datetime import date

from cqfi.batch.models import FutureCellPayload
from cqfi.bond_future_input import BondFutureInput
from cqfi.delivery_basket import DeliveryBasket
from cqfi.quantlib.quantlib_bond_future_calculator import QuantLibBondFutureCalculator
from cqfi.quantlib.quantlib_market_context import QuantlibMarketContext
from cqfi.quantlib.quantlib_market_context_manager import QuantlibMarketContextManager


def _market_context(
    trade_date: date, issuer_code: str, curve_label: str
) -> QuantlibMarketContext | None:
    """Build (or reuse) the context for *trade_date*, or ``None`` if curve data is missing."""
    manager = QuantlibMarketContextManager.instance()
    if manager.get(trade_date, issuer_code, curve_label) is None:
        return None
    return manager.get(trade_date)


def compute_future_batch(
    issuer_code: str,
    trade_date: date,
    baskets: list[DeliveryBasket],
    curve_label: str,
) -> list[FutureCellPayload]:
    """Compute basis analytics for every contract basket in *baskets* on *trade_date*.

    One market context is built (or reused within this worker process) and
    shared across every contract — the reason work is batched by issuer x
    day. A failure building the context fails every contract in the batch; a
    failure pricing one contract's basket only fails that contract (mirrors
    ``/fut``, which fails or succeeds for a whole basket at a time).
    """
    calculator = QuantLibBondFutureCalculator()

    try:
        context = _market_context(trade_date, issuer_code, curve_label)
    except Exception as exc:
        context = None
        context_error = str(exc)
    else:
        context_error = (
            None
            if context is not None
            else f"No market context for {issuer_code} on {trade_date} ({curve_label})"
        )

    if context is None:
        return [
            FutureCellPayload(contract=str(basket.bond_future), success=False, error=context_error)
            for basket in baskets
        ]

    results: list[FutureCellPayload] = []
    for basket in baskets:
        contract_label = str(basket.bond_future)
        try:
            request = BondFutureInput.from_basket(basket, trade_date, curve_label=curve_label)
            result = calculator.compute_bond_future_analytics(
                request, context, curve_label=curve_label
            )
            results.append(
                FutureCellPayload(contract=contract_label, success=True, request=request, result=result)
            )
        except Exception as exc:
            results.append(FutureCellPayload(contract=contract_label, success=False, error=str(exc)))

    return results
