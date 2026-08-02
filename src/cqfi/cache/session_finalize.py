"""Session-end quant_cache_db cleanup and optional merge into bond_analytics_db."""

from __future__ import annotations

from cqfi.cache.registry import (
    CacheRegistry,
    reset_cache_registry,
)
from cqfi.config import AppSettings, get_runtime_settings, get_settings

# cmt rows first — bond_analytics may reference cmt_analytic_id values.
# bond_future_basket_outputs before bond_future_outputs (parent before child).
_MERGE_ORDER = (
    "cmt_analytics",
    "bond_analytics",
    "bond_future_basket_outputs",
    "bond_future_outputs",
)


def finalize_quant_cache_session(settings: AppSettings | None = None) -> None:
    """Apply session-end quant cache policy from runtime settings."""
    settings = settings or get_settings()
    runtime = get_runtime_settings()
    cache_path = settings.quant_cache_db_path
    if not cache_path.exists():
        return

    save_to_bond = runtime.save_quant_cache_to_bond_analytics_after_session
    src = CacheRegistry(cache_path, settings.quant_cache_semantics_path)
    try:
        if save_to_bond:
            dst = CacheRegistry(
                settings.bond_analytics_db_path,
                settings.quant_cache_semantics_path,
            )
            conv = CacheRegistry(
                settings.bond_analytics_db_path,
                settings.bond_analytics_semantics_path,
            )
            try:
                _merge_analytics(src, dst, conv)
            finally:
                dst.close()
                conv.close()
        src.reset_analytics_tables()
    finally:
        src.close()
        reset_cache_registry()


def _merge_analytics(
    src: CacheRegistry,
    dst: CacheRegistry,
    conv: CacheRegistry,
) -> None:
    conn = dst._conn()
    if not dst._duckdb:
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        convention_ids = {
            row["convention_id"]
            for row in src.fetch_all_rows("bond_future_basket_outputs")
            if row.get("convention_id")
        }
        for convention_id in convention_ids:
            conv.upsert_bond_future_convention(convention_id)

        for table in _MERGE_ORDER:
            for row in src.fetch_all_rows(table):
                dst.upsert_row(table, row)
    finally:
        if not dst._duckdb:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
