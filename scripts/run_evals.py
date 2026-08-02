#!/usr/bin/env python
"""Quick runner for eval scenarios.

Usage:
    uv run scripts/run_evals.py [--scenarios SCENARIO1,SCENARIO2,...] [--tag TAG] [--model MODEL]

Examples:
    uv run scripts/run_evals.py                              # Run all scenarios
    uv run scripts/run_evals.py --tag multi_turn             # Run multi-turn scenarios only
    uv run scripts/run_evals.py --scenarios zero_rate_lookup_Germany
    uv run scripts/run_evals.py --model claude-opus-5        # Use different model
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from langchain_anthropic import ChatAnthropic

from cqfi.config import get_settings
from cqfi.evals import EvalRunner, print_summary, write_run_artifact
from cqfi.evals.scenarios import ALL_SCENARIOS


async def main():
    parser = argparse.ArgumentParser(
        description="Run evaluation scenarios against the LLM agent"
    )
    parser.add_argument(
        "--scenarios",
        help="Comma-separated scenario names (default: all)",
        type=str,
    )
    parser.add_argument(
        "--tag",
        help="Filter scenarios by tag (e.g., 'multi_turn', 'zero_rates')",
        type=str,
    )
    parser.add_argument(
        "--model",
        help="Claude model to use (default: claude-sonnet-4-5)",
        type=str,
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for JSON artifacts (default: data/evals/runs/)",
        type=Path,
    )

    args = parser.parse_args()

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    # Filter scenarios
    scenarios = ALL_SCENARIOS
    if args.scenarios:
        scenario_names = set(args.scenarios.split(","))
        scenarios = [s for s in scenarios if s.name in scenario_names]
    if args.tag:
        scenarios = [s for s in scenarios if args.tag in s.tags]

    if not scenarios:
        print("No scenarios matched the filters", file=sys.stderr)
        sys.exit(1)

    print(f"Running {len(scenarios)} scenario(s)...")
    print()

    # Build model if specified
    model = None
    if args.model:
        model = ChatAnthropic(model=args.model)

    app = get_settings()
    runner = EvalRunner(app, model=model)

    results = []
    for scenario in scenarios:
        print(f"[{scenario.name}]", end=" ", flush=True)
        result = await runner.run_scenario(scenario)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(status)

    print()
    print_summary(results)

    # Write artifacts
    output_dir = args.output_dir or Path("data/evals/runs")
    artifact_path = write_run_artifact(results, output_dir)
    print(f"Results written to: {artifact_path}")


if __name__ == "__main__":
    asyncio.run(main())
