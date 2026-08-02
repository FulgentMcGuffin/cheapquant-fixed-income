"""Singleton registry for :class:`Bond` instances loaded from bond_universe."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from cheapquant_fi.config import get_settings
from cheapquant_fi.db_backend import source_class_for
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
    with source_class_for(db_path)(db_path, read_only=True) as db:
        for sql in queries:
            frame = db.run_query(sql)
            if not frame.is_empty():
                return frame.row(0, named=True)
    return None


def _fetch_bond_rows_by_issuer(
    db_path: Path | str,
    issuer: str,
    maturity_from: date | None = None,
    maturity_to: date | None = None,
) -> Sequence[Mapping[str, Any]]:
    """Load ``bond_universe`` rows for one issuer, optionally by maturity window.

    The predicate order matches ``idx_bu_issuer_maturity_issue``.
    """
    clauses = [f"issuer = '{_escape_sql_literal(issuer)}'"]
    if maturity_from is not None:
        clauses.append(f"maturity >= '{maturity_from.isoformat()}'")
    if maturity_to is not None:
        clauses.append(f"maturity <= '{maturity_to.isoformat()}'")
    sql = (
        "SELECT * FROM bond_universe "
        f"WHERE {' AND '.join(clauses)} ORDER BY maturity"
    )
    with source_class_for(db_path)(db_path, read_only=True) as db:
        frame = db.run_query(sql)
    return [] if frame.is_empty() else frame.to_dicts()


def _fetch_latest_analytics_trade_date(db_path: Path | str, issuer: str) -> date | None:
    """Return the most recent ``bond_analytics.trade_date`` for an issuer.

    ``bond_analytics`` carries ``bond_id`` but no issuer column, so this joins
    through ``bond_universe``.
    """
    sql = (
        "SELECT MAX(a.trade_date) AS latest_trade_date FROM bond_analytics a "
        "JOIN bond_universe b ON b.bond_id = a.bond_id "
        f"WHERE b.issuer = '{_escape_sql_literal(issuer)}'"
    )
    with source_class_for(db_path)(db_path, read_only=True) as db:
        frame = db.run_query(sql)
    if frame.is_empty():
        return None
    value = frame.row(0, named=True).get("latest_trade_date")
    if value is None:
        return None
    return date.fromisoformat(str(value)[:10])


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

        row = _fetch_bond_row(self._resolve_db_path(db_path), key)
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

    def get_by_issuer(
        self,
        issuer: str,
        *,
        maturity_from: date | None = None,
        maturity_to: date | None = None,
        db_path: str | Path | None = None,
    ) -> list[Bond]:
        """Return every bond for *issuer*, optionally within a maturity window.

        Unlike :meth:`get`, this always queries the database — the singleton
        holds no record of which issuers have been fully loaded — but every
        row it reads is registered so later single-bond lookups hit the cache.

        Args:
            issuer: Issuer code as stored in ``bond_universe``.
            maturity_from: Earliest maturity to include, inclusive.
            maturity_to: Latest maturity to include, inclusive.
            db_path: Override for the bond analytics database.

        Returns:
            Bonds ordered by maturity.
        """
        rows = _fetch_bond_rows_by_issuer(
            self._resolve_db_path(db_path),
            issuer.strip().upper(),
            maturity_from,
            maturity_to,
        )
        bonds = [Bond.from_row(row) for row in rows]
        for bond in bonds:
            self._register(bond)
        return bonds

    def latest_analytics_trade_date(
        self,
        issuer: str,
        db_path: str | Path | None = None,
    ) -> date | None:
        """Return the latest trade date held in ``bond_analytics`` for *issuer*."""
        return _fetch_latest_analytics_trade_date(
            self._resolve_db_path(db_path), issuer.strip().upper()
        )

    @staticmethod
    def _resolve_db_path(db_path: str | Path | None) -> str | Path:
        return db_path if db_path is not None else get_settings().bond_analytics_db_path

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
