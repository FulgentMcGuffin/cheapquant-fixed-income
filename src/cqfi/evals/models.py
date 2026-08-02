"""Data models for eval scenarios and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict


class Criterion(Protocol):
    """A callable that evaluates a turn result and returns a CriterionResult."""

    def __call__(self, turn_result: TurnResult) -> CriterionResult: ...


@dataclass(frozen=True)
class Turn:
    """A single user input in a scenario, with criteria to evaluate its response."""

    user_input: str
    criteria: list[Criterion] = field(default_factory=list)


@dataclass
class Scenario:
    """A multi-turn scenario for testing the LLM agent."""

    name: str
    target: str  # dataset: "input", "cache", "bond_analytics"
    turns: list[Turn]
    tags: list[str] = field(default_factory=list)


class CriterionResult(BaseModel):
    """Result of evaluating a single criterion."""

    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    detail: str


class ToolCallRecord(BaseModel):
    """Record of a tool call made by the agent."""

    model_config = ConfigDict(frozen=True)

    name: str
    arguments: dict[str, Any]
    result_summary: str  # truncated result text or error message


class TurnResult(BaseModel):
    """Result of a single turn in a scenario."""

    model_config = ConfigDict(frozen=True)

    user_input: str
    answer: str  # final prose answer from the agent
    tool_calls: list[ToolCallRecord]
    token_usage: dict[str, int]  # {"input_tokens": N, "output_tokens": N, ...}
    latency_ms: float
    criteria_results: list[CriterionResult]

    @property
    def passed(self) -> bool:
        return all(cr.passed for cr in self.criteria_results)


class ScenarioResult(BaseModel):
    """Result of running a full scenario."""

    model_config = ConfigDict(frozen=True)

    scenario_name: str
    tags: list[str]
    turn_results: list[TurnResult]

    @property
    def passed(self) -> bool:
        return all(tr.passed for tr in self.turn_results)
