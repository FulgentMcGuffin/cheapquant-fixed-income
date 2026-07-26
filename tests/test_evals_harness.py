"""Unit tests for eval harness: criteria, message extraction, report generation."""

import pytest

from cheapquant_fi.evals.criteria import (
    contains_all,
    contains_none,
    no_tool_errors,
    numeric_within,
    regex_matches,
    tool_not_called,
    tool_was_called,
)
from cheapquant_fi.evals.models import (
    CriterionResult,
    ToolCallRecord,
    TurnResult,
)
from cheapquant_fi.evals.report import print_summary


@pytest.fixture
def sample_turn() -> TurnResult:
    """A sample turn result for testing criteria."""
    return TurnResult(
        user_input="What is the 10Y CMT price for Germany?",
        answer="The 10Y CMT price for Germany on 2020-01-02 was 98.75.",
        tool_calls=[
            ToolCallRecord(
                name="run_sql",
                arguments={"sql": "SELECT ..."},
                result_summary="(1 row)",
            ),
        ],
        token_usage={"input_tokens": 120, "output_tokens": 45},
        latency_ms=1234.5,
        criteria_results=[],
    )


class TestContainsCriteria:
    def test_contains_all_passes(self, sample_turn):
        criterion = contains_all("CMT", "Germany")
        result = criterion(sample_turn)
        assert result.passed
        assert "Found all substrings" in result.detail

    def test_contains_all_fails(self, sample_turn):
        criterion = contains_all("CMT", "France")
        result = criterion(sample_turn)
        assert not result.passed
        assert "Missing" in result.detail

    def test_contains_none_passes(self, sample_turn):
        criterion = contains_none("error", "failed")
        result = criterion(sample_turn)
        assert result.passed

    def test_contains_none_fails(self, sample_turn):
        criterion = contains_none("CMT", "Germany")
        result = criterion(sample_turn)
        assert not result.passed
        assert "forbidden" in result.detail.lower()

    def test_regex_matches_passes(self, sample_turn):
        criterion = regex_matches(r"\d+\.\d+")
        result = criterion(sample_turn)
        assert result.passed

    def test_regex_matches_fails(self, sample_turn):
        criterion = regex_matches(r"IMPOSSIBLE\d+")
        result = criterion(sample_turn)
        assert not result.passed


class TestToolCriteria:
    def test_tool_was_called_passes(self, sample_turn):
        criterion = tool_was_called("run_sql")
        result = criterion(sample_turn)
        assert result.passed

    def test_tool_was_called_fails(self, sample_turn):
        criterion = tool_was_called("get_schema")
        result = criterion(sample_turn)
        assert not result.passed
        assert "never called" in result.detail

    def test_tool_not_called_passes(self, sample_turn):
        criterion = tool_not_called("get_schema")
        result = criterion(sample_turn)
        assert result.passed

    def test_tool_not_called_fails(self, sample_turn):
        criterion = tool_not_called("run_sql")
        result = criterion(sample_turn)
        assert not result.passed


class TestNoToolErrors:
    def test_no_errors(self, sample_turn):
        criterion = no_tool_errors()
        result = criterion(sample_turn)
        assert result.passed

    def test_error_detected(self, sample_turn):
        # Add a tool call with an error
        sample_turn.tool_calls.append(
            ToolCallRecord(
                name="run_sql",
                arguments={},
                result_summary="Error: syntax error in SQL",
            )
        )
        criterion = no_tool_errors()
        result = criterion(sample_turn)
        assert not result.passed
        assert "error" in result.detail.lower()


class TestNumericCriteria:
    def test_numeric_within_passes(self, sample_turn):
        criterion = numeric_within(expected=98.75, tolerance=0.1)
        result = criterion(sample_turn)
        assert result.passed

    def test_numeric_within_fails_out_of_tolerance(self, sample_turn):
        criterion = numeric_within(expected=100.0, tolerance=0.1)
        result = criterion(sample_turn)
        assert not result.passed

    def test_numeric_within_no_number(self):
        turn = TurnResult(
            user_input="What?",
            answer="No numbers here.",
            tool_calls=[],
            token_usage={},
            latency_ms=100.0,
            criteria_results=[],
        )
        criterion = numeric_within(expected=42.0, tolerance=1.0)
        result = criterion(turn)
        assert not result.passed
        assert "Could not extract" in result.detail

    def test_numeric_with_custom_extractor(self, sample_turn):
        def extract_98_75(text: str) -> float | None:
            if "98.75" in text:
                return 98.75
            return None

        criterion = numeric_within(expected=98.75, tolerance=0.0, extractor=extract_98_75)
        result = criterion(sample_turn)
        assert result.passed


class TestReporting:
    def test_print_summary_no_crash(self, sample_turn, capsys):
        """Just ensure print_summary doesn't crash."""
        from cheapquant_fi.evals.models import Scenario, ScenarioResult, Turn

        scenario = Scenario(
            name="test",
            target="input",
            turns=[Turn(user_input="test", criteria=[])],
        )
        result = ScenarioResult(
            scenario_name=scenario.name,
            tags=[],
            turn_results=[sample_turn],
        )
        print_summary([result])
        captured = capsys.readouterr()
        assert "SCENARIO EVALUATION SUMMARY" in captured.out
        assert "test" in captured.out
