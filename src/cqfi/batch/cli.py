"""CLI entry point for batch bond and bond-future analytics.

Bond mode::

    uv run batch_bond_analytics.py --config ./config/cqfi.yaml \\
        --issuer FRA ITA --start 2020-01-01 --end 2020-12-31

Bond-future mode::

    uv run batch_bond_analytics.py --config ./config/cqfi.yaml \\
        --future FGBX FGBM --delivery HMUZ --start 2020-01-01 --end 2020-12-31

Choose exactly one of ``--issuer`` or ``--future``. Launches the themed
progress GUI by default; pass ``--no-gui`` for a plain console progress bar
(useful for headless/CI runs).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from cqfi.batch.command import DEFAULT_MAX_WORKERS, default_workers
from cqfi.batch.engine import BatchEngine
from cqfi.batch.future_engine import FutureBatchEngine
from cqfi.batch.future_planner import FutureBatchPlan, build_future_plan
from cqfi.batch.models import (
    BatchDone,
    BatchStarted,
    CellDone,
    CellStatus,
    FutureCellDone,
)
from cqfi.batch.planner import BatchPlan, build_plan
from cqfi.bond_futures import BondFutureError
from cqfi.config import AppSettings, configure_langsmith, load_settings


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="batch_bond_analytics",
        description=(
            "Batch-compute bond or bond-future analytics over a date range, "
            "writing directly into bond_analytics_db (quant_cache_db is never "
            "touched). Choose exactly one of --issuer (bond mode) or --future "
            "(bond-future mode)."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="YAML config file (default: CQFI_CONFIG env var or config/cqfi.yaml).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--issuer",
        nargs="+",
        metavar="CODE",
        help="Bond mode: one or more issuer codes/aliases (e.g. FRA ITA usa), case-insensitive.",
    )
    mode.add_argument(
        "--future",
        nargs="+",
        metavar="CODE",
        help="Bond-future mode: one or more future codes (e.g. FGBX FGBM), case-insensitive.",
    )
    parser.add_argument(
        "--delivery",
        default="HMUZ",
        metavar="LETTERS",
        help=(
            "Bond-future mode only: delivery month letters to include, from "
            "FGHJKMNQUVXZ (default: HMUZ = all four quarterly months). One "
            "dated contract is built per letter x per calendar year spanned "
            "by --start/--end."
        ),
    )
    parser.add_argument("--start", type=_parse_date, required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--end", type=_parse_date, required=True, metavar="YYYY-MM-DD")
    parser.add_argument(
        "--curve-label",
        default="BOND_ZERO",
        choices=("BOND_ZERO", "BOND_PAR"),
        help="Curve collection to price against (default: BOND_ZERO).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Worker processes (default: min(os.cpu_count(), {DEFAULT_MAX_WORKERS}) — "
            "capped so Stop has fewer in-flight processes to terminate on "
            "many-core machines; pass a higher N explicitly for max-throughput "
            "unattended runs)."
        ),
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Run in the console with a text progress bar instead of the GUI.",
    )
    return parser


def _console_reporter(start_time: float) -> callable:
    """Return an ``on_event`` callback that prints a text progress bar (bond mode)."""

    def on_event(event) -> None:
        if isinstance(event, BatchStarted):
            print(f"Computing {event.total_cells} bond x trade-date cells...")
        elif isinstance(event, CellDone):
            bar_len = 30
            frac = event.completed / event.total if event.total else 1.0
            filled = int(bar_len * frac)
            bar = "#" * filled + "-" * (bar_len - filled)
            mark = "ok" if event.status == CellStatus.SUCCESS else "FAIL"
            elapsed = time.monotonic() - start_time
            print(
                f"\r[{bar}] {event.completed}/{event.total} ({frac * 100:5.1f}%) "
                f"last={mark:<4} {event.issuer} {event.trade_date} {event.bond_key} "
                f"({elapsed:6.1f}s)",
                end="",
                flush=True,
            )
            if event.status == CellStatus.FAILED:
                print(
                    f"\n  FAILED {event.issuer} {event.bond_key} {event.trade_date}: "
                    f"{event.detail}"
                )
        elif isinstance(event, BatchDone):
            print()
            if event.cancelled:
                print(f"Stopped after {event.completed}/{event.total} cells.")
            else:
                print(f"Done: {event.completed}/{event.total} cells computed.")

    return on_event


def _future_console_reporter(start_time: float) -> callable:
    """Return an ``on_event`` callback that prints a text progress bar (future mode)."""

    def on_event(event) -> None:
        if isinstance(event, BatchStarted):
            print(f"Computing {event.total_cells} contract x trade-date cells...")
        elif isinstance(event, FutureCellDone):
            bar_len = 30
            frac = event.completed / event.total if event.total else 1.0
            filled = int(bar_len * frac)
            bar = "#" * filled + "-" * (bar_len - filled)
            mark = "ok" if event.status == CellStatus.SUCCESS else "FAIL"
            elapsed = time.monotonic() - start_time
            print(
                f"\r[{bar}] {event.completed}/{event.total} ({frac * 100:5.1f}%) "
                f"last={mark:<4} {event.issuer} {event.trade_date} {event.contract} "
                f"({elapsed:6.1f}s)",
                end="",
                flush=True,
            )
            if event.status == CellStatus.FAILED:
                print(
                    f"\n  FAILED {event.issuer} {event.contract} {event.trade_date}: "
                    f"{event.detail}"
                )
        elif isinstance(event, BatchDone):
            print()
            if event.cancelled:
                print(f"Stopped after {event.completed}/{event.total} cells.")
            else:
                print(f"Done: {event.completed}/{event.total} cells computed.")

    return on_event


def _args_summary(
    issuer_codes: list[str],
    plan: BatchPlan,
    start: date,
    end: date,
    curve_label: str,
    workers: int,
    settings: AppSettings,
) -> str:
    issuers = ", ".join(p.issuer for p in plan.issuer_plans) or ", ".join(issuer_codes)
    return (
        f"issuers: {issuers}  |  start: {start.isoformat()}  |  end: {end.isoformat()}  |  "
        f"curve: {curve_label}  |  workers: {workers}  |  config: {settings.config_path}"
    )


def _future_args_summary(
    future_codes: list[str],
    delivery: str,
    plan: FutureBatchPlan,
    start: date,
    end: date,
    curve_label: str,
    workers: int,
    settings: AppSettings,
) -> str:
    codes = ", ".join(s.future_code for s in plan.series_plans) or ", ".join(future_codes)
    return (
        f"futures: {codes}  |  delivery: {delivery}  |  start: {start.isoformat()}  |  "
        f"end: {end.isoformat()}  |  curve: {curve_label}  |  workers: {workers}  |  "
        f"config: {settings.config_path}"
    )


def run_gui_standalone(
    plan: BatchPlan,
    settings: AppSettings,
    *,
    workers: int,
    curve_label: str,
    args_summary: str,
    qt_extra: list[str] = (),
    also_cache: bool = False,
) -> int:
    """Bootstrap sys.path for the gui/ package (bare imports) and launch the bond window.

    Mirrors ``cqfi.gui.app.main`` — PySide6 and gui/ modules are only
    imported after the gui/ directory is on ``sys.path``. Reuses an existing
    ``QApplication`` when one is already running in this process (e.g. the
    ``/batch`` slash command invoked from the plain console REPL, where a
    previous ``/batch`` call may already have created one) — Qt allows only
    one ``QApplication`` per process, but ``exec()`` can be re-entered.
    """
    qt_app = _bootstrap_qt_app(qt_extra)

    from batch_dialog import BatchAnalyticsWindow  # noqa: PLC0415

    window = BatchAnalyticsWindow(
        plan=plan,
        settings=settings,
        workers=workers,
        curve_label=curve_label,
        args_summary=args_summary,
        also_cache=also_cache,
    )
    window.show()
    window.start()
    return qt_app.exec()


def run_future_gui_standalone(
    plan: FutureBatchPlan,
    settings: AppSettings,
    *,
    workers: int,
    curve_label: str,
    args_summary: str,
    qt_extra: list[str] = (),
    also_cache: bool = False,
) -> int:
    """Same as :func:`run_gui_standalone`, for a bond-future plan."""
    qt_app = _bootstrap_qt_app(qt_extra)

    from batch_dialog import FutureBatchAnalyticsWindow  # noqa: PLC0415

    window = FutureBatchAnalyticsWindow(
        plan=plan,
        settings=settings,
        workers=workers,
        curve_label=curve_label,
        args_summary=args_summary,
        also_cache=also_cache,
    )
    window.show()
    window.start()
    return qt_app.exec()


def _bootstrap_qt_app(qt_extra: list[str]):
    """Put gui/ on sys.path and return the (possibly newly-created) QApplication."""
    gui_dir = str(Path(__file__).resolve().parents[1] / "gui")
    if gui_dir not in sys.path:
        sys.path.insert(0, gui_dir)

    from PySide6.QtGui import QFont  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    qt_app = QApplication.instance()
    if qt_app is None:
        qt_app = QApplication([sys.argv[0]] + list(qt_extra))
        font = QFont("Segoe UI", 10)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        qt_app.setFont(font)
    return qt_app


def _run_bond_mode(args: argparse.Namespace, settings: AppSettings, workers: int, qt_extra: list[str]) -> int:
    plan = build_plan(args.issuer, args.start, args.end, settings)
    if plan.total_cells == 0:
        print(
            "No active bonds found for the given issuer(s)/date range; "
            "nothing to compute."
        )
        return 0

    if args.no_gui:
        BatchEngine().run(
            plan,
            settings,
            workers=workers,
            curve_label=args.curve_label,
            on_event=_console_reporter(time.monotonic()),
        )
        return 0

    summary = _args_summary(
        args.issuer, plan, args.start, args.end, args.curve_label, workers, settings
    )
    return run_gui_standalone(
        plan,
        settings,
        workers=workers,
        curve_label=args.curve_label,
        args_summary=summary,
        qt_extra=qt_extra,
    )


def _run_future_mode(args: argparse.Namespace, settings: AppSettings, workers: int, qt_extra: list[str]) -> int:
    try:
        plan = build_future_plan(args.future, args.delivery, args.start, args.end, settings)
    except BondFutureError as exc:
        print(f"Error: {exc}")
        return 1

    if plan.total_cells == 0:
        print(
            "No contracts overlap the given future(s)/delivery/date range; "
            "nothing to compute."
        )
        return 0

    if args.no_gui:
        FutureBatchEngine().run(
            plan,
            settings,
            workers=workers,
            curve_label=args.curve_label,
            on_event=_future_console_reporter(time.monotonic()),
        )
        return 0

    summary = _future_args_summary(
        args.future, args.delivery, plan, args.start, args.end, args.curve_label, workers, settings
    )
    return run_future_gui_standalone(
        plan,
        settings,
        workers=workers,
        curve_label=args.curve_label,
        args_summary=summary,
        qt_extra=qt_extra,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args, qt_extra = parser.parse_known_args(argv)

    if args.end < args.start:
        parser.error(f"--end {args.end} is before --start {args.start}")

    configure_langsmith()

    settings = load_settings(args.config)
    workers = args.workers or default_workers()

    if args.future:
        return _run_future_mode(args, settings, workers, qt_extra)
    return _run_bond_mode(args, settings, workers, qt_extra)


if __name__ == "__main__":
    sys.exit(main())
