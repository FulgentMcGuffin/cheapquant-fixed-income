"""Scenario runner: drives multi-turn scenarios against the real LLM agent."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp_data.client.agent import _build_model, _compose_system_prompt

from cqfi.agent.cli import EXTRA_TOOLS, mcp_settings_for
from cqfi.config import AppSettings
from cqfi.evals.models import (
    Scenario,
    ScenarioResult,
    ToolCallRecord,
    TurnResult,
)
from mcp_data.client.session import DBClient


class EvalRunner:
    """Drives multi-turn scenarios against the LLM agent."""

    def __init__(self, app: AppSettings, *, model: object | None = None):
        self.app = app
        self.model = model

    async def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        """Run a complete scenario and return aggregated results."""
        settings = mcp_settings_for(self.app, scenario.target)

        async with DBClient(settings) as client:
            description = await client.describe_dataset()
            profile_prompt = description.get("prompt")

            # Build the react agent once, reuse across turns
            tools = list(await load_mcp_tools(client.session)) + EXTRA_TOOLS
            agent = create_react_agent(
                _build_model(self.model),
                tools,
                prompt=SystemMessage(content=_compose_system_prompt(profile_prompt)),
            )

            history: list[BaseMessage] = []
            turn_results = []

            for turn in scenario.turns:
                # Record start time
                start_time = time.monotonic()

                # Add user input to history
                history.append(HumanMessage(content=turn.user_input))

                # Run the agent with accumulated history
                state = await agent.ainvoke({"messages": history})

                # Extract updated history
                history = state.get("messages", history)

                # Extract final answer (last AIMessage)
                answer = ""
                for msg in reversed(history):
                    if isinstance(msg, AIMessage):
                        answer = _extract_message_text(msg)
                        if answer:
                            break

                # Extract tool calls from new messages
                tool_calls = _extract_tool_calls(history, len(turn_results))

                # Extract token usage from the last AIMessage
                token_usage = _extract_token_usage(history)

                # Calculate latency
                latency_ms = (time.monotonic() - start_time) * 1000

                # Evaluate criteria
                turn_result = TurnResult(
                    user_input=turn.user_input,
                    answer=answer,
                    tool_calls=tool_calls,
                    token_usage=token_usage,
                    latency_ms=latency_ms,
                    criteria_results=[],
                )

                # Run all criteria for this turn
                for criterion in turn.criteria:
                    criterion_result = criterion(turn_result)
                    turn_result.criteria_results.append(criterion_result)

                turn_results.append(turn_result)

            return ScenarioResult(
                scenario_name=scenario.name,
                tags=scenario.tags,
                turn_results=turn_results,
            )


def _extract_message_text(message: AIMessage) -> str:
    """Extract plain text from an AIMessage."""
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def _extract_tool_calls(
    history: list[BaseMessage], start_turn: int
) -> list[ToolCallRecord]:
    """Extract all tool calls (and their results) from the message history.

    start_turn is the number of complete turns processed so far, used to skip
    tool messages from previous turns.
    """
    records: list[ToolCallRecord] = []
    seen_tools = set()

    for msg in history:
        # Look for ToolMessage entries (tool results), not internal LangChain tool structures
        if isinstance(msg, ToolMessage):
            # Extract tool name from the message; it's typically "tool_result" with a tool_use_id
            # The actual tool name and args are in the AIMessage that preceded this ToolMessage
            continue

        # Look for AIMessage with tool_calls (LangChain's representation)
        if isinstance(msg, AIMessage):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tool_id = (
                        tool_call.get("id") if isinstance(tool_call, dict) else None
                    )
                    if tool_id and tool_id not in seen_tools:
                        seen_tools.add(tool_id)
                        name = (
                            tool_call.get("name")
                            if isinstance(tool_call, dict)
                            else getattr(tool_call, "name", "unknown")
                        )
                        args = (
                            tool_call.get("args", {})
                            if isinstance(tool_call, dict)
                            else getattr(tool_call, "args", {})
                        )
                        # Find the corresponding ToolMessage with the result
                        result_summary = _find_tool_result(history, tool_id)
                        records.append(
                            ToolCallRecord(
                                name=name,
                                arguments=args or {},
                                result_summary=result_summary,
                            )
                        )

    return records


def _find_tool_result(history: list[BaseMessage], tool_id: str) -> str:
    """Find the result for a tool call by ID."""
    for msg in history:
        if isinstance(msg, ToolMessage):
            if (
                getattr(msg, "tool_use_id", None) == tool_id
                or getattr(msg, "id", None) == tool_id
            ):
                result = msg.content
                if isinstance(result, str):
                    return result[:200]  # truncate long results
                return str(result)[:200]
    return "(no result found)"


def _flatten_token_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    """Flatten a usage mapping down to scalar counts.

    LangChain nests per-kind breakdowns under ``input_token_details`` and
    ``output_token_details``.  Those are flattened to ``<group>_<key>`` entries
    so no counts are lost, and non-numeric values are dropped, leaving a plain
    ``dict[str, int]``.
    """
    flat: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(value, Mapping):
            prefix = key[: -len("_details")] if key.endswith("_details") else key
            for detail_key, detail_value in value.items():
                if _is_count(detail_value):
                    flat[f"{prefix}_{detail_key}"] = detail_value
        elif _is_count(value):
            flat[key] = value
    return flat


def _is_count(value: Any) -> bool:
    """Whether *value* is a token count (``bool`` is an int but never a count)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _extract_token_usage(history: list[BaseMessage]) -> dict[str, int]:
    """Extract token usage from the last AIMessage in the history."""
    for msg in reversed(history):
        if isinstance(msg, AIMessage):
            # Check for usage_metadata or response_metadata
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                return _flatten_token_usage(msg.usage_metadata)
            if hasattr(msg, "response_metadata") and msg.response_metadata:
                usage = msg.response_metadata.get("usage", {})
                if usage:
                    return _flatten_token_usage(usage)
    return {}
