"""Persist analytics rows into ``quant_cache_db`` from ``quant_cache`` semantics."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from cqfi.analytics_input import BondAnalyticsInput, CmtAnalyticsInput
from cqfi.analytics_output import FixedIncomeAnalyticsOutput
from cqfi.bond_future_input import BondFutureInput
from cqfi.bond_future_output import BondFutureBasketOutput
from cqfi.bond_futures import BOND_FUTURE_CONVENTIONS, BondFuture, BondFutureConvention
from cqfi.issuers import ISSUERS

# Tables materialised from semantics YAML (session analytics, not framecache blobs).
_ANALYTICS_TABLES = (
    "cmt_analytics",
    "bond_analytics",
    "bond_future_basket_outputs",
    "bond_future_outputs",
)
_CONVENTION_TABLE = "bond_future_conventions"
_METADATA_TABLES = ("issuers", _CONVENTION_TABLE)
_CACHE_REGISTRY_TABLES = _ANALYTICS_TABLES + _METADATA_TABLES
_TABLE_PRIMARY_KEYS: dict[str, str] = {
    "cmt_analytics": "cmt_analytic_id",
    "bond_analytics": "analytic_id",
    "bond_future_basket_outputs": "basket_output_id",
    "bond_future_outputs": "future_output_id",
    _CONVENTION_TABLE: "convention_id",
    "issuers": "issuer_code",
}
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
        cmt = data.get("cmt_analytics")
        if isinstance(cmt, dict) and "columns" in cmt:
            tables = {**tables, "cmt_analytics": cmt}
        return {
            name: (spec.get("columns") or {})
            for name, spec in tables.items()
            if name in _CACHE_REGISTRY_TABLES and isinstance(spec, dict)
        }

    def _ensure_analytics_tables(self) -> None:
        """CREATE TABLE IF NOT EXISTS from semantics YAML."""
        specs = self._load_table_specs()
        conn = self._conn()
        for table in _CACHE_REGISTRY_TABLES:
            columns = specs.get(table)
            if not columns:
                continue
            self._table_columns[table] = set(columns)
            col_sql = ", ".join(
                f'"{col}" {_sql_type(meta.get("type", "TEXT"), duckdb=self._duckdb)}'
                for col, meta in columns.items()
            )
            pk = _TABLE_PRIMARY_KEYS[table]
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" ({col_sql}, PRIMARY KEY ("{pk}"))'
            )
        if not self._duckdb:
            conn.commit()
        # Materialize metadata tables
        self._materialize_issuers_table()
        self._materialize_bond_futures_conventions_table()

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

    def _materialize_issuers_table(self) -> None:
        """Materialize ISSUERS dict into the issuers table."""
        table = "issuers"
        if table not in self._table_columns:
            return  # Table not defined in semantics
        
        import QuantLib as ql
        
        conn = self._conn()
        # Clear existing data
        conn.execute(f'DELETE FROM "{table}"')
        
        for issuer_code, profile in ISSUERS.items():
            # Serialize QuantLib objects to strings
            calendar_name = profile.calendar().__class__.__name__
            if calendar_name == "NullCalendar":
                calendar_name = "NullCalendar"
            
            day_count_name = profile.day_count.name()
            
            # Convert frequency int to string
            freq_map = {
                ql.NoFrequency: "NoFrequency",
                ql.Once: "Once",
                ql.Annual: "Annual",
                ql.Semiannual: "Semiannual",
                ql.Quarterly: "Quarterly",
                ql.Monthly: "Monthly",
                ql.Weekly: "Weekly",
                ql.Daily: "Daily",
                ql.EveryFourthWeek: "EveryFourthWeek",
                ql.EveryFourthMonth: "EveryFourthMonth",
            }
            frequency_str = freq_map.get(profile.frequency, str(profile.frequency))
            
            # Repo day count if available
            repo_day_count_name = None
            if profile.repo_day_count is not None:
                repo_day_count_name = profile.repo_day_count.name()
            
            row = {
                "issuer_code": issuer_code,
                "name": profile.name,
                "currency": profile.currency,
                "calendar_name": calendar_name,
                "day_count_type": day_count_name,
                "settlement_days": profile.settlement_days,
                "coupon_frequency": frequency_str,
                "repo_day_count_type": repo_day_count_name,
                "repo_settlement_days": profile.repo_settlement_days,
            }
            self._insert_row(table, row)

    def _materialize_bond_futures_conventions_table(self) -> None:
        """Materialize BOND_FUTURE_CONVENTIONS into the bond_future_conventions table."""
        table = "bond_future_conventions"
        if table not in self._table_columns:
            return  # Table not defined in semantics
        
        conn = self._conn()
        # Clear existing data
        conn.execute(f'DELETE FROM "{table}"')
        
        for convention in BOND_FUTURE_CONVENTIONS.values():
            maturity_range = None
            original_term_range = None
            if convention.restrictions is not None:
                maturity_range = convention.restrictions.remaining_maturity
                original_term_range = convention.restrictions.original_term
            
            min_maturity = None
            max_maturity = None
            if maturity_range:
                if maturity_range.min_months is not None:
                    min_maturity = maturity_range.min_months / 12.0
                if maturity_range.max_months is not None:
                    max_maturity = maturity_range.max_months / 12.0
            
            original_term_str = None
            if original_term_range:
                parts = []
                if original_term_range.min_months is not None:
                    parts.append(f"{original_term_range.min_months / 12.0:.1f}")
                if original_term_range.max_months is not None:
                    parts.append(f"{original_term_range.max_months / 12.0:.1f}")
                if parts:
                    original_term_str = " to ".join(parts)
            
            row = {
                "convention_id": convention.name,
                "exchange": convention.exchange,
                "underlying_issuer": convention.issuer_code,
                "full_name": convention.name,
                "notional_maturity_years": convention.notional_maturity_years,
                "notional_coupon": convention.notional_coupon,
                "min_maturity_years": min_maturity,
                "max_maturity_years": max_maturity,
                "original_term_years": original_term_str,
                "conversion_factor_method": convention.conversion_factor_method.value,
                "contract_size": convention.contract_size,
            }
            self._insert_row(table, row)

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
                is_fixed_coupon=0,
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

    def persist_cmt_compute(
        self,
        *,
        owner: str,
        method: str,
        request: CmtAnalyticsInput,
        metrics: FixedIncomeAnalyticsOutput,
        curve_label: str,
        anchor_date: date,
    ) -> FixedIncomeAnalyticsOutput:
        """Write a standalone CMT analytics row from ``compute_cmt_analytics``."""
        _ = owner, method  # reserved for future calculation_log linkage
        composite = request.composite_tenor
        issuer = composite.issuer_profile.source_code
        tenor_label = str(composite)

        forward_start = composite.forward_start_date(anchor_date)
        forward_end = composite.forward_end_date(anchor_date)
        if isinstance(forward_start, datetime):
            forward_start = forward_start.date()
        if isinstance(forward_end, datetime):
            forward_end = forward_end.date()

        trade_date = (request.trade_date or anchor_date).isoformat()
        settlement_date = forward_start.isoformat()
        maturity_date = forward_end.isoformat()
        created_at = utc_now_ms()
        cmt_analytic_id = join_id(issuer, tenor_label, short_id())
        coupon = (
            metrics.par_yield
            if metrics.par_yield is not None
            else metrics.yield_to_maturity
        )

        row = {
            "cmt_analytic_id": cmt_analytic_id,
            "issuer": issuer,
            "tenor_label": tenor_label,
            "created_at": created_at,
            "trade_date": trade_date,
            "settlement_date": settlement_date,
            "coupon": coupon,
            "is_fixed_coupon": 0,
            "maturity_date": maturity_date,
            "curve_used": 1,
            "curve_settings": curve_label,
            **metrics.as_dict(only_populated=True),
        }
        self._insert_row("cmt_analytics", row)
        return metrics

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

    def persist_bond_future_compute(
        self,
        *,
        owner: str,
        method: str,
        request: BondFutureInput,
        result: BondFutureBasketOutput,
    ) -> BondFutureBasketOutput:
        """Write basket and per-bond rows from ``compute_bond_future_analytics``."""
        _ = owner, method  # reserved for future calculation_log linkage
        created_at = utc_now_ms()
        bond_future = request.bond_future
        convention = bond_future.convention
        trade_date = request.trade_date.isoformat()
        delivery = request.delivery_date()
        basket_output_id = join_id(
            str(bond_future),
            trade_date,
            short_id(),
        )

        self._insert_row(
            "bond_future_basket_outputs",
            {
                "basket_output_id": basket_output_id,
                "convention_id": convention.name,
                "delivery_month": _delivery_month_label(bond_future),
                "trade_date": trade_date,
                "settlement_date": result.settlement_date.isoformat(),
                "delivery_date": result.delivery_date.isoformat(),
                "futures_price": result.futures_price,
                "futures_price_is_implied": int(result.futures_price_is_implied),
                "repo_rate": result.repo_rate,
                "bond_count": float(len(result.outputs)),
                "created_at": created_at,
            },
        )

        for output in result.outputs:
            repo_rate, repo_json = _bond_repo_fields(
                request, output.bond, delivery, basket_repo_rate=result.repo_rate
            )
            self._insert_row(
                "bond_future_outputs",
                {
                    "future_output_id": join_id(basket_output_id, output.bond.bond_id, short_id()),
                    "basket_output_id": basket_output_id,
                    "bond_id": output.bond.bond_id,
                    "index": float(output.index),
                    "conversion_factor": output.conversion_factor,
                    "clean_price": output.clean_price,
                    "accrued_interest": output.accrued_interest,
                    "repo_rate": repo_rate,
                    "repo_term_structure_json": repo_json,
                    "forward_clean_price": output.forward_clean_price,
                    "implied_repo_rate": output.implied_repo_rate,
                    "gross_basis": output.gross_basis,
                    "net_basis": output.net_basis,
                    "delta": output.delta,
                    "gamma": output.gamma,
                    "implied_fair_futures_price": output.implied_fair_futures_price,
                    "created_at": created_at,
                },
            )
        return result

    def upsert_bond_future_convention(self, convention_id: str) -> None:
        """Insert or replace a convention row from :data:`BOND_FUTURE_CONVENTIONS`."""
        if _CONVENTION_TABLE not in self._table_columns:
            return
        convention = BOND_FUTURE_CONVENTIONS.get(convention_id)
        if convention is None:
            raise ValueError(f"Unknown bond future convention: {convention_id!r}")
        self.upsert_row(_CONVENTION_TABLE, _convention_row(convention))

    @staticmethod
    def _tenor_label_days(start: date, maturity: date) -> str:
        """Days from trade/settlement to maturity as a Tenor-parseable label (e.g. ``1234d``)."""
        days = (maturity - start).days
        return f"{max(days, 0)}d"

    def fetch_all_rows(self, table: str) -> list[dict[str, Any]]:
        """Return all rows from an analytics table as dicts."""
        if table not in _ANALYTICS_TABLES:
            raise ValueError(f"Unsupported analytics table: {table}")
        conn = self._conn()
        result = conn.execute(f'SELECT * FROM "{table}"')
        if self._duckdb:
            return result.fetchdf().to_dict("records")
        cols = [desc[0] for desc in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    def upsert_row(self, table: str, row: dict[str, Any]) -> None:
        """Insert or replace a row keyed by the table primary key."""
        allowed = self._table_columns.get(table) or set(row)
        payload = {k: v for k, v in row.items() if k in allowed}
        if not payload:
            return
        cols = list(payload)
        col_list = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        values = [payload[c] for c in cols]
        sql = f'INSERT OR REPLACE INTO "{table}" ({col_list}) VALUES ({placeholders})'
        conn = self._conn()
        conn.execute(sql, values)
        if not self._duckdb:
            conn.commit()

    def reset_analytics_tables(self) -> None:
        conn = self._conn()
        for table in reversed(_ANALYTICS_TABLES):
            conn.execute(f'DELETE FROM "{table}"')
        if not self._duckdb:
            conn.commit()


def _delivery_month_label(bond_future: BondFuture) -> str:
    """Return delivery month code plus year, e.g. ``U2026``."""
    return f"{bond_future.month_code()}{bond_future.delivery_year}"


def _convention_row(convention: BondFutureConvention) -> dict[str, Any]:
    """Build a ``bond_future_conventions`` row from a registry entry."""
    return {
        "convention_id": convention.name,
        "exchange": convention.exchange,
        "issuer": convention.issuer_code,
        "notional_maturity_years": convention.notional_maturity_years,
        "notional_coupon": convention.notional_coupon,
        "contract_size": convention.contract_size,
        "reference_day_spec": str(convention.reference_day),
        "delivery_start_spec": str(convention.delivery_start),
        "delivery_end_spec": str(convention.delivery_end),
        "conversion_factor_method": convention.conversion_factor_method.value,
        "cf_decimals": float(convention.cf_decimals),
        "repo_market": convention.repo_market.value,
        "bloomberg_root": convention.bloomberg_root,
        "synonyms": json.dumps(list(convention.synonyms)) if convention.synonyms else None,
        "restrictions_json": json.dumps(convention.restrictions.as_dict()),
        "created_at": utc_now_ms(),
    }


def _bond_repo_fields(
    request: BondFutureInput,
    bond,
    delivery: date,
    *,
    basket_repo_rate: float,
) -> tuple[float, str | None]:
    """Return ``(repo_rate_pct, repo_term_structure_json)`` for one deliverable."""
    override = request.delivery_basket.repo_term_structure_for(bond)
    if override is None:
        return basket_repo_rate, None

    convention = request.bond_future.convention
    rate_pct = (
        override.rate_for(
            delivery,
            settlement_days=convention.repo_settlement_days(),
            issuer=convention.issuer(),
        )
        * 100.0
    )
    repo_json = json.dumps(
        {label: value * 100.0 for label, value in override.to_dict().items()}
    )
    return rate_pct, repo_json


_registry: CacheRegistry | None = None


def get_cache_registry(
    db_path: Path | str | None = None,
    semantics_path: Path | str | None = None,
) -> CacheRegistry:
    """Process-wide registry bound to ``AppSettings`` quant-cache paths."""
    global _registry
    if _registry is None or db_path is not None:
        from cqfi.config import get_settings

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
