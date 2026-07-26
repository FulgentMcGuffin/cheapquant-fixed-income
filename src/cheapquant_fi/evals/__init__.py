"""LLM evaluation framework for cqfi's --llm mode.

Allows definition and execution of multi-turn scenarios with pass/fail criteria,
automated detection of regressions in prompt/semantics/model changes.
"""

from cheapquant_fi.evals.criteria import (
    contains_all,
    contains_none,
    no_tool_errors,
    numeric_within,
    regex_matches,
    tool_not_called,
    tool_was_called,
)
from cheapquant_fi.evals.judge import judge_response
from cheapquant_fi.evals.models import (
    Criterion,
    CriterionResult,
    Scenario,
    ScenarioResult,
    ToolCallRecord,
    Turn,
    TurnResult,
)
from cheapquant_fi.evals.report import print_summary, write_run_artifact
from cheapquant_fi.evals.runner import EvalRunner

__all__ = [
    # Models
    "Criterion",
    "CriterionResult",
    "Scenario",
    "ScenarioResult",
    "ToolCallRecord",
    "Turn",
    "TurnResult",
    # Criteria factory functions
    "contains_all",
    "contains_none",
    "no_tool_errors",
    "numeric_within",
    "regex_matches",
    "tool_not_called",
    "tool_was_called",
    # Judge
    "judge_response",
    # Runner
    "EvalRunner",
    # Reporting
    "print_summary",
    "write_run_artifact",
]
