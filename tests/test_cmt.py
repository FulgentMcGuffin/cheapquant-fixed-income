"""Basic tests for routing and CMT pricing (requires input_data.db)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cheapquant_fi.agent.cli import DatasetTarget, route_query
from cheapquant_fi.config import DEFAULT_CONFIG_PATH, load_settings


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


@pytest.mark.skipif(
    not Path(r"C:\data\sqlitedb\input_data.db").exists(),
    reason="input_data.db not available",
)
def test_price_cmts_usa():
    from cheapquant_fi.cache.manager import CacheManager
    from cheapquant_fi.config import load_settings

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
