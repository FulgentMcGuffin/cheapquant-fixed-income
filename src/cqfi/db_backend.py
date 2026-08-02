"""Backend selection for the project's read-only databases.

Configured paths may point at either DuckDB or SQLite files, so every reader
picks its backend from the file suffix rather than assuming one.
"""

from __future__ import annotations

from pathlib import Path

from mcp_data.backends.duckdb_backend import DuckDBSource
from mcp_data.backends.sqlite_backend import SQLiteSource

DUCKDB_SUFFIX = ".duckdb"


def source_class_for(db_path: Path | str) -> type[DuckDBSource] | type[SQLiteSource]:
    """Return the backend class matching *db_path*'s file suffix."""
    return (
        DuckDBSource if str(db_path).lower().endswith(DUCKDB_SUFFIX) else SQLiteSource
    )
