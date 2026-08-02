# LLM Evaluator Framework

This project includes a lightweight evaluation framework for testing the quality of responses from the `--llm` mode of the cqfi agent.

## Overview

The evaluator lets you:
- **Define scenarios** as multi-turn conversation sequences with specific pass/fail criteria
- **Run scenarios** against the real LLM agent (or a mock in tests)
- **Detect regressions** when prompt/semantics/tool-description changes break existing behavior
- **A/B test models** by running the same scenarios against different Claude versions
- **Track trends** by persisting results to JSON artifacts for historical comparison

## Quick Start

### Running fast unit tests (no API key needed)

```bash
uv run pytest tests/test_evals_harness.py -v
```

This tests the criteria library, message extraction, and reporting in isolation. All tests pass if the harness logic is correct, regardless of LLM behavior.

### Running full end-to-end scenarios (requires API key)

```bash
# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run all scenarios marked with @pytest.mark.llm_eval
uv run pytest -m llm_eval -v

# Run just one scenario for a quick check
uv run pytest -m llm_eval -k test_single_zero_rate_scenario -v
```

Results are printed to console (summary table) and written as JSON to `data/evals/runs/run_YYYYMMDD_HHMMSS.json` for later analysis.

## Defining Custom Scenarios

A scenario is a sequence of user inputs with criteria for evaluating each response.

```python
from cqfi.evals import (
    Scenario, Turn,
    contains_all, no_tool_errors, tool_was_called, numeric_within
)

scenario = Scenario(
    name="my_test",
    target="input",  # or "cache" / "bond_analytics"
    turns=[
        Turn(
            user_input="What was Germany's 10Y zero rate on 2020-01-02?",
            criteria=[
                tool_was_called("run_sql"),
                no_tool_errors(),
                contains_all("DEU", "2020-01-02", "10"),
            ],
        ),
    ],
    tags=["zero_rates", "single_turn"],
)
```

### Built-in Criteria

- **`contains_all(*substrings)`** — answer includes all given strings (case-insensitive)
- **`contains_none(*substrings)`** — answer includes none of the given strings
- **`regex_matches(pattern)`** — answer matches regex
- **`tool_was_called(name, arg_matcher=None)`** — specific tool was invoked (with optional argument check)
- **`tool_not_called(name)`** — specific tool was NOT invoked
- **`no_tool_errors()`** — none of the tool calls resulted in errors
- **`numeric_within(expected, tolerance, extractor=None)`** — extracts a number from the answer and checks it's within tolerance of an expected value; default extractor pulls the last float-like token

### Custom Criteria

A criterion is a callable taking a `TurnResult` and returning a `CriterionResult`:

```python
from cqfi.evals.models import TurnResult, CriterionResult

def my_criterion(turn: TurnResult) -> CriterionResult:
    passed = len(turn.answer) > 100  # just an example
    return CriterionResult(
        name="answer_length",
        passed=passed,
        detail="Answer has sufficient length" if passed else "Answer too short",
    )

# Use it in a Turn
Turn(
    user_input="...",
    criteria=[my_criterion],
)
```

## Multi-Turn Scenarios (Conversational Memory)

The evaluator threads LangGraph message history across turns, so later turns can reference context from earlier ones:

```python
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
                # This should work without the agent re-asking for issuer/date
                contains_all("5Y", "2020-01-02", "DEU"),
            ],
        ),
    ],
)
```

This is a key difference from the current CLI behavior (which is stateless). The evaluator builds real multi-turn memory to catch issues like "did the agent remember the issuer across turns?"

## Using the Evaluator Programmatically

```python
import asyncio
from cqfi.config import load_settings
from cqfi.evals import EvalRunner, print_summary, write_run_artifact

async def main():
    app = load_settings()
    runner = EvalRunner(app)  # model=None uses default (claude-sonnet-4-5)

    # Run a single scenario
    scenario = ...  # your Scenario object
    result = await runner.run_scenario(scenario)
    
    print_summary([result])
    write_run_artifact([result])

asyncio.run(main())
```

### A/B Testing Models

```python
from langchain_anthropic import ChatAnthropic

# Test with a different model
model_sonnet5 = ChatAnthropic(model="claude-sonnet-5")
runner_sonnet5 = EvalRunner(app, model=model_sonnet5)

# Run the same scenarios against both models
results_sonnet4_5 = [await runner.run_scenario(s) for s in scenarios]
results_sonnet5 = [await runner_sonnet5.run_scenario(s) for s in scenarios]

# Compare results (e.g., pass rates, latencies)
```

## Interpreting Results

Each `TurnResult` includes:
- **`answer`** — final prose response from the agent
- **`tool_calls`** — list of `ToolCallRecord`s showing which tools were invoked and their results
- **`token_usage`** — flat `dict[str, int]` with `input_tokens`, `output_tokens` (if the
  model provides them). LangChain's nested `input_token_details` /
  `output_token_details` breakdowns are flattened to entries such as
  `input_token_cache_read`, so the mapping is always scalar counts.
- **`latency_ms`** — wall-clock time for this turn
- **`criteria_results`** — list of `CriterionResult`s; `passed` is True if all are True

Failed criteria include a `detail` field explaining why:
```
{
  "name": "numeric_within(98.75, tol=0.1)",
  "passed": false,
  "detail": "Value 105.3 differs by 6.55 from 98.75 (tolerance: 0.1)"
}
```

The full JSON artifact written to `data/evals/runs/` includes complete tool-call traces, so you can inspect exactly which SQL the agent ran and what results came back.

## Using LLM-as-Judge

For qualitative criteria that don't fit into deterministic checks, the evaluator can use Claude as a judge:

```python
from cqfi.evals.judge import judge_response

criterion = judge_response(
    question="What is the 10Y CMT price for Germany?",
    answer=turn_result.answer,
    rubric="Is the answer clear and does it cite the SQL query that was run?",
    model="claude-haiku-4-5-20251001",  # cheap/fast judge
)
# criterion is a CriterionResult with pass/detail from Claude's evaluation
```

This incurs an extra API call per criterion, but is useful for checking:
- Clarity and tone of the answer
- Whether the agent cited its SQL (as per its system prompt)
- Hallucination detection (e.g., "does the answer invent numbers?")

## Regression Testing

Before and after changing the agent's system prompt, semantics YAML, or tool descriptions:

```bash
# Baseline run (before change)
ANTHROPIC_API_KEY=... uv run pytest -m llm_eval -v
# -> writes data/evals/runs/run_20250115_093000.json

# Make your change (e.g., edit _AGENT_SYSTEM_PROMPT or a semantics.yaml)

# After-change run
ANTHROPIC_API_KEY=... uv run pytest -m llm_eval -v
# -> writes data/evals/runs/run_20250115_095430.json

# Compare the two JSON files
```

If a scenario flips from pass to fail, the JSON includes the full tool-call and token-usage trace to help you understand what changed.

## Architecture Notes

- **Models** (`models.py`) — defines `Scenario`, `Turn`, `CriterionResult`, `TurnResult`, `ScenarioResult` (pydantic for JSON serialization)
- **Criteria** (`criteria.py`) — factory functions returning callables; pure logic, no API calls
- **Judge** (`judge.py`) — lightweight wrapper around the `anthropic` SDK for optional LLM grading
- **Runner** (`runner.py`) — `EvalRunner` class that drives the LangGraph agent directly (bypassing `SQLAgent.run()` to get true multi-turn memory)
- **Report** (`report.py`) — console summary table and JSON persistence
- **Scenarios** (`scenarios/`) — example scenario modules (curve_queries, multi_turn, etc.)

The runner reuses private helpers from `mcp_data.client.agent` (`_build_model`, `_compose_system_prompt`) rather than duplicating their logic. If reaching into private APIs becomes a friction point, consider upstreaming a public `SQLAgent.run_turn(query, history=None)` method to `mcp-data`.

## Troubleshooting

### Tests are skipped ("ANTHROPIC_API_KEY not set")
This is expected when running without an API key. Only run `pytest -m llm_eval` when you have `ANTHROPIC_API_KEY` set in your environment.

### Tests timeout or fail with network errors
The evaluator opens one `DBClient` per scenario (spawning an MCP subprocess) and makes real LLM API calls. Ensure:
- `ANTHROPIC_API_KEY` is valid
- Your network can reach `api.anthropic.com`
- The configured `ycs_db` / `bond_analytics_db` databases exist and are accessible

### A scenario passes locally but fails in CI
Multi-turn scenarios rely on `load_mcp_tools()` fetching live tool schemas from an MCP session. If the MCP server (spawned as a subprocess) crashes or the semantics YAML is missing, the scenario can fail silently. Check that your CI environment has the required databases and config files.

## See Also

- [CLAUDE.md](../CLAUDE.md) — project conventions and architecture
- `src/cqfi/evals/` — source code
- `tests/test_evals_harness.py` — unit test examples
- `tests/test_eval_scenarios.py` — end-to-end test template
