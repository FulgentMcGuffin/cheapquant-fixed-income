"""Persist analytics rows into ``quant_cache_db`` from ``quant_cache`` semantics."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from cheapquant_fi.analytics_input import BondAnalyticsInput
from cheapquant_fi.analytics_output import FixedIncomeAnalyticsOutput

# Tables we materialise from quant_cache.yaml (session analytics, not framecache blobs).
_ANALYTICS_TABLES = ("cmt_analytics", "bond_analytics")
_ID_HEX_LEN = 12  # short UUID fragment
_SEP = "-"


def short_id() -> str:
    """Compact hex id (12 chars)."""
    return uuid.uuid4().hex[:_ID_HEX_LEN]


def join_id(*parts: str) -> str:
    """Join non-empty parts with ``-``."""
    return _SEP.join(p for p in parts if p)


def utc_now_ms() -> str:
    """UTC timestamp with millisecond precision: ``%Y-%m-%d %H:%M:%S.%f`` truncated."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _sql_type(sem_type: str, *, duckdb: bool) -> str:
    key = (sem_type or "TEXT").upper()
    if duckdb:
        return {"TEXT": "VARCHAR", "BOOLEAN": "BOOLEAN", "REAL": "DOUBLE"}.get(key, key)
    return {"TEXT": "TEXT", "BOOLEAN": "INTEGER", "REAL": "REAL"}.get(key, key)


def _is_duckdb(path: Path) -> bool:
    return path.suffix.lower() == ".duckdb"


class CacheRegistry:
    """Creates/writes ``bond_analytics`` / ``cmt_analytics`` in the quant cache DB."""

    def __init__(
        self,
        db_path: Path | str,
        semantics_path: Path | str | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._semantics_path = Path(semantics_path) if semantics_path else None
        self._duckdb = _is_duckdb(self._db_path)
        self._connection: Any = None
        self._table_columns: dict[str, set[str]] = {}
        self.connect()

    def connect(self) -> None:
        if self._connection is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        if self._duckdb:
            import duckdb

            self._connection = duckdb.connect(str(self._db_path))
        else:
            self._connection = sqlite3.connect(
                str(self._db_path), check_same_thread=False
            )
            self._connection.execute("PRAGMA journal_mode=WAL")
        self._ensure_analytics_tables()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _conn(self) -> Any:
        if self._connection is None:
            raise RuntimeError("CacheRegistry is not connected")
        return self._connection

    def _load_table_specs(self) -> dict[str, dict[str, dict]]:
        if self._semantics_path is None or not self._semantics_path.exists():
            return {}
        with self._semantics_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        tables = data.get("tables") or {}
        return {
            name: (spec.get("columns") or {})
            for name, spec in tables.items()
            if name in _ANALYTICS_TABLES and isinstance(spec, dict)
        }

    def _ensure_analytics_tables(self) -> None:
        """CREATE TABLE IF NOT EXISTS from quant_cache semantics (bond/cmt analytics)."""
        specs = self._load_table_specs()
        conn = self._conn()
        # cmt first so optional FKs from bond_analytics can resolve
        for table in _ANALYTICS_TABLES:
            columns = specs.get(table)
            if not columns:
                continue
            self._table_columns[table] = set(columns)
            col_sql = ", ".join(
                f'"{col}" {_sql_type(meta.get("type", "TEXT"), duckdb=self._duckdb)}'
                for col, meta in columns.items()
            )
            pk = "cmt_analytic_id" if table == "cmt_analytics" else "analytic_id"
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" ({col_sql}, PRIMARY KEY ("{pk}"))'
            )
        if not self._duckdb:
            conn.commit()

    def _insert_row(self, table: str, row: dict[str, Any]) -> None:
        """Insert only non-None values that exist as table columns."""
        allowed = self._table_columns.get(table) or set(row)
        payload = {k: v for k, v in row.items() if v is not None and k in allowed}
        if not payload:
            return
        cols = list(payload)
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(f'"{c}"' for c in cols)
        sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'
        conn = self._conn()
        values = [payload[c] for c in cols]
        if self._duckdb:
            conn.execute(sql, values)
        else:
            conn.execute(sql, values)
            conn.commit()

    def persist_bond_compute(
        self,
        *,
        owner: str,
        method: str,
        request: BondAnalyticsInput,
        curve_label: str,
        bond_metrics: FixedIncomeAnalyticsOutput,
        mm_cmt_metrics: FixedIncomeAnalyticsOutput | None,
        mm_fc_cmt_metrics: FixedIncomeAnalyticsOutput | None,
    ) -> FixedIncomeAnalyticsOutput:
        """Write bond + linked CMT rows; stamp link ids onto *bond_metrics* and return it."""
        created_at = utc_now_ms()
        method_key = f"{owner}:{method}"
        analytic_id = join_id(method_key, short_id())

        bond_id = request.bond_id
        if not bond_id:
            bond_id = join_id("CUSTOM", request.issuer, analytic_id)

        mm_cmt_id = join_id(bond_id, short_id()) if mm_cmt_metrics is not None else None
        mm_fc_id = (
            join_id(bond_id, short_id()) if mm_fc_cmt_metrics is not None else None
        )

        curve_used = 1 if request.input_column is None else 0
        curve_settings = join_id(request.issuer, curve_label)
        trade_date = (request.trade_date or request.settlement_date).isoformat()
        settlement_date = request.settlement_date.isoformat()
        maturity_date = request.maturity_date.isoformat()
        tenor_label = self._tenor_label_days(
            request.trade_date or request.settlement_date,
            request.maturity_date,
        )

        # CMT rows first (ids referenced by bond_analytics).
        if mm_cmt_metrics is not None and mm_cmt_id is not None:
            self._insert_cmt_row(
                cmt_analytic_id=mm_cmt_id,
                request=request,
                metrics=mm_cmt_metrics,
                coupon=bond_metrics.par_yield,
                is_fixed_coupon=1,
                tenor_label=tenor_label,
                created_at=created_at,
                trade_date=trade_date,
                settlement_date=settlement_date,
                maturity_date=maturity_date,
                curve_used=curve_used,
                curve_settings=curve_settings,
            )
        if mm_fc_cmt_metrics is not None and mm_fc_id is not None:
            self._insert_cmt_row(
                cmt_analytic_id=mm_fc_id,
                request=request,
                metrics=mm_fc_cmt_metrics,
                coupon=request.coupon,
                is_fixed_coupon=1,
                tenor_label=tenor_label,
                created_at=created_at,
                trade_date=trade_date,
                settlement_date=settlement_date,
                maturity_date=maturity_date,
                curve_used=curve_used,
                curve_settings=curve_settings,
            )

        bond_metrics.mm_cmt_analytic_id = mm_cmt_id
        bond_metrics.mm_fc_cmt_analytic_id = mm_fc_id

        bond_row = {
            "analytic_id": analytic_id,
            "bond_id": bond_id,
            "created_at": created_at,
            "trade_date": trade_date,
            "settlement_date": settlement_date,
            "curve_used": curve_used,
            "curve_settings": curve_settings,
            "input_column": request.input_column,
            "mm_cmt_analytic_id": mm_cmt_id,
            "mm_fc_cmt_analytic_id": mm_fc_id,
            **bond_metrics.as_dict(only_populated=True),
        }
        # Prefer stamped link ids over any pre-existing None-stripped omissions.
        bond_row["mm_cmt_analytic_id"] = mm_cmt_id
        bond_row["mm_fc_cmt_analytic_id"] = mm_fc_id
        self._insert_row("bond_analytics", bond_row)
        return bond_metrics

    def _insert_cmt_row(
        self,
        *,
        cmt_analytic_id: str,
        request: BondAnalyticsInput,
        metrics: FixedIncomeAnalyticsOutput,
        coupon: float | None,
        is_fixed_coupon: int,
        tenor_label: str,
        created_at: str,
        trade_date: str,
        settlement_date: str,
        maturity_date: str,
        curve_used: int,
        curve_settings: str,
    ) -> None:
        row = {
            "cmt_analytic_id": cmt_analytic_id,
            "issuer": request.issuer,
            "tenor_label": tenor_label,
            "created_at": created_at,
            "trade_date": trade_date,
            "settlement_date": settlement_date,
            "coupon": coupon,
            "is_fixed_coupon": is_fixed_coupon,
            "maturity_date": maturity_date,
            "curve_used": curve_used,
            "curve_settings": curve_settings,
            "input_column": request.input_column,
            **metrics.as_dict(only_populated=True),
        }
        self._insert_row("cmt_analytics", row)

    @staticmethod
    def _tenor_label_days(start: date, maturity: date) -> str:
        """Days from trade/settlement to maturity as a Tenor-parseable label (e.g. ``1234d``)."""
        days = (maturity - start).days
        return f"{max(days, 0)}d"

    def reset_analytics_tables(self) -> None:
        conn = self._conn()
        for table in _ANALYTICS_TABLES:
            conn.execute(f'DELETE FROM "{table}"')
        if not self._duckdb:
            conn.commit()


_registry: CacheRegistry | None = None


def get_cache_registry(
    db_path: Path | str | None = None,
    semantics_path: Path | str | None = None,
) -> CacheRegistry:
    """Process-wide registry bound to ``AppSettings`` quant-cache paths."""
    global _registry
    if _registry is None or db_path is not None:
        from cheapquant_fi.config import get_settings

        settings = get_settings()
        path = Path(db_path) if db_path is not None else settings.quant_cache_db_path
        sem = (
            Path(semantics_path)
            if semantics_path is not None
            else settings.quant_cache_semantics_path
        )
        if _registry is not None:
            _registry.close()
        _registry = CacheRegistry(path, sem)
    return _registry


def reset_cache_registry() -> None:
    """Close and drop the process-wide registry (tests / reset cache)."""
    global _registry
    if _registry is not None:
        _registry.close()
        _registry = None
