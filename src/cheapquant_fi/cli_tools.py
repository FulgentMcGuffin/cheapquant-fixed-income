"""CLI tools for QuantLib market context, bond lookup, and bond analytics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from langchain_core.tools import StructuredTool

from cheapquant_fi.analytics_input import BondAnalyticsInput, CmtAnalyticsInput
from cheapquant_fi.bond_future_input import BondFutureInput
from cheapquant_fi.bond_futures import (
    BondFuture,
    resolve_bond_future_convention,
    resolve_delivery_month,
)
from cheapquant_fi.bond_manager import BondManager
from cheapquant_fi.composite_tenor import CompositeTenor
from cheapquant_fi.data.rates_loader import list_available_dates
from cheapquant_fi.delivery_basket import (
    DeliveryBasket,
    DeliveryBasketManager,
    parse_dlv_command,
    parse_fut_command,
    resolve_basket,
)
from cheapquant_fi.issuers import resolve_issuer
from cheapquant_fi.numeric_term_structure import NumericTermStructure
from cheapquant_fi.quantlib.quantlib_analytics_calculator import (
    QuantLibAnalyticsCalculator,
)
from cheapquant_fi.quantlib.quantlib_bond_future_calculator import (
    QuantLibBondFutureCalculator,
)
from cheapquant_fi.quantlib.quantlib_market_context import QuantlibMarketContext
from cheapquant_fi.quantlib.quantlib_market_context_manager import (
    QuantlibMarketContextManager,
)

_MENTION_RE = re.compile(r"@([A-Za-z0-9][\w-]*)")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CALC_BOND_RE = re.compile(
    r"^/calc\s+@?(?P<bond_id>\S+)"
    r"(?:\s+(?P<trade_date>\d{4}-\d{2}-\d{2}))?"
    r"(?:\s+(?P<curve_label>\S+))?"
    r"(?:\s+(?P<numeric_term_structure>\{.*\}))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CalcParseResult:
    """Parsed ``/calc`` slash command."""

    kind: Literal["help", "invalid", "bond", "cmt"]
    bond_id: str | None = None
    trade_date: str | None = None
    curve_label: str = "BOND_ZERO"
    numeric_term_structure: dict[str, float] | None = None
    issuer: str | None = None
    composite_tenor: str | None = None


def _try_parse_cmt_calc_tokens(tokens: list[str]) -> dict[str, str | None] | None:
    """Return CMT calc kwargs when *tokens* are ``issuer tenor [date]``."""
    if len(tokens) not in (2, 3):
        return None

    issuer_str, tenor_str = tokens[0], tokens[1]
    trade_date: str | None = None
    if len(tokens) == 3:
        if not _ISO_DATE_RE.match(tokens[2]):
            return None
        trade_date = tokens[2]
    elif _ISO_DATE_RE.match(tenor_str):
        return None

    try:
        issuer = resolve_issuer(issuer_str)
        CompositeTenor.from_combined_tenor(issuer.source_code, tenor_str)
    except ValueError:
        return None

    return {
        "issuer": issuer.source_code,
        "composite_tenor": tenor_str,
        "trade_date": trade_date,
    }


def parse_calc_command(text: str) -> CalcParseResult | None:
    """Parse ``/calc`` into bond, CMT, help, or invalid."""
    stripped = text.strip()
    if not re.match(r"^/calc\b", stripped, re.IGNORECASE):
        return None
    if re.match(r"^/calc\s*$", stripped, re.IGNORECASE):
        return CalcParseResult(kind="help")

    body = re.sub(r"^/calc\s+", "", stripped, count=1, flags=re.IGNORECASE).strip()
    cmt = _try_parse_cmt_calc_tokens(body.split())
    if cmt is not None:
        return CalcParseResult(
            kind="cmt",
            issuer=cmt["issuer"],
            composite_tenor=cmt["composite_tenor"],
            trade_date=cmt["trade_date"],
        )

    bond_match = _CALC_BOND_RE.match(stripped)
    if bond_match:
        numeric_term_structure = None
        numeric_term_structure_str = bond_match.group("numeric_term_structure")
        if numeric_term_structure_str:
            try:
                numeric_term_structure = eval(numeric_term_structure_str)
            except Exception:
                return CalcParseResult(kind="invalid")
        return CalcParseResult(
            kind="bond",
            bond_id=bond_match.group("bond_id").strip(),
            trade_date=(
                bond_match.group("trade_date").strip()
                if bond_match.group("trade_date")
                else None
            ),
            curve_label=bond_match.group("curve_label") or "BOND_ZERO",
            numeric_term_structure=numeric_term_structure,
        )

    return CalcParseResult(kind="invalid")


def _resolve_trade_date_for_issuer(
    issuer_code: str,
    trade_date: str | None,
) -> tuple[date | None, dict | None]:
    """Resolve trade date from explicit value or latest zero_rates row."""
    if trade_date is not None:
        try:
            return date.fromisoformat(trade_date.strip()), None
        except ValueError as exc:
            return None, {
                "status": "error",
                "issuer": issuer_code,
                "message": f"Invalid trade_date {trade_date!r}: {exc}",
            }

    from cheapquant_fi.config import get_settings

    issuer = resolve_issuer(issuer_code)
    dates_df = list_available_dates(get_settings().ycs_db_path, issuer)
    if dates_df.is_empty():
        return None, {
            "status": "error",
            "issuer": issuer_code,
            "message": f"No rates available for issuer {issuer_code!r}",
        }
    resolved = dates_df["date"][-1]
    if isinstance(resolved, str):
        resolved = date.fromisoformat(resolved)
    elif isinstance(resolved, datetime):
        resolved = resolved.date()
    return resolved, None


def execute_parsed_calc(parsed: CalcParseResult) -> dict:
    """Run bond or CMT analytics for a parsed ``/calc`` command."""
    if parsed.kind == "cmt":
        assert parsed.issuer is not None and parsed.composite_tenor is not None
        return compute_cmt_analytics(
            parsed.issuer,
            parsed.composite_tenor,
            trade_date=parsed.trade_date,
            curve_label=parsed.curve_label,
        )
    if parsed.kind == "bond":
        assert parsed.bond_id is not None
        return compute_bond_analytics(
            parsed.bond_id,
            trade_date=parsed.trade_date,
            curve_label=parsed.curve_label,
            numeric_term_structure=parsed.numeric_term_structure,
        )
    raise ValueError(f"Cannot execute calc command of kind {parsed.kind!r}")


def format_calc_result(result: dict) -> str:
    """Render a bond/CMT analytics tool result for CLI/GUI output."""
    if result.get("status") == "success":
        return result["analytics_json"]
    return f"Error: {result.get('message', result)}"


def execute_calc_command(text: str) -> tuple[CalcParseResult, dict] | None:
    """Parse and execute ``/calc`` when it is a bond or CMT analytics command."""
    parsed = parse_calc_command(text)
    if parsed is None or parsed.kind in ("help", "invalid"):
        return None
    return parsed, execute_parsed_calc(parsed)


def _market_context_for(
    as_of: date,
    issuer: str,
    curve_label: str,
) -> QuantlibMarketContext | None:
    """Return the market context for *as_of*, building the issuer's curve first.

    ``QuantlibMarketContextManager.get`` returns that issuer's *curve handle*
    when an issuer is supplied, not the context, so the context itself has to
    be fetched in a second call without one.

    Args:
        as_of: Valuation date.
        issuer: Issuer code whose curve must exist.
        curve_label: Curve collection label.

    Returns:
        The context, or ``None`` when the issuer's curve cannot be built.
    """
    manager = QuantlibMarketContextManager.instance()
    if manager.get(as_of, issuer, curve_label) is None:
        return None
    return manager.get(as_of)


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
        trade_date, date_error = _resolve_trade_date_for_issuer(bond.issuer, trade_date)
        if date_error is not None:
            date_error["bond_id"] = bond_id
            return date_error
        assert trade_date is not None

        # Parse numeric term structure
        repo_term_structure = None
        if numeric_term_structure:
            try:
                repo_term_structure = NumericTermStructure(
                    numeric_term_structure, trade_date
                )
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
            market_context = _market_context_for(trade_date, bond.issuer, curve_label)
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
            analytics_output, _cmt_metrics, _fc_cmt_metrics = (
                calculator.compute_bond_analytics(
                    analytics_input,
                    market_context,
                    curve_label=curve_label,
                )
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


def compute_cmt_analytics(
    issuer: str,
    composite_tenor: str,
    trade_date: str | None = None,
    curve_label: str = "BOND_ZERO",
) -> dict:
    """Compute analytics for a forward-starting constant-maturity treasury.

    Args:
        issuer: Issuer code or alias (e.g. ``DEU``, ``FRA``, ``usa``).
        composite_tenor: Combined tenor string (e.g. ``5y``, ``10y2y``, ``18m4w9m4d``).
        trade_date: Anchor trade date in YYYY-MM-DD format. Defaults to the latest
            date available in ``zero_rates`` for the issuer.
        curve_label: Curve collection label (``BOND_ZERO`` or ``BOND_PAR``).

    Returns:
        Dictionary with status and :class:`FixedIncomeAnalyticsOutput` JSON when successful.
    """
    try:
        issuer_profile = resolve_issuer(issuer)
        issuer_code = issuer_profile.source_code

        resolved_date, date_error = _resolve_trade_date_for_issuer(
            issuer_code, trade_date
        )
        if date_error is not None:
            return date_error
        assert resolved_date is not None

        try:
            request = CmtAnalyticsInput.from_string(
                issuer_code,
                composite_tenor,
                trade_date=resolved_date,
            )
        except Exception as exc:
            return {
                "status": "error",
                "issuer": issuer_code,
                "composite_tenor": composite_tenor,
                "message": f"Invalid composite tenor {composite_tenor!r}: {exc}",
            }

        try:
            market_context = _market_context_for(
                resolved_date, issuer_code, curve_label
            )
            if market_context is None:
                return {
                    "status": "error",
                    "issuer": issuer_code,
                    "composite_tenor": str(request.composite_tenor),
                    "date": resolved_date.isoformat(),
                    "curve_label": curve_label,
                    "message": (
                        f"No market context available for {issuer_code} on "
                        f"{resolved_date} with curve {curve_label}"
                    ),
                }
        except Exception as exc:
            return {
                "status": "error",
                "issuer": issuer_code,
                "message": f"Failed to get market context: {exc}",
            }

        try:
            calculator = QuantLibAnalyticsCalculator()
            analytics_output = calculator.compute_cmt_analytics(
                request,
                market_context,
                curve_label=curve_label,
            )
        except Exception as exc:
            return {
                "status": "error",
                "issuer": issuer_code,
                "composite_tenor": str(request.composite_tenor),
                "message": f"Failed to compute CMT analytics: {exc}",
            }

        return {
            "status": "success",
            "issuer": issuer_code,
            "composite_tenor": str(request.composite_tenor),
            "date": resolved_date.isoformat(),
            "curve_label": curve_label,
            "analytics_json": analytics_output.as_json(indent=2),
            "analytics": analytics_output.as_dict(),
            "message": (
                f"CMT analytics computed for {str(request.composite_tenor)!r} "
                f"on {resolved_date}"
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "message": f"Failed to compute CMT analytics: {exc}",
        }


def build_delivery_basket(
    name: str,
    future_code: str,
    delivery: str | None = None,
    bond_ids: list[str] | None = None,
) -> dict:
    """Build and store a named delivery basket for a bond future contract.

    Args:
        name: Name to store the basket under, for later use by /fut.
        future_code: Bond future code such as "FBTP", "IK", "FGBM" or "IKU9".
        delivery: Optional delivery month: "M8" (June 2028), "U" (next
            September), "6" (next quarterly month in a year ending in 6),
            "2020-09" or "U2020". Defaults to the front quarterly contract.
        bond_ids: Optional explicit deliverable bonds, each a user_friendly_id
            or bond_id, optionally suffixed with "|<conversion factor>" to
            hard-code the factor. When omitted the basket is populated from
            every eligible bond in bond_universe.

    Returns:
        Dictionary with status, the resolved contract and the deliverable bonds.
    """
    try:
        convention = resolve_bond_future_convention(future_code)
        year, month = resolve_delivery_month(delivery)
        future = BondFuture(
            convention=convention, delivery_month=month, delivery_year=year
        )

        if bond_ids:
            specs: list[tuple[str, float | None]] = []
            for token in bond_ids:
                identifier, _, factor = str(token).partition("|")
                specs.append((identifier, float(factor) if factor else None))
            basket = DeliveryBasket.from_bond_ids(future, specs, name=name)
        else:
            basket = DeliveryBasket.auto(future, name=name)

        DeliveryBasketManager.instance().put(name, basket)
        return {
            "status": "success",
            "name": name,
            "contract": str(future),
            "delivery_date": future.delivery_end_date().isoformat(),
            "bond_count": len(basket),
            "basket_json": basket.as_json(indent=2),
            "dataframe": basket.to_polars(),
            "message": (
                f"Basket {name!r} holds {len(basket)} bond(s) deliverable into "
                f"{future} on {future.delivery_end_date()}"
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "name": name,
            "error": str(exc),
            "message": f"Failed to build delivery basket: {exc}",
        }


def compute_bond_future_analytics(
    target: str,
    trade_date: str | None = None,
    futures_price: float | None = None,
    curve_label: str = "BOND_ZERO",
    numeric_term_structure: dict[str, float] | None = None,
) -> dict:
    """Compute basis analytics for a bond future delivery basket.

    Returns the conversion factor, implied repo rate, gross and net basis,
    delta, gamma and implied fair futures price for every deliverable bond,
    ordered cheapest-to-deliver first.

    Args:
        target: A basket name stored earlier by /dlv, or a bond future code
            such as "IKH7" to build the basket on the fly.
        trade_date: Analytics date in "YYYY-MM-DD" format. Defaults to the
            latest trade date held in bond_analytics for the issuer.
        futures_price: Observed futures price. When omitted the price is
            implied so the cheapest-to-deliver bond's net basis is zero.
        curve_label: Curve collection label. Defaults to "BOND_ZERO".
        numeric_term_structure: Optional repo curve mapping tenor labels such
            as "3m" to rates in percent.

    Returns:
        Dictionary with status and the per-bond analytics when successful.
    """
    try:
        basket = resolve_basket(target)
        issuer = basket.bond_future.convention.issuer_code

        resolved_date, date_error = _resolve_bond_future_trade_date(issuer, trade_date)
        if date_error is not None:
            date_error["target"] = target
            return date_error

        repo_term_structure = None
        if numeric_term_structure:
            repo_term_structure = NumericTermStructure(
                numeric_term_structure, resolved_date
            )

        market_context = _market_context_for(resolved_date, issuer, curve_label)
        if market_context is None:
            return {
                "status": "error",
                "target": target,
                "message": (
                    f"No market context available for {issuer} on "
                    f"{resolved_date} with curve {curve_label}"
                ),
            }

        request = BondFutureInput.from_basket(
            basket,
            resolved_date,
            repo_term_structure=repo_term_structure,
            curve_label=curve_label,
            futures_price=futures_price,
        )
        result = QuantLibBondFutureCalculator().compute_bond_future_analytics(
            request, market_context, curve_label=curve_label
        )
        return {
            "status": "success",
            "target": target,
            "contract": str(basket.bond_future),
            "date": resolved_date.isoformat(),
            "curve_label": curve_label,
            "futures_price": result.futures_price,
            "futures_price_is_implied": result.futures_price_is_implied,
            "ctd": result.ctd().bond.user_friendly_id or result.ctd().bond.bond_id,
            "analytics_json": result.as_json(indent=2),
            "dataframe": result.to_polars(),
            "message": (
                f"{len(result)} deliverable bond(s) for {basket.bond_future} on "
                f"{resolved_date}; CTD net basis "
                f"{result.ctd().net_basis:.6f}"
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "target": target,
            "error": str(exc),
            "message": f"Failed to compute bond future analytics: {exc}",
        }


def _resolve_bond_future_trade_date(
    issuer: str, trade_date: str | None
) -> tuple[date | None, dict | None]:
    """Resolve an explicit trade date, or fall back to the latest analytics date."""
    if trade_date:
        if not _ISO_DATE_RE.match(trade_date):
            return None, {
                "status": "error",
                "message": f"Invalid trade_date {trade_date!r}; expected YYYY-MM-DD",
            }
        return date.fromisoformat(trade_date), None

    latest = BondManager.instance().latest_analytics_trade_date(issuer)
    if latest is None:
        return None, {
            "status": "error",
            "message": (
                f"No trade date held in bond_analytics for {issuer}; "
                "supply one explicitly"
            ),
        }
    return latest, None


def execute_dlv_command(text: str) -> dict | None:
    """Parse and execute ``/dlv`` when it describes a basket to build."""
    parsed = parse_dlv_command(text)
    if parsed is None or parsed.kind in ("help", "invalid"):
        return None
    return build_delivery_basket(
        parsed.name,
        parsed.future_code,
        delivery=parsed.delivery_token,
        bond_ids=[
            identifier if factor is None else f"{identifier}|{factor}"
            for identifier, factor in parsed.bond_specs
        ]
        or None,
    )


def execute_fut_command(text: str) -> dict | None:
    """Parse and execute ``/fut`` when it requests analytics."""
    parsed = parse_fut_command(text)
    if parsed is None or parsed.kind in ("help", "invalid"):
        return None
    return compute_bond_future_analytics(
        parsed.target,
        trade_date=parsed.trade_date.isoformat() if parsed.trade_date else None,
    )


def format_dlv_result(result: dict) -> str:
    """Render a delivery basket tool result for CLI output."""
    if result.get("status") != "success":
        return f"Error: {result.get('message', result)}"
    return f"{result['message']}\n\n{result['dataframe']}"


def format_fut_result(result: dict) -> str:
    """Render a bond future analytics tool result for CLI output."""
    if result.get("status") != "success":
        return f"Error: {result.get('message', result)}"
    implied = " (implied)" if result["futures_price_is_implied"] else ""
    return (
        f"{result['contract']} on {result['date']} — futures price "
        f"{result['futures_price']:.6f}{implied}, CTD {result['ctd']}\n\n"
        f"{result['dataframe']}"
    )


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

compute_cmt_analytics_lc_tool = StructuredTool.from_function(
    func=compute_cmt_analytics,
    name="compute_cmt_analytics",
    parse_docstring=True,
)

build_delivery_basket_lc_tool = StructuredTool.from_function(
    func=build_delivery_basket,
    name="build_delivery_basket",
    parse_docstring=True,
)

compute_bond_future_analytics_lc_tool = StructuredTool.from_function(
    func=compute_bond_future_analytics,
    name="compute_bond_future_analytics",
    parse_docstring=True,
)
