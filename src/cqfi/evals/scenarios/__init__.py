"""Example evaluation scenarios for testing the LLM agent."""

from cqfi.evals.scenarios.curve_queries import SCENARIOS as CURVE_SCENARIOS
from cqfi.evals.scenarios.multi_turn import SCENARIOS as MULTI_TURN_SCENARIOS

# Collect all scenarios for easy access
ALL_SCENARIOS = CURVE_SCENARIOS + MULTI_TURN_SCENARIOS

__all__ = ["ALL_SCENARIOS", "CURVE_SCENARIOS", "MULTI_TURN_SCENARIOS"]
