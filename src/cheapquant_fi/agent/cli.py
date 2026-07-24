"""Unified text interface for ycs_data queries and cached analytics."""

from __future__ import annotations

# Must run before any langchain import (pulled in via mcp_data).
from cheapquant_fi.config import (  # noqa: F401
    DEFAULT_CONFIG_PATH,
    AppSettings,
    configure_langsmith,
    get_settings,
    load_settings,
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

from cheapquant_fi.agent.planner import (
    CQFIRulePlanner,
    RULE_MODE_HINT,
    resolve_query_mode,
)
from cheapquant_fi.cache.manager import CacheManager
from cheapquant_fi.cli_tools import (
    check_market_context,
    check_market_context_lc_tool,
    compute_bond_analytics,
    compute_bond_analytics_lc_tool,
    get_bond,
    get_bond_lc_tool,
    resolve_bond_mentions,
)

# Real, executable LangChain tools bound into SQLAgent/LLMPlanner alongside the
# built-in SQL tools, so the LLM can genuinely call them (not just read a text
# description) -- see cheapquant_fi.cli_tools.
EXTRA_TOOLS = [get_bond_lc_tool, check_market_context_lc_tool, compute_bond_analytics_lc_tool]


@dataclass(frozen=True)
class RoutedQuery:
    target: str
    text: str


HELP_TEXT_CQFI = (
    "CheapQuant Fixed Income agent\n"
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
    "    Also available in LLM mode: \"Is there a market for France on 17 Feb 2022?\"\n"
    "\n"
    "Bond commands:\n"
    "  /bond <id>  — show bond_universe row as JSON (user_friendly_id or bond_id)\n"
    "    Examples: /bond usa10y001\n"
    "              /bond US0001\n"
    "    Also available in LLM mode: \"Show bond usa10y001 as JSON\", \"what's the\n"
    "    duration of fraapr029?\"\n"
    "\n"
    "Bond analytics commands:\n"
    "  /calc <id> [date] [curve] [term_structure]  — compute bond analytics\n"
    "    id: bond_friendly_id or bond_id (required)\n"
    "    date: YYYY-MM-DD (optional, defaults to latest available date)\n"
    "    curve: curve label (optional, defaults to BOND_ZERO)\n"
    "    term_structure: JSON dict of tenors to rates (optional)\n"
    "    Examples: /calc fraapr029\n"
    "              /calc usa10y001 2024-02-15\n"
    "              /calc @fraapr029 2024-02-15 BOND_ZERO {\"1m\": 2.1, \"3m\": 2.15}\n"
    "    Also available in LLM mode: \"Calculate analytics for fraapr029\"\n"
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
    "    this mode (not just SQL): \"Is there a market for USA on 2024-02-15?\"\n"
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
_CALC_RE = re.compile(
    r"^/calc\s+@?(?P<bond_id>\S+)"
    r"(?:\s+(?P<trade_date>\d{4}-\d{2}-\d{2}))?"
    r"(?:\s+(?P<curve_label>\S+))?"
    r"(?:\s+(?P<numeric_term_structure>\{.*\}))?$",
    re.IGNORECASE,
)
_CALC_HELP_RE = re.compile(r"^/calc\s*$", re.IGNORECASE)
_MCTX_HELP_RE = re.compile(r"^/mctx\s*$", re.IGNORECASE)
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

_CALC_HELP_TEXT = (
    "Bond Analytics Calculation\n"
    "==========================\n"
    "\n"
    "The /calc command computes fixed-income analytics for a bond given market conditions.\n"
    "This includes yield-to-maturity, duration, convexity, roll-down, carry, and other metrics.\n"
    "\n"
    "Arguments: /calc <bond_id> [trade_date] [curve_label] [numeric_term_structure]\n"
    "  <bond_id>: Bond identifier — user_friendly_id or bond_id (required)\n"
    "  [trade_date]: Valuation date in YYYY-MM-DD format (optional, defaults to latest available)\n"
    "  [curve_label]: Curve collection (BOND_ZERO or BOND_PAR, defaults to BOND_ZERO)\n"
    "  [numeric_term_structure]: JSON dict of repo rates (optional, for programmatic use)\n"
    "\n"
    "Examples:\n"
    "  /calc fraapr029           — Calculate analytics for FRA APR 2029 bond\n"
    "  /calc usa10y001 2024-02-15 BOND_ZERO  — Calculate for US 10Y on specific date\n"
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

        if call.name not in tools:
            print(f"Tool {call.name!r} not available (have: {tools})")
            continue
        result = await client.call_tool(call.name, call.arguments)
        print(_render(result))


async def _query_dataset(
    app: AppSettings,
    target: str,
    text: str,
    *,
    use_agent: bool,
    use_single_shot: bool,
    force_rule: bool,
) -> None:
    # Check for no-argument help requests first
    if _BOND_HELP_RE.match(text.strip()):
        print(_BOND_HELP_TEXT)
        return

    if _MCTX_HELP_RE.match(text.strip()):
        print(_MCTX_HELP_TEXT)
        return

    if _CALC_HELP_RE.match(text.strip()):
        print(_CALC_HELP_TEXT)
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

    calc_match = _CALC_RE.match(text.strip())
    if calc_match:
        bond_id = calc_match.group("bond_id")
        trade_date = calc_match.group("trade_date")
        curve_label = calc_match.group("curve_label") or "BOND_ZERO"
        numeric_term_structure_str = calc_match.group("numeric_term_structure")

        kwargs = {
            "bond_id": bond_id,
            "curve_label": curve_label,
        }
        if trade_date:
            kwargs["trade_date"] = trade_date.strip()
        if numeric_term_structure_str:
            try:
                kwargs["numeric_term_structure"] = eval(numeric_term_structure_str)
            except Exception as e:
                print(f"Error parsing term structure: {e}")
                return

        try:
            result = compute_bond_analytics(**kwargs)
            if result.get("status") == "success":
                print(result["analytics_json"])
            else:
                print(f"Error: {result.get('message')}")
        except Exception as exc:
            print(f"Analytics error: {exc}")
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
        tools.extend(["check_market_context", "get_bond", "compute_bond_analytics"])
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

    # Check for no-argument help requests
    if _BOND_HELP_RE.match(text.strip()):
        print(_BOND_HELP_TEXT)
        return True

    if _MCTX_HELP_RE.match(text.strip()):
        print(_MCTX_HELP_TEXT)
        return True

    if _CALC_HELP_RE.match(text.strip()):
        print(_CALC_HELP_TEXT)
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
        print(f"Session saved as {sid!r} -> {cache_mgr.settings.sessions_dir / (sid + '.db')}")
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

    match = _CALC_RE.match(text.strip())
    if match:
        bond_id = match.group("bond_id")
        trade_date = match.group("trade_date")
        curve_label = match.group("curve_label") or "BOND_ZERO"
        numeric_term_structure_str = match.group("numeric_term_structure")

        kwargs = {
            "bond_id": bond_id,
            "curve_label": curve_label,
        }
        if trade_date:
            kwargs["trade_date"] = trade_date.strip()
        if numeric_term_structure_str:
            try:
                kwargs["numeric_term_structure"] = eval(numeric_term_structure_str)
            except Exception as e:
                print(f"Error parsing term structure: {e}")
                return True

        try:
            result = compute_bond_analytics(**kwargs)
            if result.get("status") == "success":
                print(result["analytics_json"])
            else:
                print(f"Error: {result.get('message')}")
        except Exception as exc:
            print(f"Analytics error: {exc}")
        return True

    return False


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
            print(f"Warning: could not resolve bond mentions: {', '.join(f'@{id}' for id in unresolved)}")

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
    app = get_settings()
    app.ensure_dirs()
    cache_mgr = CacheManager(app)

    try:
        if args.query:
            if _handle_local_command(args.query, cache_mgr):
                return
            rewritten, unresolved = resolve_bond_mentions(args.query)
            if unresolved:
                print(f"Warning: could not resolve bond mentions: {', '.join(f'@{id}' for id in unresolved)}")
            routed = route_query(app, rewritten)
            if routed is None:
                print("Ambiguous query — prefix with input:, cache:, or bond_analytics:")
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
        cache_mgr.close()


def main() -> None:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="CheapQuant fixed income interactive agent.",
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
