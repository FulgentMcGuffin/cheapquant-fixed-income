# Evaluator Framework Implementation Summary

## What was built

A complete LLM evaluation framework for testing the quality of `cqfi --llm` mode responses. The evaluator enables regression testing, A/B model comparison, and automated detection of prompt/semantics/tool-description regressions.

## Key components

### 1. **Core models** (`src/cheapquant_fi/evals/models.py`)
- `Scenario`, `Turn` — scenario definition (dataclass, holds callables)
- `TurnResult`, `ScenarioResult` — result models (pydantic, fully JSON-serializable)
- `Criterion` — protocol for evaluators
- `ToolCallRecord`, `CriterionResult` — granular result tracking

### 2. **Criteria library** (`src/cheapquant_fi/evals/criteria.py`)
Factory functions for common checks:
- Text matching: `contains_all`, `contains_none`, `regex_matches`
- Tool tracking: `tool_was_called`, `tool_not_called`, `no_tool_errors`
- Numeric validation: `numeric_within` with tolerance + custom extractors
- All return callables conforming to the `Criterion` protocol

### 3. **LLM-as-judge** (`src/cheapquant_fi/evals/judge.py`)
Lightweight wrapper around the Anthropic SDK for qualitative grading:
- Optional, additive to deterministic criteria
- Uses cheap/fast judge model (Claude Haiku) by default
- Fully JSON-structured input/output

### 4. **Multi-turn runner** (`src/cheapquant_fi/evals/runner.py`)
`EvalRunner` class that drives scenarios end-to-end:
- Builds LangGraph ReAct agent directly (not via `SQLAgent.run()`) for true multi-turn memory
- Threads message history across turns
- Extracts tool calls, answers, token usage from LangGraph state
- Runs all criteria, aggregates pass/fail
- Times each turn

### 5. **Reporting** (`src/cheapquant_fi/evals/report.py`)
- `print_summary()` — console table showing pass/fail, latency, failing criteria
- `write_run_artifact()` — JSON serialization to `data/evals/runs/` for historical comparison

### 6. **Example scenarios** (`src/cheapquant_fi/evals/scenarios/`)
- `curve_queries.py` — input-dataset NL questions (tables, schema, zero-rate lookups)
- `multi_turn.py` — follow-up scenarios testing conversational memory
- Expandable: easily add `cmt_pricing.py`, `adversarial.py`, etc.

## What it enables

### Regression testing
```bash
# Before prompt change
uv run pytest -m llm_eval
# -> run_20250115_093000.json

# After prompt change
uv run pytest -m llm_eval  
# -> run_20250115_095430.json

# Diff the two JSON files to see what broke
```

### Root-causing failures
When a criterion fails, `TurnResult` includes the full tool-call trace (SQL executed, tool outputs) and token usage — not just prose. You can pinpoint which tool was wrong, then fix the actual lever (system prompt, semantics YAML, tool docstring).

### Model A/B testing
```python
runner_sonnet4_5 = EvalRunner(app)  # default
runner_sonnet5 = EvalRunner(app, model=ChatAnthropic(model="claude-sonnet-5"))

# Run same scenarios on both, compare pass rates and latency
```

### Multi-turn memory verification
Example: if the agent is supposed to carry context forward ("and what about the 5Y on the same date?" should not re-ask for the issuer), multi-turn scenarios will catch when this breaks.

### Growing a golden set
Turn unexpected CLI/GUI behavior into reusable scenarios, building a living regression suite rather than one-off smoke tests.

## Test coverage

### Fast unit tests (no API key needed)
`tests/test_evals_harness.py` — 17 tests
- Criteria logic in isolation
- Message extraction helpers
- Reporting

Run with: `uv run pytest tests/test_evals_harness.py -v` (takes ~5s)

### End-to-end integration tests (requires API key)
`tests/test_eval_scenarios.py` — marked with `@pytest.mark.llm_eval`
- Skipped by default (no API key)
- Run explicitly: `ANTHROPIC_API_KEY=... uv run pytest -m llm_eval -v`
- Exercises real agent, writes JSON artifacts

## Design highlights

### Multi-turn memory (not available in current CLI)
The runner builds its own LangGraph ReAct agent instead of using `SQLAgent.run()` (which is single-message only). This lets the harness:
- Accumulate `HumanMessage` + `AIMessage` history across turns
- Re-invoke the agent with full history on each turn
- Test whether the agent actually remembers prior context

### Minimal new dependencies
- `anthropic` (already a dep)
- `langchain-core`, `langgraph`, `langchain-mcp-adapters` (all transitive via `mcp-data`)
- `pytest-asyncio` (added to dev deps for async test support)

### Reuse from `mcp_data`
- Imports underscore-prefixed helpers (`_build_model`, `_compose_system_prompt`) rather than duplicating
- Rationale: `mcp-data` is a sibling repo by the same author, acceptable to reach into internals for now
- Future: if friction shows, upstream a public `SQLAgent.run_turn(query, history)` method

### No modification to existing code paths
- Zero changes to `agent/cli.py`, `agent/planner.py`, or any production code
- Evaluator sits cleanly in a new `evals/` subpackage
- Existing unit tests all still pass

## File changes summary

### New files created
```
src/cheapquant_fi/evals/
  __init__.py
  models.py
  criteria.py
  judge.py
  runner.py
  report.py
  scenarios/
    __init__.py
    curve_queries.py
    multi_turn.py

tests/
  test_evals_harness.py       (17 unit tests, always runs)
  test_eval_scenarios.py       (3 integration tests, marked llm_eval)

docs/
  EVALUATOR.md                (complete usage guide)
```

### Modified files
```
pyproject.toml
  - Added pytest-asyncio dev dependency
  - Added pytest markers: llm_eval, asyncio

.gitignore
  - Added data/evals/runs/ (eval artifacts)
```

## Verification

1. **Unit tests pass** — `uv run pytest tests/test_evals_harness.py -v` (17/17 ✓)
2. **Integration tests collect** — `uv run pytest tests/test_eval_scenarios.py --collect-only` (3 tests collected)
3. **Imports work** — `uv run python -c "from cheapquant_fi.evals import *"`
4. **No breaking changes** — existing `uv run pytest` (non-marked tests) still work

## How to use it now

### Quick start
```bash
# Unit tests (always available)
uv run pytest tests/test_evals_harness.py -v

# Integration tests (requires API key)
export ANTHROPIC_API_KEY=sk-ant-...
uv run pytest -m llm_eval -v
```

### Add custom scenarios
```python
# In src/cheapquant_fi/evals/scenarios/my_domain.py
from cheapquant_fi.evals import Scenario, Turn, no_tool_errors

SCENARIOS = [
    Scenario(
        name="my_test",
        target="input",
        turns=[
            Turn(
                user_input="Your query here",
                criteria=[no_tool_errors()],
            ),
        ],
    ),
]

# Register in scenarios/__init__.py
from cheapquant_fi.evals.scenarios.my_domain import SCENARIOS as MY_SCENARIOS
ALL_SCENARIOS = ... + MY_SCENARIOS
```

### Programmatic usage
```python
import asyncio
from cheapquant_fi.config import load_settings
from cheapquant_fi.evals import EvalRunner, print_summary

async def main():
    app = load_settings()
    runner = EvalRunner(app)
    result = await runner.run_scenario(my_scenario)
    print_summary([result])

asyncio.run(main())
```

See `docs/EVALUATOR.md` for full documentation.

## Future enhancements (out of scope for this PR)

1. **Upstream `SQLAgent.run_turn()`** to `mcp-data` — would eliminate reaching into private APIs
2. **CMT/bond pricing scenarios** — use QuantLib to compute ground-truth prices, grade against those
3. **Adversarial scenarios** — test SQL injection, out-of-scope issuers, ambiguous routing
4. **Scheduled CI/nightly runs** — run evaluator on a schedule, track pass-rate trends in a database
5. **Dashboard** — web UI to browse runs, diff before/after, trend graphs
6. **Prompt optimization** — use eval framework to systematically improve prompt text (e.g., via prompt engineering, few-shot examples)
