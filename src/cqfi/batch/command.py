"""Parsing and plan-building for the ``/batch`` slash command.

Shared by the CLI REPL (``agent/cli.py``) and the GUI chat worker
(``gui/chat_dialog.py``) so both front ends parse ``/batch`` identically and
build the same kind of launch request from it. Two forms, disambiguated by
shape (the future form's second token is letters-only; a date never is, so
there is no overlap):

    /batch <issuer> <start> <end>                      bond mode
    /batch <future_code> <delivery_letters> <start> <end>   future mode
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from cqfi.batch.future_planner import FutureBatchPlan, build_future_plan
from cqfi.batch.planner import BatchPlan, build_plan
from cqfi.config import AppSettings, get_runtime_settings, get_settings

_BATCH_FUTURE_RE = re.compile(
    r"^/batch\s+(?P<future_code>\S+)\s+(?P<delivery>[FGHJKMNQUVXZ]+)\s+"
    r"(?P<start>\d{4}-\d{2}-\d{2})\s+(?P<end>\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)
_BATCH_BOND_RE = re.compile(
    r"^/batch\s+(?P<issuer>\S+)\s+(?P<start>\d{4}-\d{2}-\d{2})\s+(?P<end>\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)
_BATCH_HELP_RE = re.compile(r"^/batch\s*$", re.IGNORECASE)

DEFAULT_CURVE_LABEL = "BOND_ZERO"
DEFAULT_DELIVERY = "HMUZ"


@dataclass(frozen=True)
class BatchCommandResult:
    """Parsed ``/batch`` slash command."""

    kind: Literal["help", "invalid", "run"]
    mode: Literal["bond", "future"] | None = None
    issuer: str | None = None  # bond mode
    future_code: str | None = None  # future mode
    delivery: str | None = None  # future mode, e.g. "HMUZ"
    start: date | None = None
    end: date | None = None


def parse_batch_command(text: str) -> BatchCommandResult | None:
    """Parse ``/batch``. ``None`` if *text* isn't a ``/batch`` command at all."""
    stripped = text.strip()
    if not re.match(r"^/batch\b", stripped, re.IGNORECASE):
        return None
    if _BATCH_HELP_RE.match(stripped):
        return BatchCommandResult(kind="help")

    future_match = _BATCH_FUTURE_RE.match(stripped)
    if future_match:
        try:
            start = date.fromisoformat(future_match.group("start"))
            end = date.fromisoformat(future_match.group("end"))
        except ValueError:
            return BatchCommandResult(kind="invalid")
        if end < start:
            return BatchCommandResult(kind="invalid")
        return BatchCommandResult(
            kind="run",
            mode="future",
            future_code=future_match.group("future_code"),
            delivery=future_match.group("delivery"),
            start=start,
            end=end,
        )

    bond_match = _BATCH_BOND_RE.match(stripped)
    if bond_match:
        try:
            start = date.fromisoformat(bond_match.group("start"))
            end = date.fromisoformat(bond_match.group("end"))
        except ValueError:
            return BatchCommandResult(kind="invalid")
        if end < start:
            return BatchCommandResult(kind="invalid")
        return BatchCommandResult(
            kind="run", mode="bond", issuer=bond_match.group("issuer"), start=start, end=end
        )

    return BatchCommandResult(kind="invalid")


@dataclass(frozen=True)
class BatchLaunchRequest:
    """Everything needed to open the bond batch progress window for one ``/batch`` run."""

    plan: BatchPlan
    settings: AppSettings
    workers: int
    curve_label: str
    also_cache: bool
    args_summary: str


@dataclass(frozen=True)
class FutureBatchLaunchRequest:
    """Everything needed to open the bond-future batch progress window."""

    plan: FutureBatchPlan
    settings: AppSettings
    workers: int
    curve_label: str
    also_cache: bool
    args_summary: str


def _resolved_workers(workers: int | None) -> int:
    return workers or os.cpu_count() or 4


def build_batch_launch_request(
    parsed: BatchCommandResult,
    *,
    curve_label: str = DEFAULT_CURVE_LABEL,
    workers: int | None = None,
) -> BatchLaunchRequest:
    """Build the plan (and everything else the GUI needs) for a validated bond ``/batch`` command.

    Uses the already-loaded session config/settings (``get_settings()``) —
    unlike the standalone script, ``/batch`` never takes a ``--config`` path.
    ``also_cache`` mirrors the session's live ``use_quant_cache`` toggle
    (``/cache on``/``/cache off``): when on, results are written to
    ``quant_cache_db`` in addition to the unconditional ``bond_analytics_db``
    write (see ``BatchEngine.run``'s ``also_cache`` docstring).
    """
    assert parsed.kind == "run" and parsed.mode == "bond"
    assert parsed.issuer and parsed.start and parsed.end
    settings = get_settings()
    resolved_workers = _resolved_workers(workers)
    plan = build_plan([parsed.issuer], parsed.start, parsed.end, settings)
    also_cache = get_runtime_settings().use_quant_cache
    issuer_label = plan.issuer_plans[0].issuer if plan.issuer_plans else parsed.issuer.upper()

    summary = (
        f"issuer: {issuer_label}  |  start: {parsed.start.isoformat()}  |  "
        f"end: {parsed.end.isoformat()}  |  curve: {curve_label}  |  "
        f"workers: {resolved_workers}  |  cache: {'on' if also_cache else 'off'}  |  "
        f"config: {settings.config_path}"
    )
    return BatchLaunchRequest(
        plan=plan,
        settings=settings,
        workers=resolved_workers,
        curve_label=curve_label,
        also_cache=also_cache,
        args_summary=summary,
    )


def build_future_batch_launch_request(
    parsed: BatchCommandResult,
    *,
    curve_label: str = DEFAULT_CURVE_LABEL,
    workers: int | None = None,
) -> FutureBatchLaunchRequest:
    """Build the plan (and everything else the GUI needs) for a validated future ``/batch`` command.

    Same config/cache-toggle behaviour as :func:`build_batch_launch_request`,
    but for one bond-future code across every delivery month letter in
    ``parsed.delivery`` (default ``HMUZ`` — all four quarterly months) and
    every calendar year spanned by ``[parsed.start, parsed.end]``.
    """
    assert parsed.kind == "run" and parsed.mode == "future"
    assert parsed.future_code and parsed.start and parsed.end
    settings = get_settings()
    resolved_workers = _resolved_workers(workers)
    delivery = parsed.delivery or DEFAULT_DELIVERY
    plan = build_future_plan([parsed.future_code], delivery, parsed.start, parsed.end, settings)
    also_cache = get_runtime_settings().use_quant_cache
    future_label = (
        plan.series_plans[0].future_code if plan.series_plans else parsed.future_code.upper()
    )

    summary = (
        f"future: {future_label}  |  delivery: {delivery}  |  "
        f"start: {parsed.start.isoformat()}  |  end: {parsed.end.isoformat()}  |  "
        f"curve: {curve_label}  |  workers: {resolved_workers}  |  "
        f"cache: {'on' if also_cache else 'off'}  |  config: {settings.config_path}"
    )
    return FutureBatchLaunchRequest(
        plan=plan,
        settings=settings,
        workers=resolved_workers,
        curve_label=curve_label,
        also_cache=also_cache,
        args_summary=summary,
    )
