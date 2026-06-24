"""Queryable analytics tables alongside the framecache blob store."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import polars as pl


class CacheRegistry:
    """Maintains flattened result tables for LLM SQL queries."""

    _SCHEMA = [
        """
        CREATE TABLE IF NOT EXISTS cmt_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_id TEXT NOT NULL,
            issuer TEXT NOT NULL,
            valuation_date TEXT NOT NULL,
            rate_type TEXT NOT NULL,
            tenor_label TEXT NOT NULL,
            tenor_years REAL NOT NULL,
            input_rate_pct REAL,
            curve_zero_pct REAL,
            clean_price REAL NOT NULL,
            yield_pct REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS calculation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_id TEXT NOT NULL UNIQUE,
            function_name TEXT NOT NULL,
            issuer TEXT,
            valuation_date TEXT,
            rate_type TEXT,
            row_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cmt_issuer_date ON cmt_prices (issuer, valuation_date)",
        "CREATE INDEX IF NOT EXISTS idx_calc_fn ON calculation_log (function_name)",
    ]

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._connection: sqlite3.Connection | None = None
        self.connect()

    def connect(self) -> None:
        if self._connection is not None:
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        for stmt in self._SCHEMA:
            self._connection.execute(stmt)
        self._connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("CacheRegistry is not connected")
        return self._connection

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

    def register_cmt_prices(
        self,
        cache_id: str,
        frame: pl.DataFrame,
    ) -> None:
        """Persist CMT pricing output rows for SQL querying."""
        if frame.is_empty():
            return

        created_at = self._now_iso()
        issuer = frame["issuer"][0]
        valuation_date = frame["valuation_date"][0]
        rate_type = frame["rate_type"][0]

        rows = [
            (
                cache_id,
                row["issuer"],
                row["valuation_date"],
                row["rate_type"],
                row["tenor_label"],
                row["tenor_years"],
                row["input_rate_pct"],
                row["curve_zero_pct"],
                row["clean_price"],
                row["yield_pct"],
                created_at,
            )
            for row in frame.iter_rows(named=True)
        ]

        conn = self._conn()
        conn.executemany(
            """
            INSERT INTO cmt_prices (
                cache_id, issuer, valuation_date, rate_type,
                tenor_label, tenor_years, input_rate_pct, curve_zero_pct,
                clean_price, yield_pct, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.execute(
            """
            INSERT INTO calculation_log (
                cache_id, function_name, issuer, valuation_date,
                rate_type, row_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_id) DO UPDATE SET
                row_count = excluded.row_count,
                created_at = excluded.created_at
            """,
            (
                cache_id,
                "price_cmts",
                issuer,
                valuation_date,
                rate_type,
                len(rows),
                created_at,
            ),
        )
        conn.commit()

    def reset_analytics_tables(self) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM cmt_prices")
        conn.execute("DELETE FROM calculation_log")
        conn.commit()
