"""Planning for batch bond-future analytics runs: contracts, trade dates, baskets.

Pure and synchronous — SQL reads (via ``DeliveryBasket.auto``) and calendar
math, no QuantLib pricing — so the whole plan is built once, up front, in the
main process before any worker is spawned (see ``future_engine.FutureBatchEngine``).

Mirrors ``cqfi.batch.planner`` closely: a :class:`FutureSeriesPlan` is one
future code's tab (like one issuer's), a :class:`FutureWorkItem` is one
(issuer, trade_date) compute batch (like the bond ``WorkItem``) — except the
row unit here is a *dated contract* (its basket, fixed at construction) and
the "active" test is ``trade_date <= contract.delivery_end_date()`` rather
than an issuance/maturity window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cqfi.batch.planner import resolve_trade_dates
from cqfi.bond_futures import (
    MONTH_CODE_TO_MONTH,
    BondFuture,
    BondFutureError,
    resolve_bond_future_convention,
)
from cqfi.config import AppSettings
from cqfi.delivery_basket import DeliveryBasket


def parse_delivery_letters(text: str) -> list[str]:
    """Parse e.g. ``"HMUZ"`` into ``["H", "M", "U", "Z"]``, de-duplicated in order.

    Raises:
        BondFutureError: If *text* is empty or contains a character that
            isn't one of the twelve delivery-month letters (``FGHJKMNQUVXZ``).
    """
    letters = text.strip().upper()
    if not letters:
        raise BondFutureError("--delivery must not be empty")
    unknown = sorted(set(letters) - set(MONTH_CODE_TO_MONTH))
    if unknown:
        raise BondFutureError(
            f"Unknown delivery month letter(s) {', '.join(unknown)!r} in {text!r}; "
            f"expected letters from {''.join(sorted(MONTH_CODE_TO_MONTH))}"
        )
    seen: list[str] = []
    for letter in letters:
        if letter not in seen:
            seen.append(letter)
    return seen


@dataclass(frozen=True)
class FutureWorkItem:
    """One (issuer, trade_date) compute batch: every contract still valid that day.

    A contract counts as valid on a trade date when the date is on or before
    its delivery — basket membership never changes with the trade date.
    """

    issuer: str
    trade_date: date
    baskets: tuple[DeliveryBasket, ...]


@dataclass(frozen=True)
class FutureSeriesPlan:
    """Everything needed to run and render one future code's tab."""

    future_code: str
    contracts: tuple[BondFuture, ...]  # every dated contract in the plan, by delivery date
    trade_dates: tuple[date, ...]  # every trade date with curve data in [start, end]
    work_items: tuple[FutureWorkItem, ...]  # one per trade date, in trade_date order

    @property
    def total_cells(self) -> int:
        return sum(len(item.baskets) for item in self.work_items)


@dataclass(frozen=True)
class FutureBatchPlan:
    """Full plan for a batch run across one or more future codes."""

    series_plans: tuple[FutureSeriesPlan, ...]

    @property
    def total_cells(self) -> int:
        return sum(series.total_cells for series in self.series_plans)

    @property
    def work_items(self) -> tuple[FutureWorkItem, ...]:
        return tuple(item for series in self.series_plans for item in series.work_items)


def build_future_series_plan(
    future_code: str,
    delivery_letters: list[str],
    start: date,
    end: date,
    settings: AppSettings,
) -> FutureSeriesPlan:
    """Build the trade-date x contract plan for one future code."""
    convention = resolve_bond_future_convention(future_code)
    issuer = convention.issuer()
    trade_dates = resolve_trade_dates(issuer, start, end, settings)

    future_contracts = sorted(
        (
            BondFuture(convention=convention, delivery_month=MONTH_CODE_TO_MONTH[letter], delivery_year=year)
            for year in range(start.year, end.year + 1)
            for letter in delivery_letters
        ),
        key=lambda future: future.delivery_end_date(),
    )
    # Paired list, not a dict keyed by BondFuture — avoids relying on the
    # frozen-dataclass chain (BondFuture -> BondFutureConvention -> ...)
    # staying hashable.
    contracts_and_baskets = [
        (future, DeliveryBasket.auto(future, db_path=settings.bond_analytics_db_path))
        for future in future_contracts
    ]

    work_items = tuple(
        FutureWorkItem(
            issuer=issuer.source_code,
            trade_date=trade_date,
            baskets=tuple(
                basket
                for future, basket in contracts_and_baskets
                if trade_date <= future.delivery_end_date()
            ),
        )
        for trade_date in trade_dates
    )

    return FutureSeriesPlan(
        future_code=convention.name,
        contracts=tuple(future_contracts),
        trade_dates=tuple(trade_dates),
        work_items=work_items,
    )


def build_future_plan(
    future_codes: list[str],
    delivery: str,
    start: date,
    end: date,
    settings: AppSettings,
) -> FutureBatchPlan:
    """Build the full multi-contract batch plan."""
    letters = parse_delivery_letters(delivery)

    # Resolve and de-duplicate by canonical convention name, first-seen order.
    seen: dict[str, str] = {}
    for code in future_codes:
        convention = resolve_bond_future_convention(code)
        seen.setdefault(convention.name, code)

    return FutureBatchPlan(
        series_plans=tuple(
            build_future_series_plan(code, letters, start, end, settings)
            for code in seen.values()
        )
    )
