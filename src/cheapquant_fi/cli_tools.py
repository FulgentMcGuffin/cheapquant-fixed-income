"""CLI tools for QuantLib market context, bond lookup, and bond analytics."""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from langchain_core.tools import StructuredTool

from cheapquant_fi.analytics_input import BondAnalyticsInput
from cheapquant_fi.bond_manager import BondManager
from cheapquant_fi.data.rates_loader import list_available_dates
from cheapquant_fi.issuers import resolve_issuer
from cheapquant_fi.numeric_term_structure import NumericTermStructure
from cheapquant_fi.quantlib.quantlib_analytics_calculator import (
    QuantLibAnalyticsCalculator,
)
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


def compute_bond_analytics(
    bond_id: str,
    trade_date: str | None = None,
    curve_label: str = "BOND_ZERO",
    numeric_term_structure: dict[str, float] | None = None,
) -> dict:
    """Compute analytics for a bond.

    Args:
        bond_id: Bond identifier (user_friendly_id or bond_id).
        trade_date: Valuation date in YYYY-MM-DD format. Defaults to latest date
            in zero_rates table for the bond's issuer.
        curve_label: Curve collection label (BOND_ZERO or BOND_PAR).
            Defaults to BOND_ZERO.
        numeric_term_structure: Optional repo term structure as dictionary of tenor
            strings to float rates.

    Returns:
        Dictionary with status and FixedIncomeAnalyticsOutput JSON when successful.
    """
    try:
        # Load bond
        bond = BondManager.instance().get(bond_id.strip())
        if bond is None:
            return {
                "status": "not_found",
                "bond_id": bond_id,
                "message": f"No bond found for id {bond_id!r}",
            }

        # Resolve trade date
        if trade_date is None:
            from cheapquant_fi.config import get_settings
            issuer = resolve_issuer(bond.issuer)
            dates_df = list_available_dates(
                get_settings().ycs_db_path,
                issuer,
            )
            if dates_df.is_empty():
                return {
                    "status": "error",
                    "bond_id": bond_id,
                    "issuer": bond.issuer,
                    "message": f"No rates available for issuer {bond.issuer!r}",
                }
            # Get latest date
            trade_date = dates_df["date"][-1]
        else:
            trade_date = date.fromisoformat(trade_date.strip())

        # Parse numeric term structure
        repo_term_structure = None
        if numeric_term_structure:
            try:
                repo_term_structure = NumericTermStructure(numeric_term_structure)
            except Exception as e:
                return {
                    "status": "error",
                    "bond_id": bond_id,
                    "message": f"Invalid numeric_term_structure: {e}",
                }

        # Create BondAnalyticsInput
        try:
            analytics_input = BondAnalyticsInput.from_bond(
                bond,
                trade_date=trade_date,
                repo_term_structure=repo_term_structure,
            )
        except Exception as e:
            return {
                "status": "error",
                "bond_id": bond_id,
                "message": f"Failed to create analytics input: {e}",
            }

        # Get market context
        try:
            manager = QuantlibMarketContextManager.instance()
            market_context = manager.get(trade_date, bond.issuer, curve_label)
            if market_context is None:
                return {
                    "status": "error",
                    "bond_id": bond_id,
                    "date": trade_date.isoformat(),
                    "issuer": bond.issuer,
                    "curve_label": curve_label,
                    "message": f"No market context available for {bond.issuer} on {trade_date} with curve {curve_label}",
                }
        except Exception as e:
            return {
                "status": "error",
                "bond_id": bond_id,
                "message": f"Failed to get market context: {e}",
            }

        # Compute analytics
        try:
            calculator = QuantLibAnalyticsCalculator()
            analytics_output, _cmt_metrics = calculator.compute_bond_analytics(
                analytics_input,
                market_context,
                curve_label=curve_label,
            )
        except Exception as e:
            return {
                "status": "error",
                "bond_id": bond_id,
                "message": f"Failed to compute analytics: {e}",
            }

        return {
            "status": "success",
            "bond_id": bond_id,
            "user_friendly_id": bond.user_friendly_id,
            "date": trade_date.isoformat(),
            "curve_label": curve_label,
            "analytics_json": analytics_output.as_json(indent=2),
            "analytics": analytics_output.as_dict(),
            "message": f"Analytics computed for {bond_id!r} on {trade_date}",
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "message": f"Failed to compute analytics: {exc}",
        }


# Real, executable LangChain tools — bound directly into SQLAgent/LLMPlanner via
# extra_tools so the LLM can genuinely call these functions (not just read a text
# description of them). Schemas are derived from the Google-style docstrings and
# type hints on the plain functions above via parse_docstring=True.
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

compute_bond_analytics_lc_tool = StructuredTool.from_function(
    func=compute_bond_analytics,
    name="compute_bond_analytics",
    parse_docstring=True,
)
