"""CLI tools for QuantLib market context and bond lookup."""

from __future__ import annotations

import json
from datetime import date, datetime

from cheapquant_fi.bond_manager import BondManager
from cheapquant_fi.quantlib.quantlib_market_context_manager import (
    QuantlibMarketContextManager,
)


def check_market_context(
    as_of: str,
    issuer: str | None = None,
    curve_label: str = "BOND_ZERO",
) -> dict:
    """Check if a QuantlibMarketContext exists and create it if missing.

    Args:
        as_of: Valuation date in "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS" format.
        issuer: Optional issuer code (e.g., "USA", "DEU"). Defaults to None (full context).
        curve_label: Curve collection label. Defaults to "BOND_ZERO".

    Returns:
        Dictionary with status and details about the market context.
    """
    try:
        # Parse the date/datetime string
        as_of_value: date | datetime
        try:
            as_of_value = datetime.strptime(as_of, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            as_of_value = datetime.strptime(as_of, "%Y-%m-%d").date()

        # Get or create the market context
        manager = QuantlibMarketContextManager.instance()
        has_context = manager.has_market_context(as_of_value, issuer, curve_label)

        # If not found, try to get/build it (which will create it if possible)
        if not has_context:
            context = manager.get(as_of_value, issuer, curve_label)
            has_context = context is not None

        date_str = (
            as_of_value.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(as_of_value, datetime)
            else as_of_value.strftime("%Y-%m-%d")
        )

        return {
            "status": "success",
            "date": date_str,
            "issuer": issuer or "(all)",
            "curve_label": curve_label,
            "has_context": has_context,
            "message": (
                f"Market context {'exists' if has_context else 'not found'} "
                f"for {date_str} issuer={issuer or 'all'} curve={curve_label}"
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "message": f"Failed to check market context: {exc}",
        }


def get_bond(bond_id: str) -> dict:
    """Load a :class:`Bond` by ``user_friendly_id`` or ``bond_id``.

    Args:
        bond_id: Identifier matching a row in ``bond_universe``.

    Returns:
        Dictionary with status and bond JSON when found.
    """
    try:
        key = bond_id.strip()
        if not key:
            return {
                "status": "error",
                "message": "Bond id is required",
            }

        bond = BondManager.instance().get(key)
        if bond is None:
            return {
                "status": "not_found",
                "id": key,
                "message": f"No bond found for id {key!r}",
            }

        return {
            "status": "success",
            "id": key,
            "bond_id": bond.bond_id,
            "user_friendly_id": bond.user_friendly_id,
            "bond_json": bond.as_json(indent=2),
            "bond": json.loads(bond.as_json()),
            "message": f"Bond loaded for id {key!r}",
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "message": f"Failed to load bond: {exc}",
        }


def get_mctx_tool_definition() -> dict:
    """Return JSON schema for the market context tool for LLM use."""
    return {
        "name": "check_market_context",
        "description": (
            "Check if a QuantLib market context exists for a given date, issuer, and curve. "
            "If it doesn't exist, create it and make it available to the manager. "
            "Returns whether the context exists or was successfully created."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "as_of": {
                    "type": "string",
                    "description": "Valuation date in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format",
                    "examples": ["2022-02-17", "2024-02-15", "2025-11-18 10:30:45"],
                },
                "issuer": {
                    "type": "string",
                    "description": (
                        "Optional issuer code (e.g., USA, DEU, FRA, GBR, JPN, etc.). "
                        "If omitted, checks/creates full market context."
                    ),
                    "examples": ["USA", "DEU", "FRA"],
                },
                "curve_label": {
                    "type": "string",
                    "description": "Curve collection label (BOND_ZERO or BOND_PAR)",
                    "enum": ["BOND_ZERO", "BOND_PAR"],
                    "default": "BOND_ZERO",
                },
            },
            "required": ["as_of"],
        },
    }


def get_bond_tool_definition() -> dict:
    """Return JSON schema for the bond lookup tool for LLM use."""
    return {
        "name": "get_bond",
        "description": (
            "Load a bond from bond_universe by user_friendly_id or bond_id and "
            "return its fields as JSON. Creates a cached Bond instance when found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bond_id": {
                    "type": "string",
                    "description": (
                        "Bond identifier: user_friendly_id (e.g. usa10y001) or "
                        "bond_id / ISIN (e.g. US0001)."
                    ),
                    "examples": ["usa10y001", "US0001", "deu10y001"],
                },
            },
            "required": ["bond_id"],
        },
    }
