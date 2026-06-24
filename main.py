"""CheapQuant Fixed Income CLI entry point.

Run with uv (recommended)::

    uv run cqfi
    uv run main.py

If you execute this file directly from an IDE, it will re-launch using
``.venv`` when that environment exists (created by ``uv sync``).
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
        import cheapquant_fi  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        sys.exit(subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))

    print(
        "cheapquant_fi is not installed in this Python environment.\n"
        "\n"
        "This is a uv project — sync and run via uv:\n"
        "\n"
        "  uv sync\n"
        "  uv run cqfi\n"
        "  uv run main.py\n"
        "\n"
        f"Current interpreter: {sys.executable}\n"
        f"Expected venv:       {VENV_PYTHON}\n",
        file=sys.stderr,
    )
    sys.exit(1)


_ensure_venv()

import cheapquant_fi.config  # noqa: F401,E402 — disable LangSmith before langchain loads

from cheapquant_fi.agent.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
