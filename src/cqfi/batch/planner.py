"""Planning for batch bond-analytics runs: issuers, trade dates, active bonds.

Pure and synchronous — only SQL reads and calendar math, no QuantLib pricing
— so the whole plan is built once, up front, in the main process before any
worker is spawned (see ``engine.BatchEngine``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cqfi.bond_manager import BondManager
from cqfi.config import AppSettings
from cqfi.data.rates_loader import list_available_dates
from cqfi.instruments import Bond
from cqfi.issuers import IssuerProfile, RateType, resolve_issuer


def resolve_issuers(codes: list[str]) -> list[IssuerProfile]:
    """Resolve issuer codes/aliases to profiles, de-duplicated, first-seen order."""
    resolved: dict[str, IssuerProfile] = {}
    for code in codes:
        profile = resolve_issuer(code)
        resolved.setdefault(profile.source_code, profile)
    return list(resolved.values())


def is_bond_active(bond: Bond, trade_date: date, settlement_date: date) -> bool:
    """A bond is active when already issued and not yet matured.

    ``issue_date`` is optional in ``bond_universe``; a bond with no
    ``issue_date`` is treated as already issued (``/calc`` never requires
    ``issue_date`` to price a bond either).
    """
    if bond.issue_date is not None and bond.issue_date > trade_date:
        return False
    return bond.maturity > settlement_date


def resolve_trade_dates(
    issuer: IssuerProfile,
    start: date,
    end: date,
    settings: AppSettings,
    rate_type: RateType = RateType.ZERO,
) -> list[date]:
    """Dates with curve data for *issuer* inside ``[start, end]``, ascending."""
    if end < start:
        raise ValueError(f"end date {end} is before start date {start}")
    frame = list_available_dates(settings.ycs_db_path, issuer, rate_type=rate_type)
    if frame.is_empty():
        return []
    all_dates = (date.fromisoformat(str(value)[:10]) for value in frame["date"].to_list())
    return sorted(d for d in all_dates if start <= d <= end)


@dataclass(frozen=True)
class WorkItem:
    """One (issuer, trade_date) compute batch: every bond active that day."""

    issuer: str
    trade_date: date
    bonds: tuple[Bond, ...]


@dataclass(frozen=True)
class IssuerPlan:
    """Everything needed to run and render one issuer's tab."""

    issuer: str
    trade_dates: tuple[date, ...]
    bonds: tuple[Bond, ...]  # every bond active on >=1 trade date, sorted by maturity
    work_items: tuple[WorkItem, ...]  # one per trade date, in trade_date order

    @property
    def total_cells(self) -> int:
        return sum(len(item.bonds) for item in self.work_items)


@dataclass(frozen=True)
class BatchPlan:
    """Full plan for a batch run across one or more issuers."""

    issuer_plans: tuple[IssuerPlan, ...]

    @property
    def total_cells(self) -> int:
        return sum(plan.total_cells for plan in self.issuer_plans)

    @property
    def work_items(self) -> tuple[WorkItem, ...]:
        return tuple(item for plan in self.issuer_plans for item in plan.work_items)


def build_issuer_plan(
    issuer: IssuerProfile,
    start: date,
    end: date,
    settings: AppSettings,
) -> IssuerPlan:
    """Build the trade-date x active-bond plan for one issuer."""
    trade_dates = resolve_trade_dates(issuer, start, end, settings)
    all_bonds = BondManager.instance().get_by_issuer(
        issuer.source_code, db_path=settings.bond_analytics_db_path
    )

    work_items: list[WorkItem] = []
    active_keys: set[str] = set()
    for trade_date in trade_dates:
        settlement = issuer.settlement_date(trade_date)
        active = tuple(
            bond for bond in all_bonds if is_bond_active(bond, trade_date, settlement)
        )
        work_items.append(WorkItem(issuer=issuer.source_code, trade_date=trade_date, bonds=active))
        active_keys.update(str(bond) for bond in active)

    row_bonds = tuple(
        sorted(
            (bond for bond in all_bonds if str(bond) in active_keys),
            key=lambda bond: (bond.maturity, str(bond)),
        )
    )
    return IssuerPlan(
        issuer=issuer.source_code,
        trade_dates=tuple(trade_dates),
        bonds=row_bonds,
        work_items=tuple(work_items),
    )


def build_plan(
    issuer_codes: list[str],
    start: date,
    end: date,
    settings: AppSettings,
) -> BatchPlan:
    """Build the full multi-issuer batch plan."""
    issuers = resolve_issuers(issuer_codes)
    return BatchPlan(
        issuer_plans=tuple(
            build_issuer_plan(issuer, start, end, settings) for issuer in issuers
        )
    )
