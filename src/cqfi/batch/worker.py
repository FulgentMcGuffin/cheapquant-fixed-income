"""Per-process compute for batch bond analytics.

Runs inside :class:`~concurrent.futures.ProcessPoolExecutor` workers: each
worker is a fully isolated Python process with its own QuantLib global state
(``ql.Settings.instance().evaluationDate``) and its own
``QuantlibMarketContextManager`` singleton, so concurrent batches never step
on each other. Workers never open ``bond_analytics_db`` — they only read
``ycs_db`` (curve rates) and return plain dataclass results for the calling
process (``engine.BatchEngine``) to persist.
"""

from __future__ import annotations

from datetime import date

from cqfi.analytics_input import BondAnalyticsInput
from cqfi.batch.models import BondCellPayload
from cqfi.instruments import Bond
from cqfi.issuers import resolve_issuer
from cqfi.quantlib.quantlib_analytics_calculator import QuantLibAnalyticsCalculator
from cqfi.quantlib.quantlib_market_context import QuantlibMarketContext
from cqfi.quantlib.quantlib_market_context_manager import QuantlibMarketContextManager


def worker_init(config_path: str) -> None:
    """``ProcessPoolExecutor`` initializer: load settings once per worker.

    Also forces ``use_quant_cache`` off in this process's runtime settings so
    ``compute_bond_analytics``'s ``@cache_bond_analytics`` decorator never
    writes to ``quant_cache_db`` — batch runs persist to ``bond_analytics_db``
    directly (see ``engine.BatchEngine``), regardless of what the user's
    saved runtime toggle says.
    """
    from cqfi.config import load_runtime_settings, load_settings

    load_settings(config_path)
    runtime = load_runtime_settings(create_if_missing=False)
    runtime.use_quant_cache = False


def _market_context(
    trade_date: date, issuer_code: str, curve_label: str
) -> QuantlibMarketContext | None:
    """Build (or reuse) the context for *trade_date*, or ``None`` if curve data is missing."""
    manager = QuantlibMarketContextManager.instance()
    if manager.get(trade_date, issuer_code, curve_label) is None:
        return None
    return manager.get(trade_date)


def compute_batch(
    issuer_code: str,
    trade_date: date,
    bond_rows: list[dict],
    curve_label: str,
) -> list[BondCellPayload]:
    """Compute analytics for every bond in *bond_rows* on *trade_date*.

    One market context is built (or reused within this worker process) and
    shared across every bond — the reason work is batched by issuer x day. A
    failure building the context fails every bond in the batch; a failure
    pricing one bond only fails that bond.
    """
    issuer = resolve_issuer(issuer_code)
    calculator = QuantLibAnalyticsCalculator()

    try:
        context = _market_context(trade_date, issuer.source_code, curve_label)
    except Exception as exc:
        context = None
        context_error = str(exc)
    else:
        context_error = (
            None
            if context is not None
            else f"No market context for {issuer.source_code} on {trade_date} ({curve_label})"
        )

    if context is None:
        return [
            BondCellPayload(bond_key=str(Bond.from_row(row)), success=False, error=context_error)
            for row in bond_rows
        ]

    results: list[BondCellPayload] = []
    for row in bond_rows:
        bond = Bond.from_row(row)
        try:
            settlement = issuer.settlement_date(trade_date)
            analytics_input = BondAnalyticsInput.from_bond(
                bond, settlement_date=settlement, trade_date=trade_date
            )
            bond_metrics, mm_cmt, mm_fc = calculator.compute_bond_analytics(
                analytics_input, context, curve_label=curve_label
            )
            results.append(
                BondCellPayload(
                    bond_key=str(bond),
                    success=True,
                    request=analytics_input,
                    bond_metrics=bond_metrics,
                    mm_cmt_metrics=mm_cmt,
                    mm_fc_cmt_metrics=mm_fc,
                )
            )
        except Exception as exc:
            results.append(BondCellPayload(bond_key=str(bond), success=False, error=str(exc)))

    return results
