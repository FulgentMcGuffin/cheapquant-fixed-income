"""Tests for persisting bond/CMT analytics into quant_cache_db."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from cheapquant_fi.analytics_input import BondAnalyticsInput
from cheapquant_fi.analytics_output import FixedIncomeAnalyticsOutput
from cheapquant_fi.cache.decorators import cache_bond_analytics
from cheapquant_fi.cache.registry import (
    CacheRegistry,
    get_cache_registry,
    join_id,
    reset_cache_registry,
    short_id,
    utc_now_ms,
)
from cheapquant_fi.config import get_runtime_settings, load_settings
from cheapquant_fi.issuers import ISSUERS, RateType
from cheapquant_fi.quantlib.quantlib_analytics_calculator import (
    QuantLibAnalyticsCalculator,
)
from cheapquant_fi.quantlib.quantlib_curve import ql_build_zero_curve
from cheapquant_fi.quantlib.quantlib_market_context import (
    QuantLibCurveCollection,
    QuantlibMarketContext,
)
from cheapquant_fi.tenor import Tenor

_VAL_DATE = date(2024, 1, 15)
_MATURITY = date(2034, 1, 15)
_DEU = ISSUERS["DEU"]
_SLOPED_RATES = pl.DataFrame(
    {
        "tenor_label": ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"],
        "tenor_years": [1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
        "rate_pct": [3.40, 3.55, 3.70, 3.85, 3.80, 3.65],
    }
)
_CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")


def _bond_request(**kwargs) -> BondAnalyticsInput:
    base = dict(
        issuer="DEU",
        coupon=3.85,
        maturity_date=_MATURITY,
        settlement_date=_VAL_DATE,
        trade_date=_VAL_DATE,
        issue_date=_VAL_DATE,
    )
    base.update(kwargs)
    return BondAnalyticsInput(**base)


@pytest.fixture
def calculator() -> QuantLibAnalyticsCalculator:
    return QuantLibAnalyticsCalculator()


@pytest.fixture
def deu_market() -> QuantlibMarketContext:
    handle, _ = ql_build_zero_curve(_DEU, _VAL_DATE, _SLOPED_RATES, RateType.ZERO)
    collection = QuantLibCurveCollection(as_of=_VAL_DATE)
    collection.set_bond_curve("DEU", handle)
    market = QuantlibMarketContext(as_of=_VAL_DATE)
    market.set_curve_collection(collection, label="BOND_ZERO")
    return market


@pytest.fixture
def quant_cache_env(tmp_path: Path, monkeypatch):
    """Point AppSettings + runtime toggle at a temp sqlite quant_cache DB."""
    reset_cache_registry()
    semantics_src = Path("semantics/quant_cache.yaml")
    semantics_dst = tmp_path / "quant_cache.yaml"
    semantics_dst.write_text(semantics_src.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "quant_cache.sqlite"
    sem_input = tmp_path / "ycs_data.yaml"
    sem_bond = tmp_path / "bond_analytics.yaml"
    sem_input.write_text("dataset: x\ntables: {}\n", encoding="utf-8")
    sem_bond.write_text("dataset: x\ntables: {}\n", encoding="utf-8")
    ycs_db = tmp_path / "input.db"
    bond_db = tmp_path / "bond_analytics.db"
    ycs_db.write_bytes(b"")
    bond_db.write_bytes(b"")
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    cfg = tmp_path / "cqfi.yaml"
    cfg.write_text(
        f"""
paths:
  ycs_db: {ycs_db.as_posix()}
  ycs_semantics: {sem_input.as_posix()}
  bond_analytics_db: {bond_db.as_posix()}
  bond_analytics_semantics: {sem_bond.as_posix()}
  quant_cache_db: {db_path.as_posix()}
  quant_cache_semantics: {semantics_dst.as_posix()}
  sessions_dir: {sessions.as_posix()}
settings:
  write_to_bond_analytics_db: false
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("CQFI_QUANT_CACHE_DB", raising=False)
    monkeypatch.delenv("CQFI_CACHE_DB", raising=False)
    settings = load_settings(cfg)
    rt = get_runtime_settings()
    previous = rt.use_quant_cache
    rt.update(use_quant_cache=True)
    yield settings, db_path, semantics_dst
    rt.update(use_quant_cache=previous)
    reset_cache_registry()


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_join_id_uses_hyphen_separator():
    assert join_id("custom", "test") == "custom-test"
    assert join_id("CUSTOM", "DEU", "abc") == "CUSTOM-DEU-abc"
    assert join_id("a", "", "b") == "a-b"


def test_short_id_length_and_uniqueness():
    ids = {short_id() for _ in range(50)}
    assert all(len(i) == 12 for i in ids)
    assert len(ids) == 50


def test_utc_now_ms_format():
    stamp = utc_now_ms()
    assert _CREATED_AT_RE.match(stamp)
    datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f")


def test_tenor_label_days_is_parseable():
    label = CacheRegistry._tenor_label_days(date(2024, 1, 15), date(2027, 6, 1))
    assert label == "1233d"
    assert Tenor.parse(label).days == 1233


def test_tenor_label_clamps_negative_to_zero():
    assert CacheRegistry._tenor_label_days(date(2030, 1, 1), date(2020, 1, 1)) == "0d"


# ---------------------------------------------------------------------------
# CacheRegistry schema / low-level persist
# ---------------------------------------------------------------------------


def test_registry_creates_tables_from_semantics(quant_cache_env):
    _settings, db_path, semantics = quant_cache_env
    reg = CacheRegistry(db_path, semantics)
    try:
        conn = _connect(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        cols_bond = {
            r[1] for r in conn.execute("PRAGMA table_info(bond_analytics)").fetchall()
        }
        cols_cmt = {
            r[1] for r in conn.execute("PRAGMA table_info(cmt_analytics)").fetchall()
        }
        conn.close()
    finally:
        reg.close()

    assert "bond_analytics" in tables
    assert "cmt_analytics" in tables
    assert {"analytic_id", "bond_id", "mm_cmt_analytic_id", "yield_to_maturity"} <= cols_bond
    assert {"cmt_analytic_id", "tenor_label", "coupon", "is_fixed_coupon"} <= cols_cmt


def test_persist_bond_compute_with_explicit_bond_id(quant_cache_env):
    _settings, db_path, semantics = quant_cache_env
    reg = CacheRegistry(db_path, semantics)
    request = _bond_request(bond_id="DEU0001")
    bond = FixedIncomeAnalyticsOutput(
        yield_to_maturity=3.9,
        clean_price=99.5,
        par_yield=3.88,
        z_spread=None,  # must be omitted from INSERT
    )
    mm = FixedIncomeAnalyticsOutput(yield_to_maturity=3.88, clean_price=100.0)
    fc = FixedIncomeAnalyticsOutput(yield_to_maturity=3.95, clean_price=99.2)
    try:
        reg.persist_bond_compute(
            owner="FakeCalc",
            method="compute_bond_analytics",
            request=request,
            curve_label="BOND_ZERO",
            bond_metrics=bond,
            mm_cmt_metrics=mm,
            mm_fc_cmt_metrics=fc,
        )
        conn = _connect(db_path)
        bond_row = conn.execute(
            "SELECT analytic_id, bond_id, created_at, curve_used, curve_settings, "
            "z_spread, mm_cmt_analytic_id, mm_fc_cmt_analytic_id "
            "FROM bond_analytics"
        ).fetchone()
        cmt_by_id = {
            r[0]: r
            for r in conn.execute(
                "SELECT cmt_analytic_id, coupon, tenor_label, issuer, maturity_date "
                "FROM cmt_analytics"
            ).fetchall()
        }
        # Sparse: z_spread column exists but value should be NULL (not written as 0).
        z_spread = bond_row[5]
        conn.close()
    finally:
        reg.close()

    analytic_id, bond_id, created_at, curve_used, curve_settings, _, mm_id, mm_fc_id = (
        bond_row
    )
    assert bond_id == "DEU0001"
    assert analytic_id.startswith("FakeCalc:compute_bond_analytics-")
    assert _CREATED_AT_RE.match(created_at)
    assert curve_used == 1
    assert curve_settings == "DEU-BOND_ZERO"
    assert z_spread is None
    assert mm_id.startswith("DEU0001-")
    assert mm_fc_id.startswith("DEU0001-")
    assert mm_id != mm_fc_id
    assert bond.mm_cmt_analytic_id == mm_id
    assert bond.mm_fc_cmt_analytic_id == mm_fc_id

    assert cmt_by_id[mm_id][1] == pytest.approx(3.88)  # par coupon
    assert cmt_by_id[mm_fc_id][1] == pytest.approx(3.85)  # bond coupon
    assert cmt_by_id[mm_id][2].endswith("d")
    assert cmt_by_id[mm_id][3] == "DEU"
    assert cmt_by_id[mm_id][4] == _MATURITY.isoformat()


def test_persist_skips_none_cmt_outputs(quant_cache_env):
    _settings, db_path, semantics = quant_cache_env
    reg = CacheRegistry(db_path, semantics)
    try:
        reg.persist_bond_compute(
            owner="FakeCalc",
            method="compute_bond_analytics",
            request=_bond_request(
                input_column="clean_price",
                input_value=99.0,
                bond_id="X1",
            ),
            curve_label="BOND_PAR",
            bond_metrics=FixedIncomeAnalyticsOutput(clean_price=99.0),
            mm_cmt_metrics=None,
            mm_fc_cmt_metrics=None,
        )
        conn = _connect(db_path)
        n_bond = conn.execute("SELECT COUNT(*) FROM bond_analytics").fetchone()[0]
        n_cmt = conn.execute("SELECT COUNT(*) FROM cmt_analytics").fetchone()[0]
        curve_used, input_col, mm_id, mm_fc_id = conn.execute(
            "SELECT curve_used, input_column, mm_cmt_analytic_id, mm_fc_cmt_analytic_id "
            "FROM bond_analytics"
        ).fetchone()
        conn.close()
    finally:
        reg.close()

    assert n_bond == 1
    assert n_cmt == 0
    assert curve_used == 0
    assert input_col == "clean_price"
    assert mm_id is None
    assert mm_fc_id is None


def test_reset_analytics_tables_clears_rows(quant_cache_env):
    _settings, db_path, semantics = quant_cache_env
    reg = CacheRegistry(db_path, semantics)
    try:
        reg.persist_bond_compute(
            owner="A",
            method="m",
            request=_bond_request(bond_id="B1"),
            curve_label="BOND_ZERO",
            bond_metrics=FixedIncomeAnalyticsOutput(clean_price=100.0, par_yield=3.5),
            mm_cmt_metrics=FixedIncomeAnalyticsOutput(clean_price=100.0),
            mm_fc_cmt_metrics=FixedIncomeAnalyticsOutput(clean_price=99.0),
        )
        reg.reset_analytics_tables()
        conn = _connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM bond_analytics").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM cmt_analytics").fetchone()[0] == 0
        conn.close()
    finally:
        reg.close()


def test_get_cache_registry_singleton_and_reset(quant_cache_env):
    settings, db_path, semantics = quant_cache_env
    a = get_cache_registry(db_path, semantics)
    b = get_cache_registry()
    assert a is b
    reset_cache_registry()
    c = get_cache_registry(db_path, semantics)
    assert c is not a


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def test_cache_bond_analytics_decorator_calls_registry(monkeypatch, quant_cache_env):
    _settings, _db_path, _sem = quant_cache_env
    get_runtime_settings().update(use_quant_cache=True)
    mock_reg = MagicMock()
    monkeypatch.setattr(
        "cheapquant_fi.cache.decorators.get_cache_registry", lambda: mock_reg
    )

    class FakeCalc:
        @cache_bond_analytics
        def compute_bond_analytics(self, request, market=None, *, curve_label="BOND_ZERO"):
            bond = FixedIncomeAnalyticsOutput(clean_price=100.0, par_yield=3.5)
            mm = FixedIncomeAnalyticsOutput(clean_price=100.0)
            fc = FixedIncomeAnalyticsOutput(clean_price=99.0)
            return bond, mm, fc

    req = _bond_request()
    FakeCalc().compute_bond_analytics(req, None, curve_label="BOND_PAR")
    mock_reg.persist_bond_compute.assert_called_once()
    kwargs = mock_reg.persist_bond_compute.call_args.kwargs
    assert kwargs["owner"] == "FakeCalc"
    assert kwargs["method"] == "compute_bond_analytics"
    assert kwargs["curve_label"] == "BOND_PAR"
    assert kwargs["request"] is req
    assert kwargs["mm_cmt_metrics"] is not None
    assert kwargs["mm_fc_cmt_metrics"] is not None


def test_cache_bond_analytics_decorator_skips_when_disabled(monkeypatch, quant_cache_env):
    _settings, _db_path, _sem = quant_cache_env
    get_runtime_settings().update(use_quant_cache=False)
    mock_reg = MagicMock()
    monkeypatch.setattr(
        "cheapquant_fi.cache.decorators.get_cache_registry", lambda: mock_reg
    )

    class FakeCalc:
        @cache_bond_analytics
        def compute_bond_analytics(self, request, market=None, *, curve_label="BOND_ZERO"):
            return FixedIncomeAnalyticsOutput(clean_price=100.0), None, None

    FakeCalc().compute_bond_analytics(_bond_request())
    mock_reg.persist_bond_compute.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end via QuantLibAnalyticsCalculator
# ---------------------------------------------------------------------------


def test_compute_bond_analytics_persists_three_rows(
    quant_cache_env, calculator, deu_market
):
    _settings, db_path, _sem = quant_cache_env
    bond, mm_cmt, mm_fc = calculator.compute_bond_analytics(
        _bond_request(bond_id=None), deu_market, curve_label="BOND_ZERO"
    )
    assert mm_cmt is not None
    assert mm_fc is not None
    assert bond.mm_cmt_analytic_id is not None
    assert bond.mm_fc_cmt_analytic_id is not None
    assert bond.mm_cmt_analytic_id != bond.mm_fc_cmt_analytic_id

    conn = _connect(db_path)
    bond_rows = conn.execute(
        "SELECT analytic_id, bond_id, curve_settings, created_at, "
        "mm_cmt_analytic_id, mm_fc_cmt_analytic_id, yield_to_maturity "
        "FROM bond_analytics"
    ).fetchall()
    cmt_rows = conn.execute(
        "SELECT cmt_analytic_id, tenor_label, coupon, is_fixed_coupon, curve_settings "
        "FROM cmt_analytics"
    ).fetchall()
    conn.close()

    assert len(bond_rows) == 1
    analytic_id, bond_id, curve_settings, created_at, mm_id, mm_fc_id, ytm = bond_rows[0]
    assert analytic_id.startswith(
        "QuantLibAnalyticsCalculator:compute_bond_analytics-"
    )
    assert bond_id == join_id("CUSTOM", "DEU", analytic_id)
    assert curve_settings == "DEU-BOND_ZERO"
    assert _CREATED_AT_RE.match(created_at)
    assert mm_id == bond.mm_cmt_analytic_id
    assert mm_fc_id == bond.mm_fc_cmt_analytic_id
    assert ytm == pytest.approx(bond.yield_to_maturity)

    assert len(cmt_rows) == 2
    by_id = {r[0]: r for r in cmt_rows}
    assert set(by_id) == {mm_id, mm_fc_id}

    mm_coupon = by_id[mm_id][2]
    fc_coupon = by_id[mm_fc_id][2]
    assert mm_coupon == pytest.approx(bond.par_yield)
    assert fc_coupon == pytest.approx(3.85)

    expected_tenor = CacheRegistry._tenor_label_days(_VAL_DATE, _MATURITY)
    for _id, tenor_label, _coupon, is_fc, cmt_curve in cmt_rows:
        assert tenor_label == expected_tenor
        assert is_fc == 1
        assert cmt_curve == "DEU-BOND_ZERO"
        Tenor.parse(tenor_label)


def test_compute_persists_with_universe_bond_id(
    quant_cache_env, calculator, deu_market
):
    _settings, db_path, _sem = quant_cache_env
    bond, mm_cmt, mm_fc = calculator.compute_bond_analytics(
        _bond_request(bond_id="DEU10Y001"), deu_market
    )
    assert mm_cmt is not None and mm_fc is not None

    conn = _connect(db_path)
    bond_id, mm_id, mm_fc_id = conn.execute(
        "SELECT bond_id, mm_cmt_analytic_id, mm_fc_cmt_analytic_id FROM bond_analytics"
    ).fetchone()
    conn.close()
    assert bond_id == "DEU10Y001"
    assert mm_id.startswith("DEU10Y001-")
    assert mm_fc_id.startswith("DEU10Y001-")
    assert bond.mm_cmt_analytic_id == mm_id


def test_use_quant_cache_false_skips_persist(quant_cache_env, calculator, deu_market):
    _settings, db_path, _sem = quant_cache_env
    get_runtime_settings().update(use_quant_cache=False)
    calculator.compute_bond_analytics(_bond_request(), deu_market)
    if db_path.exists():
        conn = _connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM bond_analytics").fetchone()[0]
        conn.close()
        assert n == 0


def test_input_path_persists_bond_only(quant_cache_env, calculator, deu_market):
    _settings, db_path, _sem = quant_cache_env
    bond, mm_cmt, mm_fc = calculator.compute_bond_analytics(
        _bond_request(input_column="clean_price", input_value=99.5, bond_id="IN1"),
        deu_market,
    )
    assert mm_cmt is None and mm_fc is None
    assert bond.mm_cmt_analytic_id is None
    assert bond.mm_fc_cmt_analytic_id is None

    conn = _connect(db_path)
    n_bond = conn.execute("SELECT COUNT(*) FROM bond_analytics").fetchone()[0]
    n_cmt = conn.execute("SELECT COUNT(*) FROM cmt_analytics").fetchone()[0]
    curve_used, input_col = conn.execute(
        "SELECT curve_used, input_column FROM bond_analytics"
    ).fetchone()
    conn.close()
    assert n_bond == 1
    assert n_cmt == 0
    assert curve_used == 0
    assert input_col == "clean_price"


def test_registry_supports_duckdb(tmp_path: Path):
    duckdb = pytest.importorskip("duckdb")

    semantics = Path("semantics/quant_cache.yaml")
    db_path = tmp_path / "quant_cache.duckdb"
    reg = CacheRegistry(db_path, semantics)
    try:
        reg.persist_bond_compute(
            owner="DuckCalc",
            method="compute_bond_analytics",
            request=_bond_request(bond_id="D1"),
            curve_label="BOND_ZERO",
            bond_metrics=FixedIncomeAnalyticsOutput(clean_price=100.0, par_yield=3.5),
            mm_cmt_metrics=FixedIncomeAnalyticsOutput(clean_price=100.0),
            mm_fc_cmt_metrics=None,
        )
        con = duckdb.connect(str(db_path))
        n_bond = con.execute("SELECT COUNT(*) FROM bond_analytics").fetchone()[0]
        n_cmt = con.execute("SELECT COUNT(*) FROM cmt_analytics").fetchone()[0]
        con.close()
    finally:
        reg.close()
    assert n_bond == 1
    assert n_cmt == 1
