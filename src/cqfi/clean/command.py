"""Parsing and execution for the ``/clean`` slash command."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from cqfi.clean.bonds import clean_bond_analytics_duplicates
from cqfi.config import AppSettings, get_settings

_CLEAN_HELP_RE = re.compile(r"^/clean\s*$", re.IGNORECASE)
_CLEAN_BONDS_RE = re.compile(r"^/clean\s+bonds\s*$", re.IGNORECASE)

CLEAN_HELP_TEXT = (
    "The /clean command removes redundant rows from bond_analytics_db.\n\n"
    "  /clean bonds  — for each (bond_id, trade_date) in bond_analytics, delete\n"
    "                  older duplicates and keep the row with the latest created_at\n\n"
    "More targets may be added later. Works in the CLI REPL, one-shot queries,\n"
    "and GUI chat (including LLM mode)."
)


@dataclass(frozen=True)
class CleanCommandResult:
    """Parsed ``/clean`` slash command."""

    kind: Literal["help", "run_bonds", "invalid"]
    message: str | None = None


def parse_clean_command(text: str) -> CleanCommandResult | None:
    """Parse ``/clean``. ``None`` if *text* is not a ``/clean`` command."""
    stripped = text.strip()
    if not re.match(r"^/clean\b", stripped, re.IGNORECASE):
        return None
    if _CLEAN_HELP_RE.match(stripped):
        return CleanCommandResult(kind="help")
    if _CLEAN_BONDS_RE.match(stripped):
        return CleanCommandResult(kind="run_bonds")
    return CleanCommandResult(
        kind="invalid",
        message="Only /clean bonds is supported for now.",
    )


def handle_clean_command(text: str) -> str | None:
    """Return help/invalid text for ``/clean``; ``None`` if not a clean command."""
    parsed = parse_clean_command(text)
    if parsed is None:
        return None
    if parsed.kind == "help":
        return CLEAN_HELP_TEXT
    if parsed.kind == "invalid":
        return f"Invalid /clean command. {parsed.message}\n\n{CLEAN_HELP_TEXT}"
    return None


def execute_clean_bonds(settings: AppSettings | None = None) -> str:
    """Run ``/clean bonds`` against the session ``bond_analytics_db``."""
    settings = settings or get_settings()
    result = clean_bond_analytics_duplicates(
        settings.bond_analytics_db_path,
        settings.bond_analytics_semantics_path,
    )
    if not result.changed:
        return (
            f"No duplicate bond_analytics rows found "
            f"({result.rows_before} row(s) in bond_analytics_db)."
        )
    return (
        f"Removed {result.duplicates_removed} duplicate bond_analytics row(s) "
        f"across {result.duplicate_groups} bond/trade_date group(s). "
        f"{result.rows_before} -> {result.rows_after} row(s) remaining."
    )
