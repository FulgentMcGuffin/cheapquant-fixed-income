"""Scenarios exercising multi-turn conversational memory."""

from __future__ import annotations

from cqfi.evals.criteria import contains_all, no_tool_errors
from cqfi.evals.models import Scenario, Turn

SCENARIOS = [
    Scenario(
        name="follow_up_without_repeating_context",
        target="input",
        turns=[
            Turn(
                user_input="What was Germany's 10Y zero rate on 2020-01-02?",
                criteria=[no_tool_errors(), contains_all("2020-01-02")],
            ),
            Turn(
                user_input="And what about the 5Y on the same date?",
                criteria=[
                    no_tool_errors(),
                    # Should use context from turn 1, not re-ask for issuer/date
                    contains_all("5Y", "2020-01-02", "DEU"),
                ],
            ),
        ],
        tags=["multi_turn", "conversational_memory"],
    ),
    Scenario(
        name="multiple_issuers_same_session",
        target="input",
        turns=[
            Turn(
                user_input="What is the current 10Y zero rate for France?",
                criteria=[
                    no_tool_errors(),
                    contains_all("FRA", "10"),
                ],
            ),
            Turn(
                user_input="Now show me Italy's 10Y zero rate.",
                criteria=[
                    no_tool_errors(),
                    contains_all("ITA", "10"),
                ],
            ),
            Turn(
                user_input="Compare the two rates.",
                criteria=[
                    no_tool_errors(),
                    # Should reference both issuers from prior context
                    contains_all("FRA", "ITA"),
                ],
            ),
        ],
        tags=["multi_turn", "conversational_memory", "comparison"],
    ),
]
