"""CLI tools for QuantLib market context and bond lookup."""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from langchain_core.tools import StructuredTool

from cheapquant_fi.bond_manager import BondManager
from cheapquant_fi.quantlib.quantlib_market_context_manager import (
    QuantlibMarketContextManager,
)

_MENTION_RE = re.compile(r"@([A-Za-z0-9][\w-]*)")


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


def resolve_bond_mentions(text: str) -> tuple[str, list[str]]:
    """Resolve `@user_friendly_id` mentions and inject resolved bond context.

    For each `@token` found in text:
    - If BondManager resolves it, strip the `@` from the visible text and
      collect bond details into a context block.
    - If not found, leave it unchanged and record in unresolved list.

    Returns (rewritten_text, unresolved_ids). If any bonds resolved, the
    rewritten text includes a preamble with their details for LLM context.

    Args:
        text: Input query possibly containing `@mention` tokens.

    Returns:
        Tuple of (modified text with `@` stripped and context appended, list of unresolved ids).
    """
    manager = BondManager.instance()
    resolved: dict[str, dict] = {}
    unresolved: list[str] = []
    visible_text = text

    for match in _MENTION_RE.finditer(text):
        token = match.group(1)
        bond = manager.get(token)
        if bond is not None:
            resolved[token] = bond.as_dict()
            visible_text = visible_text.replace(f"@{token}", token)
        else:
            unresolved.append(token)

    if not resolved:
        return text, unresolved

    context_block = f"Context — resolved bond mentions: {json.dumps(resolved)}\n\n"
    return context_block + visible_text, unresolved


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


# Real, executable LangChain tools — bound directly into SQLAgent/LLMPlanner via
# extra_tools so the LLM can genuinely call get_bond / check_market_context (not
# just read a text description of them). Schemas are derived from the Google-style
# docstrings and type hints on the plain functions above via parse_docstring=True.
get_bond_lc_tool = StructuredTool.from_function(
    func=get_bond,
    name="get_bond",
    parse_docstring=True,
)

check_market_context_lc_tool = StructuredTool.from_function(
    func=check_market_context,
    name="check_market_context",
    parse_docstring=True,
)
