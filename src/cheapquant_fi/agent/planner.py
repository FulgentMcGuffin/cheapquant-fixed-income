"""Query planners for the cqfi agent."""

from __future__ import annotations

import os
import re

from mcp_data.client.planner import Planner, RuleBasedPlanner, ToolCall

_LIST_TABLES_RE = re.compile(
    r"\b(?:what|which|show|list|name)\b.*\btables?\b|\btables?\b.*\b(?:exist|available|there)\b",
    re.IGNORECASE,
)

_DESCRIBE_RE = re.compile(
    r"\b(?:describe|explain|overview of|about)\b.*\b(?:dataset|data|database)\b",
    re.IGNORECASE,
)

_BOND_CMD_RE = re.compile(r"^/bond\s+(?P<id>\S+)$", re.IGNORECASE)
_MCTX_CMD_RE = re.compile(
    r"^/mctx\s+(?P<date>\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)"
    r"(?:\s+(?P<issuer>\S+))?(?:\s+(?P<curve_label>\S+))?$",
    re.IGNORECASE,
)
_CALC_CMD_RE = re.compile(
    r"^/calc\s+@?(?P<bond_id>\S+)"
    r"(?:\s+(?P<trade_date>\d{4}-\d{2}-\d{2}))?"
    r"(?:\s+(?P<curve_label>\S+))?"
    r"(?:\s+(?P<numeric_term_structure>\{.*\}))?$",
    re.IGNORECASE,
)


class CQFIRulePlanner(RuleBasedPlanner):
    """Rule-based planner with a few extra phrases for dataset introspection."""

    def plan(self, query: str, available_tools: list[str]) -> list[ToolCall]:
        calls = self._extended_parse(query, available_tools)
        if calls:
            return calls
        return super().plan(query, available_tools)

    def _extended_parse(
        self, query: str, available_tools: list[str]
    ) -> list[ToolCall]:
        text = query.strip()
        lowered = text.lower()

        if lowered in ("describe", "describe dataset", "dataset"):
            if "describe_dataset" in available_tools:
                return [ToolCall("describe_dataset")]

        if _LIST_TABLES_RE.search(text):
            if "list_tables" in available_tools:
                return [ToolCall("list_tables")]

        if _DESCRIBE_RE.search(text):
            if "describe_dataset" in available_tools:
                return [ToolCall("describe_dataset")]

        if lowered.startswith("schema "):
            table = text.split(None, 1)[1].strip()
            if table and "get_schema" in available_tools:
                return [ToolCall("get_schema", {"table": table})]

        # Handle /bond command syntax
        bond_match = _BOND_CMD_RE.match(text)
        if bond_match and "get_bond" in available_tools:
            return [ToolCall("get_bond", {"bond_id": bond_match.group("id")})]

        # Handle /mctx command syntax
        mctx_match = _MCTX_CMD_RE.match(text)
        if mctx_match and "check_market_context" in available_tools:
            date_str = mctx_match.group("date").strip()
            issuer = mctx_match.group("issuer")
            curve_label = mctx_match.group("curve_label") or "BOND_ZERO"
            return [
                ToolCall(
                    "check_market_context",
                    {
                        "as_of": date_str,
                        "issuer": issuer,
                        "curve_label": curve_label,
                    },
                )
            ]

        # Handle /calc command syntax
        calc_match = _CALC_CMD_RE.match(text)
        if calc_match and "compute_bond_analytics" in available_tools:
            bond_id = calc_match.group("bond_id").strip()
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
                except Exception:
                    pass

            return [ToolCall("compute_bond_analytics", kwargs)]

        return []


def has_llm_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def resolve_query_mode(
    *,
    use_agent: bool,
    use_single_shot: bool,
    force_rule: bool,
) -> tuple[bool, bool]:
    """Return ``(use_agent, use_single_shot)`` after applying defaults.

    When no explicit LLM flag is given and ``ANTHROPIC_API_KEY`` is set, default
    to single-shot LLM so natural-language questions work like ``db-mcp-client
    --llm-single-shot``. Use ``force_rule=True`` (``--rule``) to keep the strict
    command syntax instead.
    """
    if force_rule:
        return False, False
    if use_agent or use_single_shot:
        return use_agent, use_single_shot
    if has_llm_credentials():
        return False, True
    return False, False


RULE_MODE_HINT = (
    "Natural-language questions need LLM mode. Either:\n"
    "  • restart with:  uv run cqfi --llm\n"
    "  • or set ANTHROPIC_API_KEY (auto-enables single-shot LLM)\n"
    "  • or use rule syntax:\n"
    "      input: tables\n"
    "      input: schema zero_rates\n"
    "      input: sql: SELECT AVG(Y010p0) FROM zero_rates "
    "WHERE source='DEU' AND date BETWEEN '2017-01-01' AND '2017-12-31'"
)
