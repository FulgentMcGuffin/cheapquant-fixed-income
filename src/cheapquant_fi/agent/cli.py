"""Unified text interface for input_data queries and cached analytics."""

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
from enum import Enum
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
from cheapquant_fi.cli_tools import check_market_context


class DatasetTarget(str, Enum):
    INPUT = "input"
    CACHE = "cache"


@dataclass(frozen=True)
class RoutedQuery:
    target: DatasetTarget
    text: str


HELP_TEXT_CQFI = (
    "CheapQuant Fixed Income agent\n"
    "=============================\n"
    "\n"
    "Query datasets (prefix optional — auto-routed when obvious):\n"
    "  input: <question>   — read-only questions about yield curves in input_data.db\n"
    "  cache: <question>   — questions about cached QuantLib results\n"
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


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def _render(result: Any) -> str:
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


def _mcp_settings(app: AppSettings, target: DatasetTarget) -> MCPSettings:
    if target == DatasetTarget.INPUT:
        return MCPSettings(
            transport="stdio",
            db_path=app.ycs_db_path,
            dataset=app.ycs_dataset,
            semantics_dir=app.ycs_semantics_dir,
            server_name="cqfi-input",
        )
    return MCPSettings(
        transport="stdio",
        db_path=app.cache_db_path,
        dataset=app.cache_dataset,
        semantics_dir=app.cache_semantics_dir,
        server_name="cqfi-cache",
    )


def route_query(text: str) -> RoutedQuery | None:
    """Parse explicit prefixes or infer dataset from keywords."""
    lowered = text.strip().lower()
    if lowered.startswith("input:"):
        return RoutedQuery(DatasetTarget.INPUT, text.split(":", 1)[1].strip())
    if lowered.startswith("cache:"):
        return RoutedQuery(DatasetTarget.CACHE, text.split(":", 1)[1].strip())

    cache_hints = (
        "cmt",
        "clean price",
        "cached",
        "pricing run",
        "calculation_log",
        "quantlib",
        "we computed",
        "we priced",
        "session",
    )
    if any(h in lowered for h in cache_hints):
        return RoutedQuery(DatasetTarget.CACHE, text)

    input_hints = (
        "zero rate",
        "par rate",
        "spotfx",
        "window_corr",
        "correlation",
        "spread",
        "slope",
        "input_data",
        "treasury curve",
        "yield curve",
    )
    if any(h in lowered for h in input_hints):
        return RoutedQuery(DatasetTarget.INPUT, text)

    return None


async def _run_tool_calls(client: DBClient, calls: list[ToolCall]) -> None:
    tools = await client.list_tools()
    for call in calls:
        if call.name not in tools:
            print(f"Tool {call.name!r} not available (have: {tools})")
            continue
        result = await client.call_tool(call.name, call.arguments)
        print(_render(result))


async def _query_dataset(
    app: AppSettings,
    target: DatasetTarget,
    text: str,
    *,
    use_agent: bool,
    use_single_shot: bool,
    force_rule: bool,
) -> None:
    use_agent, use_single_shot = resolve_query_mode(
        use_agent=use_agent,
        use_single_shot=use_single_shot,
        force_rule=force_rule,
    )
    settings = _mcp_settings(app, target)
    label = "input_data" if target == DatasetTarget.INPUT else "quant_cache"
    async with DBClient(settings) as client:
        profile_prompt: str | None = None
        if use_agent or use_single_shot:
            description = await client.describe_dataset()
            profile_prompt = description.get("prompt") or None

        if use_agent:
            from mcp_data.client.agent import SQLAgent

            agent = SQLAgent(client, profile_prompt=profile_prompt)
            result = await agent.run(text)
            print(f"[{label}]\n{result.answer or '(no answer)'}")
            return

        if use_single_shot:
            planner: Planner = LLMPlanner(profile_prompt=profile_prompt)
        else:
            planner = CQFIRulePlanner()

        calls = planner.plan(text, await client.list_tools())
        if not calls:
            print(f"[{label}] Could not interpret that.\n{RULE_MODE_HINT}")
            return
        print(f"[{label}]")
        await _run_tool_calls(client, calls)


def _handle_local_command(
    text: str,
    cache_mgr: CacheManager,
) -> bool:
    """Handle pricing/session commands locally. Returns True if handled."""
    lowered = text.strip().lower()

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
            print(f"\n({len(result)} CMTs priced and cached)")
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

        routed = route_query(query)
        if routed is None:
            print(
                "Ambiguous query — prefix with input: or cache:, "
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
            routed = route_query(args.query)
            if routed is None:
                print("Ambiguous query — prefix with input: or cache:")
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
