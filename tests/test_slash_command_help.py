"""Tests for slash-command help when entered without valid arguments."""

from __future__ import annotations

from cqfi.agent.cli import (
    handle_bond_command,
    handle_calc_command,
    handle_dlv_command,
    handle_fut_command,
    handle_mctx_command,
)


def test_bond_bare_shows_help():
    result = handle_bond_command("/bond")
    assert result is not None
    assert "Bond Information Lookup" in result
    assert "/bond <id>" in result


def test_bond_invalid_shows_help():
    result = handle_bond_command("/bond id1 id2")
    assert result is not None
    assert "Invalid /bond command" in result
    assert "Bond Information Lookup" in result


def test_bond_valid_not_handled():
    assert handle_bond_command("/bond fraapr029") is None
    assert handle_bond_command("/bond @usa10y001") is None


def test_mctx_bare_shows_help():
    result = handle_mctx_command("/mctx")
    assert result is not None
    assert "Market Context Verification" in result
    assert "/mctx <date>" in result


def test_mctx_invalid_shows_help():
    result = handle_mctx_command("/mctx not-a-date")
    assert result is not None
    assert "Invalid /mctx command" in result
    assert "Market Context Verification" in result


def test_mctx_valid_not_handled():
    assert handle_mctx_command("/mctx 2024-02-15 FRA") is None
    assert handle_mctx_command("/mctx 2024-02-15") is None


def test_calc_bare_shows_help():
    result = handle_calc_command("/calc")
    assert result is not None
    assert "Bond and CMT Analytics Calculation" in result
    assert "/calc <bond_id>" in result
    assert "/calc <issuer> <composite_tenor>" in result


def test_calc_invalid_shows_help():
    result = handle_calc_command("/calc foo 2024-01-15 BOND_ZERO {bad")
    assert result is not None
    assert "Invalid /calc command" in result
    assert "Bond and CMT Analytics Calculation" in result


def test_calc_valid_not_handled():
    assert handle_calc_command("/calc fraapr029") is None
    assert handle_calc_command("/calc usa10y001 2024-02-15 BOND_ZERO") is None
    assert handle_calc_command("/calc DEU 5y") is None
    assert handle_calc_command("/calc FRA 10y2y 2024-02-15") is None


def test_dlv_bare_shows_help():
    result = handle_dlv_command("/dlv")
    assert result is not None
    assert "Bond Future Delivery Baskets" in result
    assert "/dlv <name> <future_code>" in result


def test_dlv_invalid_shows_help():
    result = handle_dlv_command("/dlv mybasket ZZZZ")
    assert result is not None
    assert "Invalid /dlv command" in result
    assert "Bond Future Delivery Baskets" in result


def test_dlv_valid_not_handled():
    assert handle_dlv_command("/dlv mybasket FGBM") is None
    assert handle_dlv_command("/dlv hist FGBS 2020-09") is None
    assert handle_dlv_command("/dlv mine FOA fraapr029|1.0326 2025-12") is None


def test_fut_bare_shows_help():
    result = handle_fut_command("/fut")
    assert result is not None
    assert "Bond Future Basis Analytics" in result
    assert "/fut <basket_or_contract>" in result


def test_fut_invalid_shows_help():
    result = handle_fut_command("/fut IKH7 not-a-date")
    assert result is not None
    assert "Invalid /fut command" in result
    assert "Bond Future Basis Analytics" in result


def test_fut_valid_not_handled():
    assert handle_fut_command("/fut IKH7") is None
    assert handle_fut_command("/fut mybasket 2025-10-15") is None


def test_unrelated_commands_not_handled():
    assert handle_bond_command("/calc fraapr029") is None
    assert handle_mctx_command("/cache on") is None
    assert handle_calc_command("/bond usa10y001") is None
    assert handle_dlv_command("/fut IKH7") is None
    assert handle_fut_command("/dlv mybasket FGBM") is None
