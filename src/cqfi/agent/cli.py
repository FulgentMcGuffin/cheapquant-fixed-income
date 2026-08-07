"""Unified text interface for ycs_data queries and cached analytics."""

from __future__ import annotations

# Must run before any langchain import (pulled in via mcp_data).
from cqfi.config import (  # noqa: F401
    DEFAULT_CONFIG_PATH,
    AppSettings,
    configure_langsmith,
    get_runtime_settings,
    get_settings,
    load_runtime_settings,
    load_settings,
    save_runtime_settings,
)

configure_langsmith()

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl
from mcp_data.client.planner import HELP_TEXT, LLMPlanner, Planner, ToolCall
from mcp_data.client.session import DBClient
from mcp_data.config import Settings as MCPSettings

if TYPE_CHECKING:
    from mcp_data.client.agent import SQLAgent

from cqfi.agent.planner import (
    CQFIRulePlanner,
    RULE_MODE_HINT,
    resolve_query_mode,
)
from cqfi.batch.cli import run_future_gui_standalone, run_gui_standalone
from cqfi.batch.command import (
    BatchCommandResult,
    build_batch_launch_request,
    build_future_batch_launch_request,
    parse_batch_command,
)
from cqfi.clean.command import (
    execute_clean_bonds,
    handle_clean_command,
    parse_clean_command,
)
from cqfi.cache.manager import CacheManager
from cqfi.cli_tools import (
    build_delivery_basket,
    build_delivery_basket_lc_tool,
    check_market_context,
    check_market_context_lc_tool,
    compute_bond_analytics,
    compute_bond_analytics_lc_tool,
    compute_bond_future_analytics,
    compute_bond_future_analytics_lc_tool,
    compute_cmt_analytics,
    compute_cmt_analytics_lc_tool,
    execute_dlv_command,
    execute_fut_command,
    execute_parsed_calc,
    format_calc_result,
    format_dlv_result,
    format_fut_result,
    get_bond,
    get_bond_lc_tool,
    parse_calc_command,
    resolve_bond_mentions,
)
from cqfi.delivery_basket import parse_dlv_command, parse_fut_command

# Real, executable LangChain tools bound into SQLAgent/LLMPlanner alongside the
# built-in SQL tools, so the LLM can genuinely call them (not just read a text
# description) -- see cqfi.cli_tools.
EXTRA_TOOLS = [
    get_bond_lc_tool,
    check_market_context_lc_tool,
    compute_bond_analytics_lc_tool,
    compute_cmt_analytics_lc_tool,
    build_delivery_basket_lc_tool,
    compute_bond_future_analytics_lc_tool,
]

# Tool names handled locally by _run_tool_calls rather than by the MCP server.
LOCAL_TOOL_NAMES = [
    "check_market_context",
    "get_bond",
    "compute_bond_analytics",
    "compute_cmt_analytics",
    "build_delivery_basket",
    "compute_bond_future_analytics",
]


@dataclass(frozen=True)
class RoutedQuery:
    target: str
    text: str


HELP_TEXT_CQFI = (
    "cqfi agent\n"
    "=============================\n"
    "\n"
    "Query datasets (prefix optional — auto-routed when obvious):\n"
    "  input: <question>          — read-only questions about yield curves in ycs_data.duckdb/sqlite\n"
    "  cache: <question>          — questions about cached QuantLib results\n"
    "  bond_analytics: <question> — bond_universe / bond_analytics / cmt_analytics questions\n"
    "\n"
    "Pricing commands:\n"
    "  price cmt <issuer> <YYYY-MM-DD> [--par]  — price CMTs (USA, DEU, …)\n"
    "\n"
    "Market context commands:\n"
    "  /mctx <YYYY-MM-DD> [issuer] [curve]  — check/create market context\n"
    "  /mctx <YYYY-MM-DD HH:MM:SS> [issuer] [curve]  — with time precision\n"
    "    Examples: /mctx 2022-02-17 FRA BOND_ZERO\n"
    "              /mctx 2024-02-15 USA\n"
    "              /mctx 2025-11-18\n"
    '    Also available in LLM mode: "Is there a market for France on 17 Feb 2022?"\n'
    "\n"
    "Bond commands:\n"
    "  /bond <id>  — show bond_universe row as JSON (user_friendly_id or bond_id)\n"
    "    Examples: /bond usa10y001\n"
    "              /bond US0001\n"
    '    Also available in LLM mode: "Show bond usa10y001 as JSON", "what\'s the\n'
    '    duration of fraapr029?"\n'
    "\n"
    "Quant cache commands:\n"
    "  /cache on   — enable writing analytics to quant_cache_db\n"
    "  /cache off  — disable quant cache writes\n"
    "  /cache      — show help and current use_quant_cache setting\n"
    "\n"
    "Quant cache session-end commands:\n"
    "  /save_cache on   — on exit, merge quant_cache_db analytics into bond_analytics_db\n"
    "  /save_cache off  — on exit, discard quant_cache_db analytics without merging\n"
    "  /save_cache      — show help and current save_quant_cache_to_bond_analytics_after_session setting\n"
    "\n"
    "Database cleanup:\n"
    "  /clean bonds  — remove duplicate bond_analytics rows (keep latest created_at\n"
    "                  per bond_id and trade_date)\n"
    "  /clean        — show /clean help\n"
    "\n"
    "Bond analytics commands:\n"
    "  /calc <bond_id> [date] [curve] [term_structure]  — compute bond analytics\n"
    "  /calc <issuer> <composite_tenor> [date]          — compute CMT analytics\n"
    "    bond id: bond_friendly_id or bond_id\n"
    "    issuer + composite_tenor: e.g. DEU 5y, FRA 10y2y, DEU 18m4w9m4d\n"
    "    date: YYYY-MM-DD (optional, defaults to latest zero_rates date)\n"
    "    curve: curve label for bond calc (optional, defaults to BOND_ZERO)\n"
    "    term_structure: JSON dict of tenors to rates (optional, bond calc only)\n"
    "    Examples: /calc fraapr029\n"
    "              /calc usa10y001 2024-02-15\n"
    "              /calc DEU 5y\n"
    "              /calc FRA 10y2y 2024-02-15\n"
    '              /calc @fraapr029 2024-02-15 BOND_ZERO {"1m": 2.1, "3m": 2.15}\n'
    '    Also available in LLM mode: "Calculate analytics for fraapr029"\n'
    "\n"
    "Bond future commands:\n"
    "  /dlv <name> <future> [bonds...] [delivery]        — build a delivery basket\n"
    "  /fut <basket|contract> [date] [repo]              — basis analytics, CTD first\n"
    "    future: exchange code or Bloomberg root (FGBM, OE, IK, ZN, TU, …)\n"
    "    delivery: M8, U, 6, 2020-09 or U2020 (defaults to front quarterly)\n"
    "    bonds: '<id>' or '<id>|<conversion_factor>' to hard-code a factor\n"
    "    repo: applied to every bond in the basket. A number is a flat repo\n"
    "          rate held for every tenor (i.e. for eternity); a JSON object\n"
    "          maps tenor labels to rates in percent, e.g. {\"3m\": 3.0, \"1y\": 3.2}.\n"
    "          Defaults to the discount curve's own forward rate when omitted.\n"
    "    Examples: /dlv mybasket FGBM\n"
    "              /dlv hist FGBS 2020-09\n"
    "              /dlv mine FOA fraapr029|1.0326 frajun030|1.0291 2025-12\n"
    "              /fut IKH7\n"
    "              /fut mybasket 2025-10-15\n"
    "              /fut IKH7 2026-05-15 3.0\n"
    '              /fut IKH7 2026-05-15 {"3m": 3.0, "1y": 3.2}\n'
    '    Also available in LLM mode: "What is the CTD for the March 2027 BTP future?"\n'
    "\n"
    "Batch analytics commands:\n"
    "  /batch <issuer> <start> <end>  — compute analytics for every active bond of\n"
    "                                    one issuer across a trade-date range, in a\n"
    "                                    separate progress window\n"
    "    issuer: one issuer code or alias (single issuer only)\n"
    "    start, end: YYYY-MM-DD, inclusive\n"
    "    Always writes to bond_analytics_db; also writes to quant_cache_db when\n"
    "    /cache is on (see /cache).\n"
    "    Examples: /batch FRA 2020-01-01 2020-12-31\n"
    "\n"
    "Session commands:\n"
    "  save [session_id]   — persist active cache to data/sessions/\n"
    "  load <session_id>   — restore a saved cache session\n"
    "  sessions            — list saved session ids\n"
    "  reset cache         — clear active cache\n"
    "\n"
    "Other:\n"
    "  help                — this message\n"
    "  quit / exit         — leave\n"
    "\n"
    "Dataset queries:\n"
    "  • With ANTHROPIC_API_KEY set (or --llm / --llm-single-shot), natural\n"
    "    language works:  input: average 10Y zero for Germany in 2017\n"
    "    Bond lookups and market-context queries are genuine LLM tool calls in\n"
    '    this mode (not just SQL): "Is there a market for USA on 2024-02-15?"\n'
    "  • Without LLM, use rule syntax (same as db-mcp-client):\n"
    "      input: tables\n"
    "      input: schema zero_rates\n"
    "      input: sql: SELECT …\n"
    "  • Force rule syntax even when an API key is set:  cqfi --rule\n"
)


_PRICE_RE = re.compile(
    r"^price\s+cmt\s+(?P<issuer>\S+)\s+(?P<date>\d{4}-\d{2}-\d{2})(?:\s+--(?P<flag>par|zero))?$",
    re.IGNORECASE,
)

_MCTX_RE = re.compile(
    r"^/mctx\s+(?P<date>\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)"
    r"(?:\s+(?P<issuer>\S+))?(?:\s+(?P<curve_label>\S+))?$",
    re.IGNORECASE,
)

_BOND_RE = re.compile(r"^/bond\s+@?(?P<id>\S+)$", re.IGNORECASE)
_BOND_HELP_RE = re.compile(r"^/bond\s*$", re.IGNORECASE)
_CALC_HELP_RE = re.compile(r"^/calc\s*$", re.IGNORECASE)
_MCTX_HELP_RE = re.compile(r"^/mctx\s*$", re.IGNORECASE)
_CACHE_HELP_RE = re.compile(r"^/cache\s*$", re.IGNORECASE)
_CACHE_ON_RE = re.compile(r"^/cache\s+on\s*$", re.IGNORECASE)
_CACHE_OFF_RE = re.compile(r"^/cache\s+off\s*$", re.IGNORECASE)
_SAVE_CACHE_HELP_RE = re.compile(r"^/save_cache\s*$", re.IGNORECASE)
_SAVE_CACHE_ON_RE = re.compile(r"^/save_cache\s+on\s*$", re.IGNORECASE)
_SAVE_CACHE_OFF_RE = re.compile(r"^/save_cache\s+off\s*$", re.IGNORECASE)
_DLV_HELP_RE = re.compile(r"^/dlv\s*$", re.IGNORECASE)
_FUT_HELP_RE = re.compile(r"^/fut\s*$", re.IGNORECASE)
_BARE_MENTION_RE = re.compile(r"^@(?P<id>\S+)$")

_BOND_HELP_TEXT = (
    "Bond Information Lookup\n"
    "======================\n"
    "\n"
    "The /bond command loads and displays a bond's details from the bond_universe table as JSON.\n"
    "This includes issuer, maturity, coupon, and other bond characteristics.\n"
    "\n"
    "Arguments: /bond <id> (where <id> is a user_friendly_id or bond_id)\n"
    "\n"
    "Examples:\n"
    "  /bond fraapr029           — Load French April 2029 bond\n"
    "  /bond @usa10y001          — Load US 10Y bond (@ prefix optional)\n"
)

_MCTX_HELP_TEXT = (
    "Market Context Verification\n"
    "============================\n"
    "\n"
    "The /mctx command checks if yield curve data exists for a given date, issuer, and curve type.\n"
    "If the market context doesn't exist, it attempts to build it. Use this to verify data availability\n"
    "before pricing bonds or running analytics.\n"
    "\n"
    "Arguments: /mctx <date> [issuer] [curve_label]\n"
    "  <date>: YYYY-MM-DD (required)\n"
    "  [issuer]: Optional issuer code (e.g., USA, DEU, FRA). If omitted, checks all issuers.\n"
    "  [curve_label]: Optional curve type (BOND_ZERO or BOND_PAR). Defaults to BOND_ZERO.\n"
    "\n"
    "Examples:\n"
    "  /mctx 2024-02-15 FRA      — Check France market on Feb 15, 2024\n"
    "  /mctx 2024-02-15          — Check all markets on Feb 15, 2024\n"
)

_CACHE_HELP_TEXT = (
    "Quant Cache Session Toggle\n"
    "==========================\n"
    "\n"
    "The /cache command controls whether analytics results from /calc, /fut, "
    "compute_bond_analytics, and compute_bond_future_analytics are written to "
    "quant_cache_db during this session.\n"
    "\n"
    "Commands:\n"
    "  /cache on   — enable caching of analytics to quant_cache_db\n"
    "  /cache off  — disable caching (compute only, no DB writes)\n"
    "  /cache      — show this help and the current setting\n"
)

_SAVE_CACHE_HELP_TEXT = (
    "Quant Cache Session-End Policy\n"
    "==============================\n"
    "\n"
    "The /save_cache command controls what happens to quant_cache_db analytics "
    "when the application closes.\n"
    "\n"
    "When enabled (/save_cache on), on exit all rows from quant_cache_db "
    "bond_analytics, cmt_analytics, bond_future_basket_outputs, and "
    "bond_future_outputs are merged into the corresponding tables in "
    "bond_analytics_db (overwriting rows with the same primary key), then "
    "quant_cache_db analytics tables are cleared.\n"
    "\n"
    "When disabled (/save_cache off), on exit quant_cache_db analytics rows "
    "are deleted without copying to bond_analytics_db.\n"
    "\n"
    "Commands:\n"
    "  /save_cache on   — merge into bond_analytics_db on exit, then clear quant cache\n"
    "  /save_cache off  — discard quant cache analytics on exit (no merge)\n"
    "  /save_cache      — show this help and the current setting\n"
)

_CALC_HELP_TEXT = (
    "Bond and CMT Analytics Calculation\n"
    "==================================\n"
    "\n"
    "The /calc command computes fixed-income analytics from market curves.\n"
    "\n"
    "Bond form:\n"
    "  /calc <bond_id> [trade_date] [curve_label] [numeric_term_structure]\n"
    "  Includes yield-to-maturity, duration, convexity, roll-down, carry, and more.\n"
    "\n"
    "CMT form:\n"
    "  /calc <issuer> <composite_tenor> [trade_date]\n"
    "  Prices a forward-starting constant-maturity treasury at par on the curve.\n"
    "  composite_tenor examples: 5y, 10y2y, 18m4w9m4d\n"
    "\n"
    "Arguments:\n"
    "  <bond_id>: user_friendly_id or bond_id\n"
    "  <issuer>: valid issuer code or alias (DEU, FRA, usa, …)\n"
    "  [trade_date]: YYYY-MM-DD (optional; defaults to latest zero_rates date)\n"
    "  [curve_label]: BOND_ZERO or BOND_PAR (bond form only, default BOND_ZERO)\n"
    "  [numeric_term_structure]: JSON repo rates (bond form only)\n"
    "\n"
    "Examples:\n"
    "  /calc fraapr029                    — bond analytics\n"
    "  /calc usa10y001 2024-02-15         — bond on a specific date\n"
    "  /calc DEU 5y                       — 5Y CMT for Germany\n"
    "  /calc FRA 10y2y 2024-02-15         — forward 10y2y CMT for France\n"
)

_DLV_HELP_TEXT = (
    "Bond Future Delivery Baskets\n"
    "============================\n"
    "\n"
    "The /dlv command builds and names the set of bonds deliverable into a bond\n"
    "future contract. Membership is fixed by the delivery month; analytics can\n"
    "later be run on any trade date with /fut.\n"
    "\n"
    "Arguments: /dlv <name> <future_code> [bond_ids...] [delivery]\n"
    "  <name>: name to store the basket under\n"
    "  <future_code>: exchange code or Bloomberg root (FGBM, OE, IK, ZN, TU, …)\n"
    "  [bond_ids]: optional explicit bonds, each '<id>' or '<id>|<factor>'\n"
    "  [delivery]: M8 (Jun 2028), U (next Sep), 6 (next quarterly year ending 6),\n"
    "              2020-09 or U2020. Defaults to the front quarterly contract.\n"
    "\n"
    "Examples:\n"
    "  /dlv mybasket FGBM                 — front Euro-Bobl basket\n"
    "  /dlv mybasket OE M8                — Euro-Bobl for June 2028\n"
    "  /dlv hist FGBS 2020-09             — historical Euro-Schatz basket\n"
    "  /dlv mine FOA fraapr029 frajun030 2025-12\n"
    "  /dlv mine FOA fraapr029|1.0326 frajun030|1.0291 2025-12\n"
)

_FUT_HELP_TEXT = (
    "Bond Future Basis Analytics\n"
    "===========================\n"
    "\n"
    "The /fut command computes the conversion factor, implied repo rate, gross\n"
    "and net basis, delta, gamma and implied fair futures price for every bond\n"
    "in a delivery basket, ordered cheapest-to-deliver first.\n"
    "\n"
    "Arguments: /fut <basket_or_contract> [trade_date] [repo]\n"
    "  <basket_or_contract>: a basket named by /dlv, or a contract code (IKH7)\n"
    "  [trade_date]: YYYY-MM-DD (optional; defaults to the latest trade date\n"
    "                held in bond_analytics for the issuer)\n"
    "  [repo]: optional repo term structure, applied to every bond in the\n"
    "          basket. A single number is a flat repo rate held for every\n"
    "          tenor (i.e. for eternity); a JSON object maps tenor labels\n"
    "          such as \"3m\" to rates in percent for a full curve. Defaults\n"
    "          to the discount curve's own forward rate to delivery when\n"
    "          omitted.\n"
    "\n"
    "Examples:\n"
    "  /fut IKH7                                     — Italian 10Y basket for March 2027\n"
    "  /fut IKH7 2026-05-15                          — the same basket valued on 15 May 2026\n"
    "  /fut mybasket 2025-10-15                      — a basket stored earlier, with its own factors\n"
    "  /fut IKH7 2026-05-15 3.0                      — valued with a flat 3% repo rate\n"
    '  /fut IKH7 2026-05-15 {"3m": 3.0, "1y": 3.2}   — valued with a full repo curve\n'
)

_BATCH_HELP_TEXT = (
    "Batch Analytics\n"
    "===============\n"
    "\n"
    "The /batch command computes analytics in parallel and shows progress in\n"
    "a separate window (one heatmap per issuer or future code). Results\n"
    "always go straight to bond_analytics_db. When /cache is on for this\n"
    "session, results are also written to quant_cache_db (use /cache to\n"
    "check or change that setting).\n"
    "\n"
    "Bond form: /batch <issuer> <start> <end>\n"
    "  Computes analytics for every active bond of one issuer across a\n"
    "  trade-date range. A bond counts as active on a trade date when it was\n"
    "  already issued and has not yet matured by that date's settlement date.\n"
    "  <issuer>: one issuer code or alias (single issuer per run)\n"
    "  <start>, <end>: YYYY-MM-DD, inclusive\n"
    "\n"
    "Bond-future form: /batch <future_code> <delivery> <start> <end>\n"
    "  Computes basis analytics for one future's delivery baskets, one dated\n"
    "  contract per delivery-month letter x per calendar year spanned by\n"
    "  <start>/<end>. A contract counts as valid on a trade date up to and\n"
    "  including its own delivery date.\n"
    "  <future_code>: one bond future code (single contract series per run)\n"
    "  <delivery>: delivery month letters from FGHJKMNQUVXZ, e.g. HMUZ for\n"
    "              all four quarterly months\n"
    "  <start>, <end>: YYYY-MM-DD, inclusive\n"
    "\n"
    "Examples:\n"
    "  /batch FRA 2020-01-01 2020-12-31          — all of 2020 for France\n"
    "  /batch usa 2024-01-01 2024-01-31          — January 2024 for the US\n"
    "  /batch FGBM HMUZ 2020-01-01 2020-12-31    — all 2020 Euro-Bobl quarterlies\n"
    "  /batch IK H 2027-01-01 2027-12-31         — just the March 2027 Euro-BTP\n"
    "\n"
    "For a config-driven, multi-issuer/multi-future run outside the app, use\n"
    "batch_bond_analytics.py directly (see its --help).\n"
)


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def _render(result: Any) -> str:
    if isinstance(result, dict) and result.get("bond_json"):
        return result["bond_json"]
    if isinstance(result, dict) and "error" in result:
        return f"Error: {result['error']}"
    if isinstance(result, dict) and "rows" in result:
        rows = result.get("rows", [])
        if not rows:
            return f"(0 rows) columns={result.get('columns', [])}"
        df = pl.DataFrame(rows)
        return f"{df}\n({result.get('row_count', len(rows))} rows)"
    if isinstance(result, dict) and "columns" in result and "table" in result:
        df = pl.DataFrame(result["columns"])
        return f"Schema for {result['table']}:\n{df}"
    if isinstance(result, list):
        return "\n".join(f"- {item}" for item in result) or "(none)"
    return str(result)


def mcp_settings_for(app: AppSettings, target: str) -> MCPSettings:
    """Build MCP connection settings for a registered dataset (see AppSettings.mcp_datasets)."""
    cfg = app.mcp_datasets[target]
    return MCPSettings(
        transport="stdio",
        db_path=cfg.db_path,
        dataset=cfg.dataset,
        semantics_dir=cfg.semantics_dir,
        server_name=f"cqfi-{target}",
    )


def format_cache_status() -> str:
    """Return a one-line summary of the current use_quant_cache setting."""
    enabled = get_runtime_settings().use_quant_cache
    state = "enabled (true)" if enabled else "disabled (false)"
    return f"Current setting: use_quant_cache is {state}."


def handle_cache_command(text: str) -> str | None:
    """Handle /cache, /cache on, /cache off. Returns output text if handled."""
    stripped = text.strip()
    if _CACHE_HELP_RE.match(stripped):
        return f"{_CACHE_HELP_TEXT}\n\n{format_cache_status()}"
    if _CACHE_ON_RE.match(stripped):
        runtime = get_runtime_settings()
        runtime.update(use_quant_cache=True)
        save_runtime_settings(runtime)
        return f"use_quant_cache set to true.\n\n{format_cache_status()}"
    if _CACHE_OFF_RE.match(stripped):
        runtime = get_runtime_settings()
        runtime.update(use_quant_cache=False)
        save_runtime_settings(runtime)
        return f"use_quant_cache set to false.\n\n{format_cache_status()}"
    if re.match(r"^/cache\b", stripped, re.IGNORECASE):
        return (
            "Invalid /cache command. Use /cache, /cache on, or /cache off.\n\n"
            f"{format_cache_status()}"
        )
    return None


def format_save_cache_status() -> str:
    """Return a one-line summary of save_quant_cache_to_bond_analytics_after_session."""
    enabled = get_runtime_settings().save_quant_cache_to_bond_analytics_after_session
    state = "enabled (true)" if enabled else "disabled (false)"
    return (
        "Current setting: save_quant_cache_to_bond_analytics_after_session is "
        f"{state}."
    )


def handle_save_cache_command(text: str) -> str | None:
    """Handle /save_cache, /save_cache on, /save_cache off."""
    stripped = text.strip()
    if _SAVE_CACHE_HELP_RE.match(stripped):
        return f"{_SAVE_CACHE_HELP_TEXT}\n\n{format_save_cache_status()}"
    if _SAVE_CACHE_ON_RE.match(stripped):
        runtime = get_runtime_settings()
        runtime.update(save_quant_cache_to_bond_analytics_after_session=True)
        save_runtime_settings(runtime)
        return (
            "save_quant_cache_to_bond_analytics_after_session set to true.\n\n"
            f"{format_save_cache_status()}"
        )
    if _SAVE_CACHE_OFF_RE.match(stripped):
        runtime = get_runtime_settings()
        runtime.update(save_quant_cache_to_bond_analytics_after_session=False)
        save_runtime_settings(runtime)
        return (
            "save_quant_cache_to_bond_analytics_after_session set to false.\n\n"
            f"{format_save_cache_status()}"
        )
    if re.match(r"^/save_cache\b", stripped, re.IGNORECASE):
        return (
            "Invalid /save_cache command. Use /save_cache, /save_cache on, "
            f"or /save_cache off.\n\n{format_save_cache_status()}"
        )
    return None


def handle_runtime_toggle_commands(text: str) -> str | None:
    """Handle /cache and /save_cache runtime toggle commands."""
    for handler in (handle_cache_command, handle_save_cache_command):
        result = handler(text)
        if result is not None:
            return result
    return None


def handle_bond_command(text: str) -> str | None:
    """Return /bond help or invalid-usage text; None if not a /bond command."""
    stripped = text.strip()
    if _BOND_HELP_RE.match(stripped):
        return _BOND_HELP_TEXT
    if re.match(r"^/bond\b", stripped, re.IGNORECASE) and not _BOND_RE.match(stripped):
        return (
            "Invalid /bond command. Use /bond <id> "
            "(user_friendly_id or bond_id).\n\n"
            f"{_BOND_HELP_TEXT}"
        )
    return None


def handle_mctx_command(text: str) -> str | None:
    """Return /mctx help or invalid-usage text; None if not a /mctx command."""
    stripped = text.strip()
    if _MCTX_HELP_RE.match(stripped):
        return _MCTX_HELP_TEXT
    if re.match(r"^/mctx\b", stripped, re.IGNORECASE) and not _MCTX_RE.match(stripped):
        return (
            "Invalid /mctx command. Use /mctx <date> [issuer] [curve_label].\n\n"
            f"{_MCTX_HELP_TEXT}"
        )
    return None


def handle_calc_command(text: str) -> str | None:
    """Return /calc help or invalid-usage text; None if not a /calc command."""
    stripped = text.strip()
    if _CALC_HELP_RE.match(stripped):
        return _CALC_HELP_TEXT
    parsed = parse_calc_command(stripped)
    if parsed is None:
        return None
    if parsed.kind == "invalid":
        return (
            "Invalid /calc command. Use bond form /calc <bond_id> [trade_date] "
            "[curve_label] [numeric_term_structure] or CMT form "
            "/calc <issuer> <composite_tenor> [trade_date].\n\n"
            f"{_CALC_HELP_TEXT}"
        )
    return None


def handle_dlv_command(text: str) -> str | None:
    """Return /dlv help or invalid-usage text; None if not a /dlv command."""
    stripped = text.strip()
    if _DLV_HELP_RE.match(stripped):
        return _DLV_HELP_TEXT
    parsed = parse_dlv_command(stripped)
    if parsed is None:
        return None
    if parsed.kind == "invalid":
        return f"Invalid /dlv command. {parsed.message}\n\n{_DLV_HELP_TEXT}"
    return None


def handle_fut_command(text: str) -> str | None:
    """Return /fut help or invalid-usage text; None if not a /fut command."""
    stripped = text.strip()
    if _FUT_HELP_RE.match(stripped):
        return _FUT_HELP_TEXT
    parsed = parse_fut_command(stripped)
    if parsed is None:
        return None
    if parsed.kind == "invalid":
        return f"Invalid /fut command. {parsed.message}\n\n{_FUT_HELP_TEXT}"
    return None


def handle_batch_command(text: str) -> str | None:
    """Return /batch help or invalid-usage text; None if not a /batch command."""
    stripped = text.strip()
    parsed = parse_batch_command(stripped)
    if parsed is None:
        return None
    if parsed.kind == "help":
        return _BATCH_HELP_TEXT
    if parsed.kind == "invalid":
        return (
            "Invalid /batch command. Use /batch <issuer> <start> <end> or "
            "/batch <future_code> <delivery> <start> <end>, e.g. "
            "/batch FRA 2020-01-01 2020-12-31 or "
            "/batch FGBM HMUZ 2020-01-01 2020-12-31.\n\n"
            f"{_BATCH_HELP_TEXT}"
        )
    return None


def execute_batch_command(parsed: BatchCommandResult) -> str:
    """Build the plan and launch the batch progress window for a validated /batch command.

    Uses the session's already-loaded config (no --config here) and its live
    /cache setting to decide whether to also write to quant_cache_db. Blocks
    until the window is closed — there is no running Qt event loop to hand
    the window off to in the plain console REPL, so this is the same
    trade-off the standalone script makes.
    """
    if parsed.mode == "future":
        return _execute_future_batch_command(parsed)
    return _execute_bond_batch_command(parsed)


def _execute_bond_batch_command(parsed: BatchCommandResult) -> str:
    request = build_batch_launch_request(parsed)
    if request.plan.total_cells == 0:
        return (
            f"No active bonds found for {parsed.issuer} between "
            f"{parsed.start.isoformat()} and {parsed.end.isoformat()}; nothing to compute."
        )
    print(
        f"Launching batch analytics window — {request.plan.total_cells} cells "
        f"({request.args_summary})..."
    )
    run_gui_standalone(
        request.plan,
        request.settings,
        workers=request.workers,
        curve_label=request.curve_label,
        args_summary=request.args_summary,
        also_cache=request.also_cache,
    )
    return "Batch analytics window closed."


def _execute_future_batch_command(parsed: BatchCommandResult) -> str:
    request = build_future_batch_launch_request(parsed)
    if request.plan.total_cells == 0:
        return (
            f"No contracts overlap {parsed.future_code} {parsed.delivery} between "
            f"{parsed.start.isoformat()} and {parsed.end.isoformat()}; nothing to compute."
        )
    print(
        f"Launching batch analytics window — {request.plan.total_cells} cells "
        f"({request.args_summary})..."
    )
    run_future_gui_standalone(
        request.plan,
        request.settings,
        workers=request.workers,
        curve_label=request.curve_label,
        args_summary=request.args_summary,
        also_cache=request.also_cache,
    )
    return "Batch analytics window closed."


def route_query(app: AppSettings, text: str) -> RoutedQuery | None:
    """Parse an explicit `<dataset>:` prefix or infer the dataset from keywords."""
    lowered = text.strip().lower()

    for name in app.mcp_datasets:
        if lowered.startswith(f"{name}:"):
            return RoutedQuery(name, text.split(":", 1)[1].strip())

    for name, cfg in app.mcp_datasets.items():
        if any(h in lowered for h in cfg.keywords):
            return RoutedQuery(name, text)

    return None


async def _run_tool_calls(client: DBClient, calls: list[ToolCall]) -> None:
    tools = await client.list_tools()
    for call in calls:
        if call.name == "check_market_context":
            # Handle custom market context tool locally
            try:
                result = check_market_context(
                    call.arguments.get("as_of"),
                    call.arguments.get("issuer"),
                    call.arguments.get("curve_label", "BOND_ZERO"),
                )
                print(_render(result))
            except Exception as exc:
                print(f"Tool error: {exc}")
            continue

        if call.name == "get_bond":
            try:
                result = get_bond(call.arguments.get("bond_id", ""))
                if result.get("status") == "success":
                    print(result["bond_json"])
                else:
                    print(_render(result))
            except Exception as exc:
                print(f"Tool error: {exc}")
            continue

        if call.name == "compute_bond_analytics":
            try:
                result = compute_bond_analytics(
                    call.arguments.get("bond_id", ""),
                    trade_date=call.arguments.get("trade_date"),
                    curve_label=call.arguments.get("curve_label", "BOND_ZERO"),
                    numeric_term_structure=call.arguments.get("numeric_term_structure"),
                )
                if result.get("status") == "success":
                    print(result["analytics_json"])
                else:
                    print(_render(result))
            except Exception as exc:
                print(f"Tool error: {exc}")
            continue

        if call.name == "compute_cmt_analytics":
            try:
                result = compute_cmt_analytics(
                    call.arguments.get("issuer", ""),
                    call.arguments.get("composite_tenor", ""),
                    trade_date=call.arguments.get("trade_date"),
                    curve_label=call.arguments.get("curve_label", "BOND_ZERO"),
                )
                print(format_calc_result(result))
            except Exception as exc:
                print(f"Tool error: {exc}")
            continue

        if call.name == "build_delivery_basket":
            try:
                result = build_delivery_basket(
                    call.arguments.get("name", ""),
                    call.arguments.get("future_code", ""),
                    delivery=call.arguments.get("delivery"),
                    bond_ids=call.arguments.get("bond_ids"),
                )
                print(format_dlv_result(result))
            except Exception as exc:
                print(f"Tool error: {exc}")
            continue

        if call.name == "compute_bond_future_analytics":
            try:
                result = compute_bond_future_analytics(
                    call.arguments.get("target", ""),
                    trade_date=call.arguments.get("trade_date"),
                    futures_price=call.arguments.get("futures_price"),
                    curve_label=call.arguments.get("curve_label", "BOND_ZERO"),
                    numeric_term_structure=call.arguments.get("numeric_term_structure"),
                )
                print(format_fut_result(result))
            except Exception as exc:
                print(f"Tool error: {exc}")
            continue

        if call.name not in tools:
            print(f"Tool {call.name!r} not available (have: {tools})")
            continue
        result = await client.call_tool(call.name, call.arguments)
        print(_render(result))


def _handle_bond_future_commands(text: str) -> bool:
    """Execute /dlv or /fut locally. Returns True when one of them handled *text*."""
    try:
        dlv_result = execute_dlv_command(text)
        if dlv_result is not None:
            print(format_dlv_result(dlv_result))
            return True

        fut_result = execute_fut_command(text)
        if fut_result is not None:
            print(format_fut_result(fut_result))
            return True
    except Exception as exc:
        print(f"Bond future error: {exc}")
        return True
    return False


async def _query_dataset(
    app: AppSettings,
    target: str,
    text: str,
    *,
    use_agent: bool,
    use_single_shot: bool,
    force_rule: bool,
) -> None:
    cache_result = handle_runtime_toggle_commands(text)
    if cache_result is not None:
        print(cache_result)
        return

    bond_help = handle_bond_command(text)
    if bond_help is not None:
        print(bond_help)
        return

    mctx_help = handle_mctx_command(text)
    if mctx_help is not None:
        print(mctx_help)
        return

    calc_help = handle_calc_command(text)
    if calc_help is not None:
        print(calc_help)
        return

    dlv_help = handle_dlv_command(text)
    if dlv_help is not None:
        print(dlv_help)
        return

    fut_help = handle_fut_command(text)
    if fut_help is not None:
        print(fut_help)
        return

    batch_help = handle_batch_command(text)
    if batch_help is not None:
        print(batch_help)
        return

    clean_help = handle_clean_command(text)
    if clean_help is not None:
        print(clean_help)
        return

    # Check for slash commands before routing to planner
    bond_match = _BOND_RE.match(text.strip())
    if bond_match:
        bond_key = bond_match.group("id")
        try:
            result = get_bond(bond_key)
            if result.get("status") == "success":
                print(result["bond_json"])
            else:
                print(f"Error: {result.get('message')}")
        except Exception as exc:
            print(f"Bond error: {exc}")
        return

    mctx_match = _MCTX_RE.match(text.strip())
    if mctx_match:
        as_of = mctx_match.group("date").strip()
        issuer = mctx_match.group("issuer")
        curve_label = mctx_match.group("curve_label") or "BOND_ZERO"
        try:
            result = check_market_context(as_of, issuer, curve_label)
            if result.get("status") == "success":
                print(
                    f"Market context check: {result['message']}\n"
                    f"  Date: {result['date']}\n"
                    f"  Issuer: {result['issuer']}\n"
                    f"  Curve: {result['curve_label']}"
                )
            else:
                print(f"Error: {result.get('message')}")
        except Exception as exc:
            print(f"Market context error: {exc}")
        return

    parsed = parse_calc_command(text.strip())
    if parsed is not None and parsed.kind in ("bond", "cmt"):
        try:
            result = execute_parsed_calc(parsed)
            print(format_calc_result(result))
        except Exception as exc:
            print(f"Analytics error: {exc}")
        return

    if _handle_bond_future_commands(text.strip()):
        return

    batch_parsed = parse_batch_command(text.strip())
    if batch_parsed is not None and batch_parsed.kind == "run":
        try:
            print(execute_batch_command(batch_parsed))
        except Exception as exc:
            print(f"Batch error: {exc}")
        return

    clean_parsed = parse_clean_command(text.strip())
    if clean_parsed is not None and clean_parsed.kind == "run_bonds":
        try:
            print(execute_clean_bonds())
        except Exception as exc:
            print(f"Clean error: {exc}")
        return

    use_agent, use_single_shot = resolve_query_mode(
        use_agent=use_agent,
        use_single_shot=use_single_shot,
        force_rule=force_rule,
    )
    settings = mcp_settings_for(app, target)
    async with DBClient(settings) as client:
        profile_prompt: str | None = None
        if use_agent or use_single_shot:
            description = await client.describe_dataset()
            profile_prompt = description.get("prompt") or None

        if use_agent:
            from mcp_data.client.agent import SQLAgent

            agent = SQLAgent(
                client, profile_prompt=profile_prompt, extra_tools=EXTRA_TOOLS
            )
            result = await agent.run(text)
            print(f"[{target}]\n{result.answer or '(no answer)'}")
            return

        if use_single_shot:
            planner: Planner = LLMPlanner(
                profile_prompt=profile_prompt, extra_tools=EXTRA_TOOLS
            )
        else:
            planner = CQFIRulePlanner()

        # Add custom tools to available tools list
        tools = list(await client.list_tools())
        tools.extend(LOCAL_TOOL_NAMES)
        calls = planner.plan(text, tools)
        if not calls:
            print(f"[{target}] Could not interpret that.\n{RULE_MODE_HINT}")
            return
        print(f"[{target}]")
        await _run_tool_calls(client, calls)


def _handle_local_command(
    text: str,
    cache_mgr: CacheManager,
) -> bool:
    """Handle pricing/session commands locally. Returns True if handled."""
    lowered = text.strip().lower()

    cache_result = handle_runtime_toggle_commands(text)
    if cache_result is not None:
        print(cache_result)
        return True

    bond_help = handle_bond_command(text)
    if bond_help is not None:
        print(bond_help)
        return True

    mctx_help = handle_mctx_command(text)
    if mctx_help is not None:
        print(mctx_help)
        return True

    calc_help = handle_calc_command(text)
    if calc_help is not None:
        print(calc_help)
        return True

    dlv_help = handle_dlv_command(text)
    if dlv_help is not None:
        print(dlv_help)
        return True

    fut_help = handle_fut_command(text)
    if fut_help is not None:
        print(fut_help)
        return True

    batch_help = handle_batch_command(text)
    if batch_help is not None:
        print(batch_help)
        return True

    clean_help = handle_clean_command(text)
    if clean_help is not None:
        print(clean_help)
        return True

    if lowered in ("help", "?"):
        print(HELP_TEXT_CQFI)
        print()
        print("Rule-based SQL planner commands (also work with input:/cache: prefix):")
        print(HELP_TEXT)
        return True

    if lowered in ("sessions", "list sessions"):
        sessions = cache_mgr.list_sessions()
        print("Saved sessions:" if sessions else "(no saved sessions)")
        for sid in sessions:
            marker = " *" if sid == cache_mgr.session_id else ""
            print(f"  {sid}{marker}")
        return True

    if lowered == "reset cache":
        cache_mgr.reset_cache()
        print("Active cache cleared.")
        return True

    if lowered.startswith("save"):
        parts = text.split()
        session_id = parts[1] if len(parts) > 1 else None
        sid = cache_mgr.save_session(session_id)
        print(
            f"Session saved as {sid!r} -> {cache_mgr.settings.sessions_dir / (sid + '.db')}"
        )
        return True

    if lowered.startswith("load "):
        session_id = text.split(maxsplit=1)[1].strip()
        cache_mgr.load_session(session_id)
        print(f"Loaded session {session_id!r}.")
        return True

    match = _PRICE_RE.match(text.strip())
    if match:
        issuer = match.group("issuer")
        val_date = match.group("date")
        rate_type = match.group("flag") or "zero"
        try:
            result = cache_mgr.price_cmts(issuer, val_date, rate_type=rate_type)
            print(result)
            print(f"\n({len(result)} CMTs priced)")
        except Exception as exc:
            print(f"Pricing error: {exc}")
        return True

    match = _MCTX_RE.match(text.strip())
    if match:
        as_of = match.group("date").strip()
        issuer = match.group("issuer")
        curve_label = match.group("curve_label") or "BOND_ZERO"
        try:
            result = check_market_context(as_of, issuer, curve_label)
            if result.get("status") == "success":
                print(
                    f"Market context check: {result['message']}\n"
                    f"  Date: {result['date']}\n"
                    f"  Issuer: {result['issuer']}\n"
                    f"  Curve: {result['curve_label']}"
                )
            else:
                print(f"Error: {result.get('message')}")
        except Exception as exc:
            print(f"Market context error: {exc}")
        return True

    # Check for bare mention shortcut: @id
    bare_match = _BARE_MENTION_RE.match(text.strip())
    if bare_match:
        bond_key = bare_match.group("id")
        try:
            result = get_bond(bond_key)
            if result.get("status") == "success":
                print(result["bond_json"])
            else:
                print(f"Error: {result.get('message')}")
        except Exception as exc:
            print(f"Bond error: {exc}")
        return True

    match = _BOND_RE.match(text.strip())
    if match:
        bond_key = match.group("id")
        try:
            result = get_bond(bond_key)
            if result.get("status") == "success":
                print(result["bond_json"])
            else:
                print(f"Error: {result.get('message')}")
        except Exception as exc:
            print(f"Bond error: {exc}")
        return True

    parsed = parse_calc_command(text.strip())
    if parsed is not None and parsed.kind in ("bond", "cmt"):
        try:
            result = execute_parsed_calc(parsed)
            print(format_calc_result(result))
        except Exception as exc:
            print(f"Analytics error: {exc}")
        return True

    batch_parsed = parse_batch_command(text.strip())
    if batch_parsed is not None and batch_parsed.kind == "run":
        try:
            print(execute_batch_command(batch_parsed))
        except Exception as exc:
            print(f"Batch error: {exc}")
        return True

    clean_parsed = parse_clean_command(text.strip())
    if clean_parsed is not None and clean_parsed.kind == "run_bonds":
        try:
            print(execute_clean_bonds())
        except Exception as exc:
            print(f"Clean error: {exc}")
        return True

    return _handle_bond_future_commands(text.strip())


async def _interactive(
    app: AppSettings,
    cache_mgr: CacheManager,
    *,
    use_agent: bool,
    use_single_shot: bool,
    force_rule: bool,
) -> None:
    print(HELP_TEXT_CQFI)
    loop = asyncio.get_event_loop()
    while True:
        try:
            query = await loop.run_in_executor(None, input, "cqfi> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        query = query.strip()
        if not query:
            continue
        if query.lower() in ("quit", "exit"):
            break

        if _handle_local_command(query, cache_mgr):
            continue

        rewritten, unresolved = resolve_bond_mentions(query)
        if unresolved:
            print(
                f"Warning: could not resolve bond mentions: {', '.join(f'@{id}' for id in unresolved)}"
            )

        routed = route_query(app, rewritten)
        if routed is None:
            print(
                "Ambiguous query — prefix with input:, cache:, or bond_analytics:, "
                "or run 'price cmt …' for pricing."
            )
            continue

        await _query_dataset(
            app,
            routed.target,
            routed.text,
            use_agent=use_agent,
            use_single_shot=use_single_shot,
            force_rule=force_rule,
        )


async def _amain(args: argparse.Namespace) -> None:
    load_settings(args.config)
    load_runtime_settings()
    app = get_settings()
    app.ensure_dirs()
    cache_mgr = CacheManager(app)

    try:
        if args.query:
            if _handle_local_command(args.query, cache_mgr):
                return
            rewritten, unresolved = resolve_bond_mentions(args.query)
            if unresolved:
                print(
                    f"Warning: could not resolve bond mentions: {', '.join(f'@{id}' for id in unresolved)}"
                )
            routed = route_query(app, rewritten)
            if routed is None:
                print(
                    "Ambiguous query — prefix with input:, cache:, or bond_analytics:"
                )
                return
            await _query_dataset(
                app,
                routed.target,
                routed.text,
                use_agent=args.llm,
                use_single_shot=args.llm_single_shot,
                force_rule=args.rule,
            )
        else:
            await _interactive(
                app,
                cache_mgr,
                use_agent=args.llm,
                use_single_shot=args.llm_single_shot,
                force_rule=args.rule,
            )
    finally:
        save_runtime_settings()
        cache_mgr.close()


def main() -> None:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="cqfi interactive agent.",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help='Optional one-shot query (e.g. "input: avg 10Y zero for DEU in 2012").',
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "YAML config file for database and semantics paths "
            f"(default: {DEFAULT_CONFIG_PATH}). "
            "Also set via CQFI_CONFIG."
        ),
    )
    parser.add_argument(
        "--rule",
        action="store_true",
        help=(
            "Force rule-based command syntax (tables / schema / sql:) even when "
            "ANTHROPIC_API_KEY is set."
        ),
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Agentic LLM mode (LangGraph ReAct via mcp-data). Requires ANTHROPIC_API_KEY.",
    )
    parser.add_argument(
        "--llm-single-shot",
        action="store_true",
        help="Single-shot LLM planner instead of the ReAct agent.",
    )
    args = parser.parse_args()
    if args.llm and args.llm_single_shot:
        parser.error("Use only one of --llm / --llm-single-shot.")
    if args.rule and (args.llm or args.llm_single_shot):
        parser.error("Use --rule alone, not with --llm / --llm-single-shot.")
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
