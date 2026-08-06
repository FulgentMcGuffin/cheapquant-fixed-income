"""Batch bond-analytics CLI entry point.

Computes bond analytics for one or more issuers over a trade-date range and
writes the results directly into ``bond_analytics_db`` (``quant_cache_db`` is
never touched). Shows a themed progress GUI by default; pass ``--no-gui`` for
a plain console progress bar.

Run with uv (recommended)::

    uv run batch_bond_analytics.py --config ./config/cqfi.yaml \\
        --issuer FRA ITA --start 2020-01-01 --end 2020-12-31

If you execute this file directly from an IDE, it will re-launch using
``.venv`` when that environment exists (created by ``uv sync``) — same as
``main.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _ensure_venv() -> None:
    """Re-exec with the project venv when the package is not importable."""
    try:
        import cqfi  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        sys.exit(subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))

    print(
        "cqfi is not installed in this Python environment.\n"
        "\n"
        "This is a uv project — sync and run via uv:\n"
        "\n"
        "  uv sync\n"
        "  uv run batch_bond_analytics.py --issuer FRA --start 2020-01-01 --end 2020-01-31\n"
        "\n"
        f"Current interpreter: {sys.executable}\n"
        f"Expected venv:       {VENV_PYTHON}\n",
        file=sys.stderr,
    )
    sys.exit(1)


_ensure_venv()

import cqfi.config  # noqa: F401,E402 — disable LangSmith before langchain loads

from cqfi.batch.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
