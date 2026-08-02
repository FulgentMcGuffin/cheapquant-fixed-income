"""Tests for /calc command parsing and CMT dispatch."""

from __future__ import annotations

from cqfi.agent.cli import handle_calc_command
from cqfi.agent.planner import CQFIRulePlanner
from cqfi.cli_tools import parse_calc_command


def test_parse_cmt_calc_two_args():
    parsed = parse_calc_command("/calc DEU 5y")
    assert parsed is not None
    assert parsed.kind == "cmt"
    assert parsed.issuer == "DEU"
    assert parsed.composite_tenor == "5y"
    assert parsed.trade_date is None


def test_parse_cmt_calc_three_args():
    parsed = parse_calc_command("/calc fra 10y2y 2024-02-15")
    assert parsed is not None
    assert parsed.kind == "cmt"
    assert parsed.issuer == "FRA"
    assert parsed.composite_tenor == "10y2y"
    assert parsed.trade_date == "2024-02-15"


def test_parse_cmt_calc_complex_tenor():
    parsed = parse_calc_command("/calc DEU 18m4w9m4d")
    assert parsed is not None
    assert parsed.kind == "cmt"
    assert parsed.composite_tenor == "18m4w9m4d"


def test_parse_bond_calc_with_date():
    parsed = parse_calc_command("/calc fraapr029 2024-02-15")
    assert parsed is not None
    assert parsed.kind == "bond"
    assert parsed.bond_id == "fraapr029"
    assert parsed.trade_date == "2024-02-15"


def test_parse_bond_calc_with_curve_and_term_structure():
    parsed = parse_calc_command(
        '/calc @fraapr029 2024-02-15 BOND_ZERO {"1m": 2.1, "3m": 2.15}'
    )
    assert parsed is not None
    assert parsed.kind == "bond"
    assert parsed.bond_id == "fraapr029"
    assert parsed.curve_label == "BOND_ZERO"
    assert parsed.numeric_term_structure == {"1m": 2.1, "3m": 2.15}


def test_parse_invalid_calc():
    parsed = parse_calc_command("/calc foo bar baz qux")
    assert parsed is not None
    assert parsed.kind == "invalid"


def test_handle_calc_command_accepts_cmt_form():
    assert handle_calc_command("/calc DEU 5y") is None
    assert handle_calc_command("/calc FRA 10y2y 2024-02-15") is None


def test_planner_routes_cmt_calc_to_tool():
    planner = CQFIRulePlanner()
    calls = planner.plan(
        "/calc DEU 5y",
        ["compute_bond_analytics", "compute_cmt_analytics"],
    )
    assert len(calls) == 1
    assert calls[0].name == "compute_cmt_analytics"
    assert calls[0].arguments["issuer"] == "DEU"
    assert calls[0].arguments["composite_tenor"] == "5y"


def test_planner_routes_bond_calc_to_tool():
    planner = CQFIRulePlanner()
    calls = planner.plan(
        "/calc fraapr029 2024-02-15 BOND_ZERO",
        ["compute_bond_analytics", "compute_cmt_analytics"],
    )
    assert len(calls) == 1
    assert calls[0].name == "compute_bond_analytics"
    assert calls[0].arguments["bond_id"] == "fraapr029"
