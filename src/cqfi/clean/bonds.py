"""Deduplicate rows in ``bond_analytics``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cqfi.cache.registry import CacheRegistry


@dataclass(frozen=True)
class CleanBondsResult:
    """Summary of a ``bond_analytics`` dedupe run."""

    rows_before: int
    rows_after: int
    duplicates_removed: int
    duplicate_groups: int

    @property
    def changed(self) -> bool:
        return self.duplicates_removed > 0


def clean_bond_analytics_duplicates(
    db_path: Path | str,
    semantics_path: Path | str,
) -> CleanBondsResult:
    """Keep the latest ``bond_analytics`` row per ``(bond_id, trade_date)``.

    "Latest" is the row with the greatest ``created_at`` (ties broken by
    ``analytic_id``). Older duplicates are deleted. Orphaned ``cmt_analytics``
    rows left unreferenced after the delete are not removed.
    """
    registry = CacheRegistry(db_path, semantics_path)
    try:
        conn = registry._conn()
        rows_before = _count_rows(conn, "bond_analytics")
        duplicate_groups = _count_duplicate_groups(conn)
        if duplicate_groups == 0:
            return CleanBondsResult(
                rows_before=rows_before,
                rows_after=rows_before,
                duplicates_removed=0,
                duplicate_groups=0,
            )

        conn.execute(
            """
            DELETE FROM bond_analytics
            WHERE analytic_id IN (
                SELECT analytic_id
                FROM (
                    SELECT
                        analytic_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY bond_id, trade_date
                            ORDER BY created_at DESC, analytic_id DESC
                        ) AS rn
                    FROM bond_analytics
                ) ranked
                WHERE rn > 1
            )
            """
        )
        if not registry._duckdb:
            conn.commit()

        rows_after = _count_rows(conn, "bond_analytics")
        return CleanBondsResult(
            rows_before=rows_before,
            rows_after=rows_after,
            duplicates_removed=rows_before - rows_after,
            duplicate_groups=duplicate_groups,
        )
    finally:
        registry.close()


def _count_rows(conn, table: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    return int(row[0])


def _count_duplicate_groups(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT bond_id, trade_date
            FROM bond_analytics
            GROUP BY bond_id, trade_date
            HAVING COUNT(*) > 1
        ) dupes
        """
    ).fetchone()
    return int(row[0])
