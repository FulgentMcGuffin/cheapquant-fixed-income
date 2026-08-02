"""Factory functions for common evaluation criteria."""

from __future__ import annotations

import re
from typing import Any, Callable

from cqfi.evals.models import CriterionResult, TurnResult


def contains_all(*substrings: str) -> Callable[[TurnResult], CriterionResult]:
    """Check that answer contains all given substrings."""

    def check(turn: TurnResult) -> CriterionResult:
        answer_lower = turn.answer.lower()
        missing = [s for s in substrings if s.lower() not in answer_lower]
        passed = len(missing) == 0
        detail = (
            f"Found all substrings"
            if passed
            else f"Missing: {missing}"
        )
        return CriterionResult(
            name=f"contains_all({', '.join(repr(s) for s in substrings)})",
            passed=passed,
            detail=detail,
        )

    return check


def contains_none(*substrings: str) -> Callable[[TurnResult], CriterionResult]:
    """Check that answer contains none of the given substrings."""

    def check(turn: TurnResult) -> CriterionResult:
        answer_lower = turn.answer.lower()
        found = [s for s in substrings if s.lower() in answer_lower]
        passed = len(found) == 0
        detail = (
            "Found no forbidden substrings"
            if passed
            else f"Found forbidden: {found}"
        )
        return CriterionResult(
            name=f"contains_none({', '.join(repr(s) for s in substrings)})",
            passed=passed,
            detail=detail,
        )

    return check


def regex_matches(pattern: str) -> Callable[[TurnResult], CriterionResult]:
    """Check that answer matches a regex pattern."""

    def check(turn: TurnResult) -> CriterionResult:
        passed = bool(re.search(pattern, turn.answer))
        detail = (
            f"Matched pattern: {pattern}"
            if passed
            else f"Did not match: {pattern}"
        )
        return CriterionResult(
            name=f"regex_matches({pattern!r})",
            passed=passed,
            detail=detail,
        )

    return check


def tool_was_called(
    name: str, arg_matcher: Callable[[dict[str, Any]], bool] | None = None
) -> Callable[[TurnResult], CriterionResult]:
    """Check that a specific tool was called (with optional argument matcher)."""

    def check(turn: TurnResult) -> CriterionResult:
        for tool_call in turn.tool_calls:
            if tool_call.name == name:
                if arg_matcher is None or arg_matcher(tool_call.arguments):
                    return CriterionResult(
                        name=f"tool_was_called({name!r})",
                        passed=True,
                        detail=f"Tool {name} was called",
                    )
        detail = f"Tool {name} was never called"
        if tool_call := next(
            (tc for tc in turn.tool_calls if tc.name == name), None
        ):
            detail += f" (called but arguments didn't match)"
        return CriterionResult(
            name=f"tool_was_called({name!r})",
            passed=False,
            detail=detail,
        )

    return check


def tool_not_called(name: str) -> Callable[[TurnResult], CriterionResult]:
    """Check that a specific tool was NOT called."""

    def check(turn: TurnResult) -> CriterionResult:
        called = [tc for tc in turn.tool_calls if tc.name == name]
        passed = len(called) == 0
        detail = (
            f"Tool {name} was not called"
            if passed
            else f"Tool {name} was called {len(called)} time(s)"
        )
        return CriterionResult(
            name=f"tool_not_called({name!r})",
            passed=passed,
            detail=detail,
        )

    return check


def no_tool_errors() -> Callable[[TurnResult], CriterionResult]:
    """Check that no tool calls resulted in errors."""

    def check(turn: TurnResult) -> CriterionResult:
        errors = [
            tc for tc in turn.tool_calls
            if any(
                err_word in tc.result_summary.lower()
                for err_word in ("error", "exception", "failed", "invalid")
            )
        ]
        passed = len(errors) == 0
        detail = (
            "No tool errors"
            if passed
            else f"Tools with errors: {[e.name for e in errors]}"
        )
        return CriterionResult(
            name="no_tool_errors",
            passed=passed,
            detail=detail,
        )

    return check


def numeric_within(
    expected: float,
    tolerance: float,
    extractor: Callable[[str], float | None] | None = None,
) -> Callable[[TurnResult], CriterionResult]:
    """Check that a number in the answer is within tolerance of expected value.

    Args:
        expected: ground truth value
        tolerance: absolute tolerance (e.g., 0.01)
        extractor: optional function to extract number from answer; default is last float-like token
    """

    def check(turn: TurnResult) -> CriterionResult:
        try:
            if extractor:
                actual = extractor(turn.answer)
            else:
                actual = _extract_last_number(turn.answer)

            if actual is None:
                return CriterionResult(
                    name=f"numeric_within({expected}, tol={tolerance})",
                    passed=False,
                    detail="Could not extract a number from answer",
                )

            diff = abs(actual - expected)
            passed = diff <= tolerance
            detail = (
                f"Value {actual} within ±{tolerance} of {expected}"
                if passed
                else f"Value {actual} differs by {diff:.6f} from {expected} (tolerance: {tolerance})"
            )
            return CriterionResult(
                name=f"numeric_within({expected}, tol={tolerance})",
                passed=passed,
                detail=detail,
            )
        except Exception as e:
            return CriterionResult(
                name=f"numeric_within({expected}, tol={tolerance})",
                passed=False,
                detail=f"Error during numeric extraction: {e}",
            )

    return check


def _extract_last_number(text: str) -> float | None:
    """Extract the last number-like token from text."""
    # Match floating-point numbers, including scientific notation
    matches = re.findall(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text)
    if matches:
        try:
            return float(matches[-1])
        except ValueError:
            pass
    return None
