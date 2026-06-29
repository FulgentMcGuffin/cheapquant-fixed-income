"""Create and populate the bond analytics database from semantics and CSV inputs."""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl
import yaml
from mcp_data.backends import DataSink, DuckDBSource, SQLiteSource

DEFAULT_DB_PATH = Path("D:/data/duckdb/bond_analytics.duckdb")
# DEFAULT_DB_PATH = Path("D:/data/sqlitedb/bond_analytics.db")

DEFAULT_SEMANTICS_PATH = Path(__file__).resolve().parents[3] / "semantics" / "bond_analytics.yaml"
DEFAULT_CSV_DIR = Path("D:/bond_csvs")

TENOR_PILLAR_COLUMNS = (
    "6M",
    "1Y",
    "2Y",
    "3Y",
    "4Y",
    "5Y",
    "7Y",
    "10Y",
    "12Y",
    "15Y",
    "20Y",
    "25Y",
    "30Y",
    "50Y",
    "70Y",
    "100Y",
)
TENOR_PILLAR_FALSE = frozenset({"50Y", "70Y", "100Y"})

CLOSEST_TENOR_YEARS = (1, 2, 3, 4, 5, 10, 15, 20, 25, 30, 40, 50, 70, 100)

MONTH_ABBR = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)

FILE_ISSUER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("uk", "GBR"),
    ("german", "DEU"),
    ("french", "FRA"),
    ("spanish", "ESP"),
    ("italian", "ITA"),
)

_SQLITE_TYPES = {
    "TEXT": "TEXT",
    "BOOLEAN": "INTEGER",
    "REAL": "REAL",
}
_DUCKDB_TYPES = {
    "TEXT": "VARCHAR",
    "BOOLEAN": "BOOLEAN",
    "REAL": "DOUBLE",
}


def load_semantics(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def unique_issuer_acronyms(semantics: dict) -> list[str]:
    issuers = semantics.get("vocabulary", {}).get("issuers", {})
    return sorted(set(issuers.values()))


def table_specs(semantics: dict) -> dict[str, dict]:
    specs = dict(semantics.get("tables", {}))
    cmt = semantics.get("cmt_analytics")
    if isinstance(cmt, dict) and "columns" in cmt:
        specs["cmt_analytics"] = cmt
    return specs


def sql_column_type(yaml_type: str, backend: str) -> str:
    mapping = _DUCKDB_TYPES if backend == "duckdb" else _SQLITE_TYPES
    return mapping.get(yaml_type.upper(), mapping["TEXT"])


def open_sink(db_path: Path) -> DataSink:
    """Return a writable mcp_data backend for the given database path."""
    if db_path.suffix == ".duckdb":
        return DuckDBSource(db_path, read_only=False)
    if db_path.suffix == ".db":
        return SQLiteSource(db_path, read_only=False)
    raise ValueError(
        f"Unsupported bond analytics database extension {db_path.suffix!r}; "
        "expected .duckdb or .db"
    )


def create_schema(db: DataSink, semantics: dict) -> None:
    backend = db.name
    for table_name, spec in table_specs(semantics).items():
        col_defs = ", ".join(
            f'"{name}" {sql_column_type(meta.get("type", "TEXT"), backend)}'
            for name, meta in spec["columns"].items()
        )
        if backend == "duckdb":
            db.execute(f'CREATE OR REPLACE TABLE "{table_name}" ({col_defs})')
        else:
            db.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            db.execute(f'CREATE TABLE "{table_name}" ({col_defs})')


def tenor_pillars_dataframe(issuer_codes: list[str]) -> pl.DataFrame:
    records: list[dict] = []
    for issuer in issuer_codes:
        row: dict = {
            "issuer": issuer,
            "from_date": "1990-01-01",
            "to_date": "2200-12-31",
        }
        for col in TENOR_PILLAR_COLUMNS:
            row[col] = col not in TENOR_PILLAR_FALSE
        records.append(row)
    return pl.DataFrame(records)


def append_polars(db: DataSink, table_name: str, df: pl.DataFrame) -> int:
    """Bulk-append a Polars frame via the mcp_data backend."""
    if df.height == 0:
        return 0
    if isinstance(db, DuckDBSource):
        return db.append_to_table(table_name, df)

    # SQLiteSource.insert does not quote identifiers such as "6M"; use execute.
    columns_sql = ", ".join(f'"{column}"' for column in df.columns)
    placeholders = ", ".join("?" for _ in df.columns)
    query = f'INSERT INTO "{table_name}" ({columns_sql}) VALUES ({placeholders})'
    for row in df.iter_rows():
        db.execute(query, row)
    return df.height


def populate_tenor_pillars(db: DataSink, issuer_codes: list[str]) -> None:
    append_polars(db, "tenor_pillars", tenor_pillars_dataframe(issuer_codes))


def issuer_from_filename(path: Path) -> str:
    stem = path.stem.lower()
    for prefix, code in FILE_ISSUER_PREFIXES:
        if prefix in stem:
            return code
    raise ValueError(f"Cannot infer issuer acronym from CSV filename: {path.name}")


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _year_suffix(year: int) -> str:
    return f"{year % 1000:03d}"


def _base_friendly_id(issuer: str, maturity: date) -> str:
    return (
        f"{issuer.lower()}"
        f"{MONTH_ABBR[maturity.month - 1]}"
        f"{_year_suffix(maturity.year)}"
    )


def closest_tenor_pillar(original_term_years: float | None) -> str | None:
    if original_term_years is None:
        return None
    best = min(CLOSEST_TENOR_YEARS, key=lambda pillar: (abs(original_term_years - pillar), pillar))
    return f"{best}Y"


def _parse_green(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _tie_key(record: dict) -> tuple:
    return (
        record["maturity"],
        record["issue_date"] or "",
        record["coupon"] if record["coupon"] is not None else float("inf"),
    )


def assign_user_friendly_ids(records: list[dict]) -> None:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        maturity = _parse_date(record["maturity"])
        if maturity is None:
            record["user_friendly_id"] = None
            continue
        base = _base_friendly_id(record["issuer"], maturity)
        groups[(record["issuer"], base)].append(record)

    for group in groups.values():
        group.sort(
            key=lambda record: (
                record["maturity"] or "",
                record["issue_date"] or "",
                record["coupon"] if record["coupon"] is not None else float("inf"),
                record["bond_id"],
            )
        )

        index = 0
        while index < len(group):
            end = index + 1
            while end < len(group) and _tie_key(group[index]) == _tie_key(group[end]):
                end += 1
            chunk = group[index:end]
            if len(chunk) > 1:
                rng = random.Random(abs(hash(tuple(item["bond_id"] for item in chunk))))
                rng.shuffle(chunk)
                group[index:end] = chunk
            index = end

        multi = len(group) > 1
        for rank, record in enumerate(group, start=1):
            maturity = _parse_date(record["maturity"])
            assert maturity is not None
            base = _base_friendly_id(record["issuer"], maturity)
            suffix = f"_{rank}" if multi else ""
            green = "g" if record.pop("is_green", False) else ""
            record["user_friendly_id"] = f"{base}{suffix}{green}"


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NULL", "NONE"}:
        return None
    return float(text)


def load_bond_records(csv_dir: Path) -> list[dict]:
    records: list[dict] = []
    for csv_path in sorted(csv_dir.glob("*.csv")):
        issuer = issuer_from_filename(csv_path)
        frame = pl.read_csv(csv_path)

        green_col = "green_bond" if "green_bond" in frame.columns else "green"
        for row in frame.iter_rows(named=True):
            coupon_raw = row.get("coupon")
            term_raw = row.get("original_term_years")
            records.append(
                {
                    "bond_id": row.get("isin"),
                    "user_friendly_id": None,
                    "issuer": issuer,
                    "coupon": _parse_float(coupon_raw),
                    "maturity": row.get("maturity_date"),
                    "issue_date": row.get("issue_date"),
                    "first_coupon_date": None,
                    "accrual_start_date": None,
                    "closest_tenor_pillar": closest_tenor_pillar(_parse_float(term_raw)),
                    "issue_amount": None,
                    "is_green": _parse_green(row.get(green_col)),
                }
            )
    return records


def bond_universe_dataframe(records: list[dict]) -> pl.DataFrame:
    assign_user_friendly_ids(records)
    frame = pl.DataFrame(
        [
            {
                "bond_id": record["bond_id"],
                "user_friendly_id": record["user_friendly_id"],
                "issuer": record["issuer"],
                "coupon": record["coupon"],
                "maturity": record["maturity"],
                "issue_date": record["issue_date"],
                "first_coupon_date": record["first_coupon_date"],
                "accrual_start_date": record["accrual_start_date"],
                "closest_tenor_pillar": record["closest_tenor_pillar"],
                "issue_amount": record["issue_amount"],
            }
            for record in records
        ]
    )
    return frame.with_columns(
        pl.col("coupon").cast(pl.Float64, strict=False),
        pl.col("issue_amount").cast(pl.Float64, strict=False),
    )


def populate_bond_universe(db: DataSink, csv_dir: Path) -> int:
    frame = bond_universe_dataframe(load_bond_records(csv_dir))
    append_polars(db, "bond_universe", frame)
    return frame.height


def build_bond_analytics_db(
    db_path: Path = DEFAULT_DB_PATH,
    semantics_path: Path = DEFAULT_SEMANTICS_PATH,
    csv_dir: Path = DEFAULT_CSV_DIR,
) -> None:
    semantics = load_semantics(semantics_path)
    issuer_codes = unique_issuer_acronyms(semantics)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with open_sink(db_path) as db:
        create_schema(db, semantics)
        populate_tenor_pillars(db, issuer_codes)
        bond_count = populate_bond_universe(db, csv_dir)

    print(f"Created {db_path} ({db_path.suffix})")
    print(f"  tenor_pillars rows: {len(issuer_codes)}")
    print(f"  bond_universe rows: {bond_count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create bond_analytics database from semantics and CSV bond universes."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Output database path (.duckdb or .db, default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--semantics",
        type=Path,
        default=DEFAULT_SEMANTICS_PATH,
        help=f"bond_analytics semantics YAML (default: {DEFAULT_SEMANTICS_PATH})",
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=DEFAULT_CSV_DIR,
        help=f"Directory of issuer CSV files (default: {DEFAULT_CSV_DIR})",
    )
    args = parser.parse_args()
    build_bond_analytics_db(
        db_path=args.db_path,
        semantics_path=args.semantics,
        csv_dir=args.csv_dir,
    )


if __name__ == "__main__":
    main()
