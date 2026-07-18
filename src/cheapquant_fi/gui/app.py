"""Entry point for the CQFI GUI — launches ChatDialog with CQFI settings.

Why this wrapper exists
-----------------------
``chat_dialog.py`` was written as a standalone script and uses flat (bare)
module imports that only resolve when the ``gui/`` directory is on ``sys.path``::

    from chat_settings_dialog import ChatSettingsDialog
    from gui_constants import Theme, ...
    ...

``LlmWorker`` inside ``chat_dialog.py`` creates a ``DBClient()`` with no
arguments, which picks up its database path from environment variables
(``MCP_DB_PATH``, ``MCP_DATASET``, ``MCP_SEMANTICS_DIR``) defined by the
``mcp-data`` package.

This entry point therefore performs three tasks **before** importing anything
from the ``gui/`` package:

1. Load CQFI settings from ``config/cqfi.yaml`` (or a custom path).
2. Set the MCP environment variables so ``LlmWorker`` connects to the
   correct yield-curve database and its semantic profile.
3. Prepend the ``gui/`` directory to ``sys.path`` so the bare imports work.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ── LangSmith must be disabled before any langchain/mcp import ───────────────
from cheapquant_fi.config import configure_langsmith

configure_langsmith()

from cheapquant_fi.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    AppSettings,
    load_settings,
)

# Resolved at import time so they are available to helpers below.
_GUI_DIR = Path(__file__).resolve().parent


# ── sys.path bootstrap ────────────────────────────────────────────────────────

def _bootstrap_sys_path() -> None:
    """Prepend the ``gui/`` directory so chat_dialog's bare imports resolve."""
    gui_dir = str(_GUI_DIR)
    if gui_dir not in sys.path:
        sys.path.insert(0, gui_dir)


# ── MCP environment wiring ────────────────────────────────────────────────────

def _apply_mcp_env(app: AppSettings) -> None:
    """Propagate CQFI paths into the MCP environment variables.

    ``LlmWorker`` (inside ``chat_dialog.py``) calls ``DBClient()`` with no
    arguments; that call reads ``MCP_DB_PATH``, ``MCP_DATASET``, and
    ``MCP_SEMANTICS_DIR`` from the process environment.  We set them here from
    the loaded CQFI ``AppSettings`` so the GUI talks to the right database.

    ``setdefault`` is used throughout so that any explicit override set by the
    user in their shell or ``.env`` file is not silently clobbered.
    """
    os.environ.setdefault("MCP_DB_PATH", str(app.ycs_db_path))
    os.environ.setdefault("MCP_DATASET", app.ycs_dataset)
    os.environ.setdefault("MCP_SEMANTICS_DIR", str(app.ycs_semantics_dir))
    os.environ.setdefault("MCP_TRANSPORT", "stdio")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Parse CLI args, bootstrap the environment, then launch the ChatDialog."""
    parser = argparse.ArgumentParser(
        prog="cqfi-gui",
        description=(
            "CheapQuant Fixed Income — GUI chat interface.\n"
            "Provides the same data-query capability as `cqfi` but through\n"
            "the ChatDialog window with inline tables and plots."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            f"YAML config file for database and semantics paths.\n"
            f"Resolution order: --config flag → CQFI_CONFIG env var →\n"
            f"  {DEFAULT_CONFIG_PATH}."
        ),
    )
    # Forward unrecognised args to Qt (e.g. --platform, -style).
    args, qt_extra = parser.parse_known_args()

    # ── Config resolution ─────────────────────────────────────────────────────
    config_path: str | Path | None = args.config or os.environ.get("CQFI_CONFIG")

    app_settings = load_settings(config_path)
    app_settings.ensure_dirs()
    _apply_mcp_env(app_settings)

    # sys.path must be extended before any gui-module import.
    _bootstrap_sys_path()

    # ── Qt application ────────────────────────────────────────────────────────
    # Deferred import: PySide6 must not be imported before sys.path is ready
    # and MCP env vars are in place (otherwise LlmWorker may use wrong paths).
    from PySide6.QtGui import QFont  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    # chat_dialog lives in gui/ and is only importable after _bootstrap_sys_path.
    from chat_dialog import ChatDialog  # noqa: PLC0415

    qt_app = QApplication([sys.argv[0]] + qt_extra)
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    qt_app.setFont(font)

    window = ChatDialog()
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
