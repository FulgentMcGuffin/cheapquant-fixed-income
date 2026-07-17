"""Basic tests for routing and CMT pricing (requires input_data.db)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import QuantLib as ql
import pytest

from cheapquant_fi.agent.cli import DatasetTarget, route_query
from cheapquant_fi.config import DEFAULT_CONFIG_PATH, load_settings
from cheapquant_fi.issuers import ISSUERS, ExDividendConvention


def test_route_input_explicit():
    routed = route_query("input: avg 10Y zero for DEU")
    assert routed is not None
    assert routed.target == DatasetTarget.INPUT
    assert "10Y" in routed.text


def test_route_cache_explicit():
    routed = route_query("cache: latest CMT prices for USA")
    assert routed is not None
    assert routed.target == DatasetTarget.CACHE


def test_route_inferred_zero_rates():
    routed = route_query("What was the 2s10s slope for Italy?")
    assert routed is not None
    assert routed.target == DatasetTarget.INPUT


def test_route_inferred_cmt():
    routed = route_query("Show CMT clean prices we computed")
    assert routed is not None
    assert routed.target == DatasetTarget.CACHE


def test_gbr_has_ex_dividend_convention():
    gbr = ISSUERS["GBR"]
    assert gbr.ex_dividend is not None
    assert isinstance(gbr.ex_dividend, ExDividendConvention)


def test_gbr_ex_dividend_period_is_7_business_days():
    gbr = ISSUERS["GBR"]
    assert gbr.ex_dividend is not None
    period = gbr.ex_dividend.period
    assert period.units() == ql.Days
    assert period.length() == 7


def test_gbr_ex_coupon_args_returns_four_values():
    gbr = ISSUERS["GBR"]
    assert gbr.ex_dividend is not None
    args = gbr.ex_dividend.ex_coupon_args()
    assert len(args) == 4
    period, calendar, convention, eom = args
    assert isinstance(period, ql.Period)
    assert isinstance(eom, bool)


def test_issuers_without_ex_dividend_unaffected():
    for code in ("USA", "DEU", "JPN"):
        assert ISSUERS[code].ex_dividend is None


@pytest.mark.skipif(
    not Path(r"C:\data\sqlitedb\input_data.db").exists(),
    reason="input_data.db not available",
)
def test_price_cmts_usa():
    from cheapquant_fi.cache.manager import CacheManager

    settings = load_settings(DEFAULT_CONFIG_PATH)
    settings.ensure_dirs()
    mgr = CacheManager(settings)
    try:
        result = mgr.price_cmts("USA", "2020-01-02")
        assert len(result) > 0
        assert "clean_price" in result.columns
        assert result["issuer"][0] == "USA"
    finally:
        mgr.close()


@pytest.mark.skipif(
    not Path(r"C:\data\sqlitedb\input_data.db").exists(),
    reason="input_data.db not available",
)
def test_price_cmts_gbr_par_curve_bootstrap():
    """Par-curve bootstrap for GBR should complete using ex-dividend helpers."""
    from cheapquant_fi.cache.manager import CacheManager

    settings = load_settings(DEFAULT_CONFIG_PATH)
    settings.ensure_dirs()
    mgr = CacheManager(settings)
    try:
        result = mgr.price_cmts("GBR", "2020-01-02", rate_type="par")
        assert len(result) > 0
        assert result["issuer"][0] == "GBR"
        assert all(result["clean_price"].to_list())
    finally:
        mgr.close()
