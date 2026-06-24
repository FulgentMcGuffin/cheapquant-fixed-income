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
