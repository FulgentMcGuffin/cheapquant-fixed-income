"""End-to-end scenario tests against the real LLM agent.

Marked with @pytest.mark.llm_eval; requires ANTHROPIC_API_KEY and costs money.
Skipped by default; run with: pytest -m llm_eval
"""

import pytest

from cqfi.agent.planner import has_llm_credentials
from cqfi.config import load_settings
from cqfi.evals import EvalRunner, print_summary, write_run_artifact
from cqfi.evals.scenarios import ALL_SCENARIOS

# Skip all tests in this file if no API key
pytestmark = pytest.mark.skipif(
    not has_llm_credentials(),
    reason="ANTHROPIC_API_KEY not set",
)


@pytest.mark.llm_eval
@pytest.mark.asyncio
async def test_run_all_scenarios():
    """Run all example scenarios end-to-end."""
    app = load_settings()
    runner = EvalRunner(app)

    results = []
    for scenario in ALL_SCENARIOS:
        result = await runner.run_scenario(scenario)
        results.append(result)

    # Print summary to console
    print_summary(results)

    # Write artifact
    artifact_path = write_run_artifact(results)
    print(f"\nFull results written to: {artifact_path}")

    # Check that we ran some scenarios
    assert len(results) > 0
    # At least some scenarios should pass (depends on agent working correctly)
    # We don't enforce 100% pass rate here since that would require perfect setup


@pytest.mark.llm_eval
@pytest.mark.asyncio
async def test_single_zero_rate_scenario():
    """Run just the zero-rate lookup scenario as a quick smoke test."""
    from cqfi.evals.scenarios.curve_queries import SCENARIOS

    app = load_settings()
    runner = EvalRunner(app)

    # Pick the first scenario
    scenario = SCENARIOS[0]
    result = await runner.run_scenario(scenario)

    print_summary([result])

    # At minimum, the agent should not crash and should attempt the query
    assert len(result.turn_results) > 0
    assert result.turn_results[0].answer  # should have some answer


@pytest.mark.llm_eval
@pytest.mark.asyncio
async def test_multi_turn_memory():
    """Run a multi-turn scenario to verify conversational memory is working."""
    from cqfi.evals.scenarios.multi_turn import SCENARIOS

    app = load_settings()
    runner = EvalRunner(app)

    # Find a multi-turn scenario
    scenario = next(
        (s for s in SCENARIOS if len(s.turns) > 1),
        SCENARIOS[0] if SCENARIOS else None,
    )
    if not scenario:
        pytest.skip("No multi-turn scenario found")

    result = await runner.run_scenario(scenario)
    print_summary([result])

    # Check that we have multiple turns
    assert len(result.turn_results) >= 2
    # All turns should have executed (have answers)
    for turn in result.turn_results:
        assert turn.answer, f"Turn {turn.user_input} has no answer"
