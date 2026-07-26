"""Scenarios for input-dataset queries (yield curves, zero rates)."""

from __future__ import annotations

from cheapquant_fi.evals.criteria import (
    contains_all,
    no_tool_errors,
    tool_was_called,
)
from cheapquant_fi.evals.models import Scenario, Turn

SCENARIOS = [
    Scenario(
        name="zero_rate_lookup_Germany",
        target="input",
        turns=[
            Turn(
                user_input="What was Germany's 10Y zero rate on 2020-01-02?",
                criteria=[
                    tool_was_called("run_sql"),
                    no_tool_errors(),
                    contains_all("DEU", "2020-01-02", "10"),
                ],
            )
        ],
        tags=["zero_rates", "single_turn"],
    ),
    Scenario(
        name="list_tables_query",
        target="input",
        turns=[
            Turn(
                user_input="What tables are available in this dataset?",
                criteria=[
                    tool_was_called("list_tables"),
                    no_tool_errors(),
                    contains_all("zero_rates", "par_rates"),
                ],
            )
        ],
        tags=["schema", "single_turn"],
    ),
    Scenario(
        name="schema_introspection",
        target="input",
        turns=[
            Turn(
                user_input="Show me the schema for the zero_rates table.",
                criteria=[
                    tool_was_called("get_schema"),
                    no_tool_errors(),
                ],
            )
        ],
        tags=["schema", "single_turn"],
    ),
    Scenario(
        name="US_rates_query",
        target="input",
        turns=[
            Turn(
                user_input="What were the average US zero rates in 2017?",
                criteria=[
                    tool_was_called("run_sql"),
                    no_tool_errors(),
                    contains_all("2017"),
                ],
            )
        ],
        tags=["zero_rates", "single_turn"],
    ),
]
