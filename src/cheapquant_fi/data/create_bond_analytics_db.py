"""Create and populate the bond analytics database from semantics and CSV inputs."""

from __future__ import annotations

import argparse
import os
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl
import yaml
from mcp_data.backends import DataSink, DuckDBSource, SQLiteSource

from cheapquant_fi.config import DEFAULT_CONFIG_PATH, AppSettings

_config_path = os.environ.get("CQFI_CONFIG", DEFAULT_CONFIG_PATH)
_settings = AppSettings.from_yaml(_config_path)
DEFAULT_DB_PATH = _settings.bond_analytics_db_path
DEFAULT_SEMANTICS_PATH = _settings.bond_analytics_semantics_path
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
    ("united_states", "USA"),
    ("american", "USA"),
    ("us", "USA"),
    ("uk", "GBR"),
    ("british", "GBR"),
    ("british_isles", "GBR"),
    ("british_empire", "GBR"),
    ("british_commonwealth", "GBR"),
    ("british_commonwealth_of_nations", "GBR"),
    ("german", "DEU"),
    ("german_federal_republic", "DEU"),
    ("german_democratic_republic", "DEU"),
    ("french", "FRA"),
    ("french_republic", "FRA"),
    ("french_republic_of_france", "FRA"),
    ("spanish", "ESP"),
    ("spanish_kingdom", "ESP"),
    ("spain", "ESP"),
    ("italian", "ITA"),
    ("italy", "ITA"),
    ("japanese", "JPN"),
    ("japan", "JPN"),
    ("austrian", "AUT"),
    ("austria", "AUT"),
    ("belgian", "BEL"),
    ("belgium", "BEL"),
    ("brazilian", "BRA"),
    ("brazil", "BRA"),
    ("canadian", "CAN"),
    ("canada", "CAN"),
    ("swiss", "CHE"),
    ("chinese", "CHN"),
    ("china", "CHN"),
    ("indian", "IND"),
    ("irish", "IRL"),
    ("netherlands", "NLD"),
    ("portuguese", "PRT"),
    ("russian", "RUS"),
    ("swedish", "SWE"),
    ("south_african", "ZAF"),
    ("saudi_arabian", "SAU"),
    ("singaporean", "SGP"),
    ("south_korean", "KOR"),
    ("korean", "KOR"),
    ("austrian", "AUT"),
    ("north_korean", "PRK"),
    ("croatian", "CRO"),
    ("cypriot", "CYP"),
    ("czech", "CZE"),
    ("danish", "DNK"),
    ("estonian", "EST"),
    ("finnish", "FIN"),
    ("mexican", "MEX"),
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

# Primary keys, foreign keys, and indexes aligned to bond_analytics query access patterns.
TABLE_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "tenor_pillars": ("issuer", "from_date"),
    "bond_universe": ("bond_id",),
    "bond_analytics": ("analytic_id",),
    "cmt_analytics": ("cmt_analytic_id",),
}

# child_table, child_column, parent_table, parent_column, on_delete
FOREIGN_KEYS: tuple[tuple[str, str, str, str, str], ...] = (
    ("bond_analytics", "bond_id", "bond_universe", "bond_id", "RESTRICT"),
    ("bond_analytics", "mm_cmt_analytic_id", "cmt_analytics", "cmt_analytic_id", "SET NULL"),
    (
        "bond_analytics",
        "mm_fc_cmt_analytic_id",
        "cmt_analytics",
        "cmt_analytic_id",
        "SET NULL",
    ),
)

# table, index_name, columns, unique
INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("bond_universe", "idx_bu_user_friendly_id", ("user_friendly_id",), True),
    (
        "bond_universe",
        "idx_bu_issuer_pillar_issue",
        ("issuer", "closest_tenor_pillar", "issue_date"),
        False,
    ),
    ("bond_universe", "idx_bu_issuer_maturity_issue", ("issuer", "maturity", "issue_date"), False),
    (
        "bond_universe",
        "idx_bu_issuer_coupon_maturity_issue",
        ("issuer", "coupon", "maturity", "issue_date"),
        False,
    ),
    ("bond_analytics", "idx_ba_bond_id", ("bond_id",), False),
    ("bond_analytics", "idx_ba_bond_trade", ("bond_id", "trade_date"), False),
    (
        "bond_analytics",
        "idx_ba_bond_trade_curve",
        ("bond_id", "trade_date", "curve_settings"),
        False,
    ),
    (
        "bond_analytics",
        "idx_ba_bond_trade_input",
        ("bond_id", "trade_date", "input_column"),
        False,
    ),
    ("bond_analytics", "idx_ba_created_at", ("created_at",), False),
    (
        "cmt_analytics",
        "idx_ca_issuer_tenor_trade",
        ("issuer", "tenor_label", "trade_date"),
        False,
    ),
    (
        "cmt_analytics",
        "idx_ca_issuer_trade_maturity",
        ("issuer", "trade_date", "maturity_date"),
        False,
    ),
)

EXPECTED_INDEX_NAMES: frozenset[str] = frozenset(index_name for _, index_name, _, _ in INDEXES)

TABLE_CREATE_ORDER: tuple[str, ...] = (
    "tenor_pillars",
    "bond_universe",
    "cmt_analytics",
    "bond_analytics",
)


def load_semantics(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def unique_issuer_acronyms(semantics: dict) -> list[str]:
    issuers = semantics.get("vocabulary", {}).get("issuers", {})
    return sorted(set(issuers.values()))

def issuers_to_currency_dict(semantics: dict) -> dict[str, str]:
    currencies = semantics.get("vocabulary", {}).get("currencies", {})
    return {issuer: currency for issuer, currency in currencies.items()}


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
    if db_path.suffix in [".db", ".sqlite"]:
        return SQLiteSource(db_path, read_only=False)
    raise ValueError(
        f"Unsupported bond analytics database extension {db_path.suffix!r}; "
        "expected .duckdb or .db or .sqlite"
    )


def _quoted_columns(columns: tuple[str, ...]) -> str:
    return ", ".join(f'"{column}"' for column in columns)


def _inline_foreign_key_clauses(table_name: str, backend: str) -> list[str]:
    clauses: list[str] = []
    for child_table, child_col, parent_table, parent_col, on_delete in FOREIGN_KEYS:
        if child_table != table_name:
            continue
        if backend == "duckdb":
            clauses.append(
                f'FOREIGN KEY ("{child_col}") REFERENCES "{parent_table}" ("{parent_col}")'
            )
        else:
            clauses.append(
                f'FOREIGN KEY ("{child_col}") REFERENCES "{parent_table}" ("{parent_col}") '
                f"ON DELETE {on_delete}"
            )
    return clauses


def create_tables(db: DataSink, semantics: dict) -> None:
    """Create tables from semantics YAML with inline primary and foreign keys."""
    backend = db.name
    specs = table_specs(semantics)
    for table_name in TABLE_CREATE_ORDER:
        spec = specs[table_name]
        parts = [
            f'"{name}" {sql_column_type(meta.get("type", "TEXT"), backend)}'
            for name, meta in spec["columns"].items()
        ]
        pk_cols = TABLE_PRIMARY_KEYS.get(table_name)
        if pk_cols:
            parts.append(f"PRIMARY KEY ({_quoted_columns(pk_cols)})")
        parts.extend(_inline_foreign_key_clauses(table_name, backend))
        col_defs = ", ".join(parts)
        if backend == "duckdb":
            db.execute(f'CREATE OR REPLACE TABLE "{table_name}" ({col_defs})')
        else:
            db.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            db.execute(f'CREATE TABLE "{table_name}" ({col_defs})')


def create_indexes(db: DataSink) -> None:
    """Create secondary indexes for query access patterns."""
    for table_name, index_name, columns, unique in INDEXES:
        unique_sql = "UNIQUE " if unique else ""
        cols_sql = _quoted_columns(columns)
        db.execute(
            f"CREATE {unique_sql}INDEX IF NOT EXISTS {index_name} "
            f'ON "{table_name}" ({cols_sql})'
        )


def analyze_statistics(db: DataSink) -> None:
    """Refresh planner statistics after bulk load."""
    db.execute("ANALYZE")


def list_index_names(db: DataSink) -> set[str]:
    """Return user-defined index names in the database."""
    if db.name == "sqlite":
        rows = db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
        return {row["name"] for row in rows}
    rows = db.execute(
        "SELECT index_name FROM duckdb_indexes() "
        "WHERE index_name NOT LIKE 'sqlite_%'"
    )
    return {row["index_name"] for row in rows}


def verify_schema(db: DataSink) -> None:
    """Assert expected indexes were created."""
    missing = EXPECTED_INDEX_NAMES - list_index_names(db)
    if missing:
        raise RuntimeError(f"Missing expected indexes: {sorted(missing)}")


def create_schema(db: DataSink, semantics: dict) -> None:
    """Create tables and indexes."""
    create_tables(db, semantics)
    create_indexes(db)


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


def load_bond_records(csv_dir: Path, issuers_to_currency: dict[str, str] = None) -> list[dict]:
    records: list[dict] = []
    for csv_path in sorted(csv_dir.glob("*.csv")):
        issuer = issuer_from_filename(csv_path)
        frame = pl.read_csv(csv_path)

        green_col = "green_bond" if "green_bond" in frame.columns else "green"
        for row in frame.iter_rows(named=True):
            coupon_raw = row.get("coupon")
            term_raw = row.get("original_term_years")
            currency = row.get("currency",None)
            if currency is None:
                currency = issuers_to_currency[issuer] if issuers_to_currency is not None else None
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
                    "currency": currency,
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
                "currency": record["currency"],
            }
            for record in records
        ]
    )
    return frame.with_columns(
        pl.col("coupon").cast(pl.Float64, strict=False),
        pl.col("issue_amount").cast(pl.Float64, strict=False),
    )


def populate_bond_universe(db: DataSink, csv_dir: Path, issuers_to_currency: dict[str, str] = None) -> int:
    frame = bond_universe_dataframe(load_bond_records(csv_dir, issuers_to_currency))
    append_polars(db, "bond_universe", frame)
    return frame.height


def build_bond_analytics_db(
    db_path: Path = DEFAULT_DB_PATH,
    semantics_path: Path = DEFAULT_SEMANTICS_PATH,
    csv_dir: Path = DEFAULT_CSV_DIR,
) -> None:
    semantics = load_semantics(semantics_path)
    issuer_codes = unique_issuer_acronyms(semantics)
    issuers_to_currency = issuers_to_currency_dict(semantics)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with open_sink(db_path) as db:
        if db.name == "sqlite":
            db.execute("PRAGMA foreign_keys = ON")
        create_schema(db, semantics)
        populate_tenor_pillars(db, issuer_codes)
        bond_count = populate_bond_universe(db, csv_dir, issuers_to_currency=issuers_to_currency)
        analyze_statistics(db)
        verify_schema(db)

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
