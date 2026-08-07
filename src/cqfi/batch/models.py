"""Shared data model for batch bond-analytics runs.

Used by ``planner`` (what to compute), ``worker`` (per-process compute
results), ``engine`` (orchestration events), and the CLI/GUI front ends
(rendering). Kept dependency-light so it is safe to import from a
:class:`~concurrent.futures.ProcessPoolExecutor` worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from cqfi.analytics_input import BondAnalyticsInput
from cqfi.analytics_output import FixedIncomeAnalyticsOutput
from cqfi.bond_future_input import BondFutureInput
from cqfi.bond_future_output import BondFutureBasketOutput


class CellStatus(Enum):
    """Heatmap cell state for one (row, trade_date) pair.

    Shared between the bond grid (row = bond) and the bond-future grid
    (row = dated contract).
    """

    NOT_APPLICABLE = "not_applicable"  # row not active/deliverable on this trade date
    PENDING = "pending"  # queued, not yet dispatched to a worker
    IN_PROGRESS = "in_progress"  # dispatched, worker has not returned yet
    SUCCESS = "success"  # computed and written to bond_analytics_db
    FAILED = "failed"  # compute or persist error


@dataclass(frozen=True)
class BondCellPayload:
    """One bond's compute outcome from a worker process.

    Plain data only (no open DB connections, no QuantLib handles) so it can
    cross the :class:`~concurrent.futures.ProcessPoolExecutor` process
    boundary via pickling.
    """

    bond_key: str
    success: bool
    request: BondAnalyticsInput | None = None
    bond_metrics: FixedIncomeAnalyticsOutput | None = None
    mm_cmt_metrics: FixedIncomeAnalyticsOutput | None = None
    mm_fc_cmt_metrics: FixedIncomeAnalyticsOutput | None = None
    error: str | None = None


@dataclass(frozen=True)
class BatchStarted:
    """Emitted once, before any work item is submitted."""

    total_cells: int


@dataclass(frozen=True)
class CellsStarted:
    """Emitted when one (issuer, trade_date) batch is dispatched to a worker."""

    issuer: str
    trade_date: date
    bond_keys: tuple[str, ...]


@dataclass(frozen=True)
class CellDone:
    """Emitted per bond once its batch completes and the DB write is resolved."""

    issuer: str
    trade_date: date
    bond_key: str
    status: CellStatus
    detail: str | None  # bond_analytics row JSON on SUCCESS, error message on FAILED
    completed: int
    total: int


@dataclass(frozen=True)
class BatchDone:
    """Emitted once, at the end of a run (whether it finished or was stopped)."""

    cancelled: bool
    completed: int
    total: int


@dataclass(frozen=True)
class Stopping:
    """Emitted while draining after a stop request, once ``remaining`` changes.

    Queued work is never submitted after Stop (engines feed the process pool
    lazily). In-flight worker processes are terminated so CPU load drops
    immediately; their futures then settle with errors and are abandoned
    (not written to the DB). ``total`` is the in-flight count when Stop was
    first detected; ``remaining`` counts down to 0 as those futures settle.
    """

    remaining: int
    total: int


BatchEvent = BatchStarted | CellsStarted | CellDone | BatchDone | Stopping


# ── Bond futures (cqfi.batch.future_planner / future_worker / future_engine) ─


@dataclass(frozen=True)
class FutureCellPayload:
    """One dated contract's compute outcome from a worker process.

    A contract's basket is priced as one unit (mirrors ``/fut``, which fails
    or succeeds for the whole basket together) — plain data only, so it can
    cross the :class:`~concurrent.futures.ProcessPoolExecutor` process
    boundary via pickling.
    """

    contract: str  # str(BondFuture), e.g. "FGBMH0"
    success: bool
    request: BondFutureInput | None = None
    result: BondFutureBasketOutput | None = None
    error: str | None = None


@dataclass(frozen=True)
class FutureCellsStarted:
    """Emitted when one (issuer, trade_date) batch is dispatched to a worker."""

    issuer: str
    trade_date: date
    contracts: tuple[str, ...]


@dataclass(frozen=True)
class FutureCellDone:
    """Emitted per contract once its batch completes and the DB write is resolved."""

    issuer: str
    trade_date: date
    contract: str
    status: CellStatus
    detail: str | None  # bond_future_outputs rows JSON on SUCCESS, error on FAILED
    completed: int
    total: int


FutureBatchEvent = BatchStarted | FutureCellsStarted | FutureCellDone | BatchDone | Stopping
