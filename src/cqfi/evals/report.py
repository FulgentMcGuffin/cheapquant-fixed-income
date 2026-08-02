"""Reporting for evaluation runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from cqfi.evals.models import ScenarioResult


def print_summary(results: list[ScenarioResult], file: TextIO | None = None) -> None:
    """Print a concise summary table to stdout (or file)."""
    import sys

    file = file or sys.stdout

    # Header
    file.write("\n" + "=" * 100 + "\n")
    file.write("SCENARIO EVALUATION SUMMARY\n")
    file.write("=" * 100 + "\n")
    file.write(
        f"{'Scenario':<40} {'Tags':<20} {'Status':<8} {'Turns':<8} {'Latency (ms)':<15}\n"
    )
    file.write("-" * 100 + "\n")

    total_passed = 0
    total_scenarios = len(results)

    for result in results:
        status = "✓ PASS" if result.passed else "✗ FAIL"
        if result.passed:
            total_passed += 1

        tags_str = ",".join(result.tags) if result.tags else "-"
        latency_str = (
            f"{sum(tr.latency_ms for tr in result.turn_results):.1f}"
        )

        file.write(
            f"{result.scenario_name:<40} {tags_str:<20} {status:<8} "
            f"{len(result.turn_results):<8} {latency_str:<15}\n"
        )

        # Print failing criteria for this scenario
        for turn_idx, turn_result in enumerate(result.turn_results):
            if not turn_result.passed:
                for criterion in turn_result.criteria_results:
                    if not criterion.passed:
                        file.write(
                            f"  └─ Turn {turn_idx + 1}: {criterion.name} — {criterion.detail}\n"
                        )

    file.write("=" * 100 + "\n")
    file.write(f"Total: {total_passed}/{total_scenarios} passed\n")
    file.write("=" * 100 + "\n")


def write_run_artifact(
    results: list[ScenarioResult], out_dir: Path | None = None
) -> Path:
    """Write a JSON artifact of the full run for later analysis.

    Args:
        results: scenario results to persist
        out_dir: output directory (default: data/evals/runs/)

    Returns:
        Path to the written JSON file
    """
    if out_dir is None:
        out_dir = Path("data/evals/runs")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Filename with UTC timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"run_{timestamp}.json"

    # Serialize all results
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenarios": [r.model_dump() for r in results],
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
        },
    }

    with out_path.open("w") as f:
        json.dump(data, f, indent=2)

    return out_path
