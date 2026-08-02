"""Tests for the /dlv and /fut CLI handlers and their tool routing."""

from __future__ import annotations

import pytest

from cqfi.agent.cli import (
    EXTRA_TOOLS,
    HELP_TEXT_CQFI,
    LOCAL_TOOL_NAMES,
    handle_dlv_command,
    handle_fut_command,
)
from cqfi.agent.planner import CQFIRulePlanner


# --------------------------------------------------------------------------- #
# Help and validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["/dlv", "/dlv   ", "  /DLV  "])
def test_bare_dlv_returns_help(text):
    assert "Bond Future Delivery Baskets" in handle_dlv_command(text)


@pytest.mark.parametrize("text", ["/fut", "/fut  ", " /FUT "])
def test_bare_fut_returns_help(text):
    assert "Bond Future Basis Analytics" in handle_fut_command(text)


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("/dlv onlyname", "basket name and a bond future code"),
        ("/dlv mybasket ZZZZ", "Unknown bond future code"),
        ("/dlv mine FGBM a|notanumber 2025-12", "bond identifier"),
    ],
)
def test_invalid_dlv_explains_and_shows_help(text, fragment):
    message = handle_dlv_command(text)
    assert message is not None
    assert "Invalid /dlv command" in message
    assert fragment in message
    assert "Bond Future Delivery Baskets" in message


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("/fut IKH7 notadate", "trade date"),
        ("/fut a b c", "basket name or contract code"),
    ],
)
def test_invalid_fut_explains_and_shows_help(text, fragment):
    message = handle_fut_command(text)
    assert message is not None
    assert "Invalid /fut command" in message
    assert fragment in message
    assert "Bond Future Basis Analytics" in message


@pytest.mark.parametrize(
    "text",
    [
        "/dlv mybasket FGBM",
        "/dlv hist FGBS 2020-09",
        "/dlv mine FOA fraapr029|1.0326 frajun030|1.0291 2025-12",
    ],
)
def test_valid_dlv_passes_through_for_execution(text):
    """A valid command returns None so the executor runs it."""
    assert handle_dlv_command(text) is None


@pytest.mark.parametrize("text", ["/fut IKH7", "/fut IKH7 2026-05-15", "/fut mine"])
def test_valid_fut_passes_through_for_execution(text):
    assert handle_fut_command(text) is None


@pytest.mark.parametrize(
    "text", ["/bond usa10y001", "/calc fraapr029", "price cmt USA 2020-01-02", "help"]
)
def test_handlers_ignore_other_commands(text):
    assert handle_dlv_command(text) is None
    assert handle_fut_command(text) is None


# --------------------------------------------------------------------------- #
# Discoverability and tool registration
# --------------------------------------------------------------------------- #
def test_help_documents_both_commands():
    assert "Bond future commands:" in HELP_TEXT_CQFI
    assert "/dlv <name> <future>" in HELP_TEXT_CQFI
    assert "/fut <basket|contract>" in HELP_TEXT_CQFI


def test_tools_are_registered_for_the_llm():
    names = {tool.name for tool in EXTRA_TOOLS}
    assert {"build_delivery_basket", "compute_bond_future_analytics"} <= names


def test_tools_are_intercepted_locally():
    assert "build_delivery_basket" in LOCAL_TOOL_NAMES
    assert "compute_bond_future_analytics" in LOCAL_TOOL_NAMES


def test_tool_schemas_come_from_the_docstrings():
    """parse_docstring=True is what gives the LLM usable argument descriptions."""
    tools = {tool.name: tool for tool in EXTRA_TOOLS}

    basket = tools["build_delivery_basket"]
    assert set(basket.args) == {"name", "future_code", "delivery", "bond_ids"}
    assert "conversion factor" in basket.args["bond_ids"]["description"]

    analytics = tools["compute_bond_future_analytics"]
    assert set(analytics.args) == {
        "target",
        "trade_date",
        "futures_price",
        "curve_label",
        "numeric_term_structure",
    }
    assert "cheapest-to-deliver" in analytics.description


# --------------------------------------------------------------------------- #
# Rule-based planner routing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/dlv mine FGBM", {"name": "mine", "future_code": "FGBM"}),
        (
            "/dlv mine FGBM M8",
            {"name": "mine", "future_code": "FGBM", "delivery": "M8"},
        ),
        (
            "/dlv mine FOA a|1.5 b 2025-12",
            {
                "name": "mine",
                "future_code": "FOA",
                "delivery": "2025-12",
                "bond_ids": ["a|1.5", "b"],
            },
        ),
    ],
)
def test_planner_routes_dlv(text, expected):
    calls = CQFIRulePlanner().plan(text, LOCAL_TOOL_NAMES)
    assert len(calls) == 1
    assert calls[0].name == "build_delivery_basket"
    assert calls[0].arguments == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/fut IKH7", {"target": "IKH7"}),
        ("/fut mine 2025-10-15", {"target": "mine", "trade_date": "2025-10-15"}),
        (
            "/fut IKH7 2025-10-15 3.0",
            {"target": "IKH7", "trade_date": "2025-10-15", "numeric_term_structure": 3.0},
        ),
        (
            '/fut IKH7 2025-10-15 {"3m": 3.0}',
            {
                "target": "IKH7",
                "trade_date": "2025-10-15",
                "numeric_term_structure": {"3m": 3.0},
            },
        ),
    ],
)
def test_planner_routes_fut(text, expected):
    calls = CQFIRulePlanner().plan(text, LOCAL_TOOL_NAMES)
    assert len(calls) == 1
    assert calls[0].name == "compute_bond_future_analytics"
    assert calls[0].arguments == expected


def test_planner_skips_when_the_tool_is_unavailable():
    """Routing is gated on the tool actually being offered."""
    assert CQFIRulePlanner().plan("/dlv mine FGBM", ["get_bond"]) == []
    assert CQFIRulePlanner().plan("/fut IKH7", ["get_bond"]) == []


def test_planner_ignores_invalid_commands():
    assert CQFIRulePlanner().plan("/dlv onlyname", LOCAL_TOOL_NAMES) == []
    assert CQFIRulePlanner().plan("/fut a b c", LOCAL_TOOL_NAMES) == []
