"""Tests for ``/dlv`` and ``/fut`` command parsing (no database access)."""

from __future__ import annotations

from datetime import date

import pytest

from cqfi.bond_futures import resolve_delivery_month
from cqfi.delivery_basket import parse_dlv_command, parse_fut_command

# Fixed "today" so every relative delivery specifier is deterministic.
TODAY = date(2026, 8, 2)


def _delivery(result) -> tuple[int, int]:
    return resolve_delivery_month(result.delivery_token, today=TODAY)


# --------------------------------------------------------------------------- #
# /dlv
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text", ["price cmt USA 2020-01-02", "/bond @x", "/fut IKH7", "", "dlv a b"]
)
def test_returns_none_for_other_commands(text):
    assert parse_dlv_command(text, today=TODAY) is None


@pytest.mark.parametrize("text", ["/dlv", "/dlv   ", "  /DLV  "])
def test_bare_command_requests_help(text):
    assert parse_dlv_command(text, today=TODAY).kind == "help"


@pytest.mark.parametrize(
    ("text", "name", "code", "expected_delivery"),
    [
        ("/dlv mybasket FGBM", "mybasket", "FGBM", (2026, 9)),
        ("/dlv mybasket OE", "mybasket", "OE", (2026, 9)),
        ("/dlv mybasket FGBM M8", "mybasket", "FGBM", (2028, 6)),
        ("/dlv mybasket FGBM U", "mybasket", "FGBM", (2026, 9)),
        ("/dlv mybasket FGBM 6", "mybasket", "FGBM", (2026, 9)),
        ("/dlv hist FGBS 2020-09", "hist", "FGBS", (2020, 9)),
        ("/dlv hist FGBS U2020", "hist", "FGBS", (2020, 9)),
    ],
)
def test_auto_baskets(text, name, code, expected_delivery):
    result = parse_dlv_command(text, today=TODAY)
    assert result.kind == "auto"
    assert result.name == name
    assert result.future_code == code
    assert result.bond_specs == ()
    assert _delivery(result) == expected_delivery


def test_explicit_bond_ids():
    result = parse_dlv_command(
        "/dlv mybasket FOA fraapr029 frajun030 2025-12", today=TODAY
    )
    assert result.kind == "explicit"
    assert result.name == "mybasket"
    assert result.future_code == "FOA"
    assert result.bond_specs == (("fraapr029", None), ("frajun030", None))
    assert _delivery(result) == (2025, 12)


def test_explicit_conversion_factor_overrides():
    result = parse_dlv_command(
        "/dlv mybasket FOA fraapr029|1.0326 frajun030|1.0291 2025-12", today=TODAY
    )
    assert result.kind == "explicit"
    assert result.bond_specs == (("fraapr029", 1.0326), ("frajun030", 1.0291))
    assert _delivery(result) == (2025, 12)


def test_explicit_ids_without_a_delivery_token_use_the_default():
    result = parse_dlv_command("/dlv mine FOA fraapr029", today=TODAY)
    assert result.kind == "explicit"
    assert result.delivery_token is None
    assert _delivery(result) == (2026, 9)


def test_mixed_overrides_and_plain_ids():
    result = parse_dlv_command("/dlv mine FOA a|1.5 b c|0.9", today=TODAY)
    assert result.bond_specs == (("a", 1.5), ("b", None), ("c", 0.9))


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("/dlv onlyname", "basket name and a bond future code"),
        ("/dlv mybasket ZZZZ", "Unknown bond future code"),
        ("/dlv mybasket FGBM a|notanumber 2025-12", "bond identifier"),
        ("/dlv mybasket FGBM a|1.0|2.0 2025-12", "bond identifier"),
    ],
)
def test_invalid_commands_explain_themselves(text, fragment):
    result = parse_dlv_command(text, today=TODAY)
    assert result.kind == "invalid"
    assert fragment in result.message


def test_command_is_case_insensitive():
    assert parse_dlv_command("/DLV mine ik", today=TODAY).future_code == "ik"


# --------------------------------------------------------------------------- #
# /fut
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["/dlv mine FGBM", "price cmt USA 2020-01-02", ""])
def test_fut_returns_none_for_other_commands(text):
    assert parse_fut_command(text) is None


@pytest.mark.parametrize("text", ["/fut", "/fut  ", " /FUT "])
def test_fut_bare_command_requests_help(text):
    assert parse_fut_command(text).kind == "help"


@pytest.mark.parametrize(
    ("text", "target", "trade_date"),
    [
        ("/fut IKH7", "IKH7", None),
        ("/fut IKH7 2026-05-15", "IKH7", date(2026, 5, 15)),
        ("/fut mybasket 2025-10-15", "mybasket", date(2025, 10, 15)),
        ("/fut mybasket", "mybasket", None),
    ],
)
def test_fut_analytics_requests(text, target, trade_date):
    result = parse_fut_command(text)
    assert result.kind == "analytics"
    assert result.target == target
    assert result.trade_date == trade_date


@pytest.mark.parametrize(
    "text",
    ["/fut IKH7 notadate", "/fut IKH7 2026-13-01", "/fut a b c", "/fut a 15-05-2026"],
)
def test_fut_invalid_requests(text):
    result = parse_fut_command(text)
    assert result.kind == "invalid"
    assert result.message


# --------------------------------------------------------------------------- #
# /fut repo term structure
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "target", "trade_date", "repo"),
    [
        ("/fut IKH7 3.0", "IKH7", None, 3.0),
        ("/fut IKH7 -0.5", "IKH7", None, -0.5),
        ("/fut IKH7 2026-05-15 3.0", "IKH7", date(2026, 5, 15), 3.0),
        (
            '/fut IKH7 2026-05-15 {"3m": 3.0, "1y": 3.2}',
            "IKH7",
            date(2026, 5, 15),
            {"3m": 3.0, "1y": 3.2},
        ),
        (
            '/fut mybasket {"3m": 3.0, "1y": 3.2}',
            "mybasket",
            None,
            {"3m": 3.0, "1y": 3.2},
        ),
    ],
)
def test_fut_repo_term_structure_is_parsed(text, target, trade_date, repo):
    result = parse_fut_command(text)
    assert result.kind == "analytics"
    assert result.target == target
    assert result.trade_date == trade_date
    assert result.numeric_term_structure == repo


def test_fut_without_repo_leaves_numeric_term_structure_none():
    result = parse_fut_command("/fut IKH7 2026-05-15")
    assert result.kind == "analytics"
    assert result.numeric_term_structure is None


@pytest.mark.parametrize(
    "text",
    [
        '/fut IKH7 2026-05-15 {not valid json}',
        "/fut IKH7 2026-05-15 {1, 2, 3}",
    ],
)
def test_fut_invalid_repo_term_structure_is_rejected(text):
    result = parse_fut_command(text)
    assert result.kind == "invalid"
    assert result.message
