"""Read-only access to zero/par rates in ycs_data.duckdb/sqlite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from mcp_data.backends.sqlite_backend import SQLiteSource

from cheapquant_fi.issuers import IssuerProfile, RateType
from cheapquant_fi.ycs_tenors import TENOR_COLUMNS


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_curve_rates(
    db_path: Path | str,
    issuer: IssuerProfile,
    valuation_date: str | date,
    rate_type: RateType = RateType.ZERO,
) -> pl.DataFrame:
    """Load one row of pillar rates for (issuer, date) as a long-form DataFrame.

    Returns columns: tenor_column, tenor_label, tenor_years, rate_pct.
    """
    table = "zero_rates" if rate_type == RateType.ZERO else "par_rates"
    val_date = _parse_date(valuation_date)
    date_str = val_date.isoformat()

    with SQLiteSource(db_path, read_only=True) as db:
        frame = db.run_query(
            f"""
            SELECT *
            FROM {table}
            WHERE source = '{issuer.source_code}'
              AND date = '{date_str}'
            """
        )

    if frame.is_empty():
        raise LookupError(
            f"No {rate_type.value} rates for {issuer.source_code} on {date_str}"
        )

    row = frame.row(0, named=True)
    records: list[dict] = []
    for col in TENOR_COLUMNS:
        rate = row.get(col)
        if rate is None:
            continue
        from cheapquant_fi.ycs_tenors import TENOR_COLUMN_TO_YEARS, column_to_label

        records.append(
            {
                "tenor_column": col,
                "tenor_label": column_to_label(col),
                "tenor_years": TENOR_COLUMN_TO_YEARS[col],
                "rate_pct": float(rate),
            }
        )

    if not records:
        raise LookupError(
            f"All tenor columns are null for {issuer.source_code} on {date_str}"
        )

    return pl.DataFrame(records).sort("tenor_years")


def list_available_dates(
    db_path: Path | str,
    issuer: IssuerProfile,
    rate_type: RateType = RateType.ZERO,
) -> pl.DataFrame:
    """Return distinct valuation dates available for an issuer."""
    table = "zero_rates" if rate_type == RateType.ZERO else "par_rates"
    with SQLiteSource(db_path, read_only=True) as db:
        return db.run_query(
            f"""
            SELECT date
            FROM {table}
            WHERE source = '{issuer.source_code}'
            ORDER BY date
            """
        )
