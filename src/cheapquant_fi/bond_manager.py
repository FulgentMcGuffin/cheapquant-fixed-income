"""Singleton registry for :class:`Bond` instances loaded from bond_universe."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from mcp_data.backends.duckdb_backend import DuckDBSource
from mcp_data.backends.sqlite_backend import SQLiteSource

from cheapquant_fi.config import get_settings
from cheapquant_fi.instruments import Bond


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _fetch_bond_row(
    db_path: Path | str,
    id_value: str,
) -> Mapping[str, Any] | None:
    """Load one ``bond_universe`` row by ``user_friendly_id``, then ``bond_id``."""
    escaped = _escape_sql_literal(id_value)
    queries = (
        f"SELECT * FROM bond_universe WHERE user_friendly_id = '{escaped}'",
        f"SELECT * FROM bond_universe WHERE bond_id = '{escaped}'",
    )
    db_str = str(db_path).lower()
    is_duckdb = db_str.endswith(".duckdb")
    source_class = DuckDBSource if is_duckdb else SQLiteSource
    with source_class(db_path, read_only=True) as db:
        for sql in queries:
            frame = db.run_query(sql)
            if not frame.is_empty():
                return frame.row(0, named=True)
    return None


class BondManager:
    """Process-wide cache of :class:`Bond` instances keyed by identifier.

    Bonds are loaded lazily from ``bond_universe`` in ``bond_analytics_db``
    (see ``config/cqfi.yaml``).  Each stored bond is indexed by both
    ``user_friendly_id`` and ``bond_id`` when present.  Lookups try
    ``user_friendly_id`` before ``bond_id``.
    """

    _instance: BondManager | None = None

    def __new__(cls) -> BondManager:
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._by_user_friendly_id: dict[str, Bond] = {}
            obj._by_bond_id: dict[str, Bond] = {}
            cls._instance = obj
        return cls._instance

    @classmethod
    def instance(cls) -> BondManager:
        """Return the singleton manager."""
        return cls()

    def get(
        self,
        id: str,
        db_path: str | Path | None = None,
    ) -> Bond | None:
        """Return a :class:`Bond` for *id*, loading from the database if needed."""
        key = id.strip()
        if not key:
            return None

        cached = self._lookup_cached(key)
        if cached is not None:
            return cached

        resolved_db_path = (
            db_path if db_path is not None else get_settings().bond_analytics_db_path
        )
        row = _fetch_bond_row(resolved_db_path, key)
        if row is None:
            return None

        bond = Bond.from_row(row)
        self._register(bond)
        return bond

    def has_bond(
        self,
        id: str,
        db_path: str | Path | None = None,
    ) -> bool:
        """Return whether a bond exists for *id* (may load from the database)."""
        return self.get(id, db_path=db_path) is not None

    def clear(self) -> None:
        """Remove all cached bonds (intended for tests)."""
        self._by_user_friendly_id.clear()
        self._by_bond_id.clear()

    def _lookup_cached(self, key: str) -> Bond | None:
        if key in self._by_user_friendly_id:
            return self._by_user_friendly_id[key]
        if key in self._by_bond_id:
            return self._by_bond_id[key]
        return None

    def _register(self, bond: Bond) -> None:
        if bond.user_friendly_id is not None:
            self._by_user_friendly_id[bond.user_friendly_id] = bond
        if bond.bond_id is not None:
            self._by_bond_id[bond.bond_id] = bond
