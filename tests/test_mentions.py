"""Tests for @mention resolution in bond lookups."""

from __future__ import annotations

import json

import pytest

from cheapquant_fi.agent.cli import _BARE_MENTION_RE, _BOND_RE, route_query
from cheapquant_fi.cli_tools import resolve_bond_mentions
from cheapquant_fi.config import DEFAULT_CONFIG_PATH, load_settings


@pytest.fixture
def app():
    return load_settings(DEFAULT_CONFIG_PATH)


def test_bare_mention_regex():
    """@id regex should match correctly."""
    match = _BARE_MENTION_RE.match("@fraapr029")
    assert match is not None
    assert match.group("id") == "fraapr029"

    match = _BARE_MENTION_RE.match("@usa10y001")
    assert match is not None
    assert match.group("id") == "usa10y001"

    # Should not match without @
    match = _BARE_MENTION_RE.match("fraapr029")
    assert match is None

    # Should not match with space after @
    match = _BARE_MENTION_RE.match("@ fraapr029")
    assert match is None


def test_bond_slash_regex():
    """./bond @id regex should work with and without @."""
    # Extended regex should accept @
    match = _BOND_RE.match("/bond @fraapr029")
    assert match is not None
    assert match.group("id") == "fraapr029"

    # Should also work without @
    match = _BOND_RE.match("/bond fraapr029")
    assert match is not None
    assert match.group("id") == "fraapr029"

    # Case insensitive
    match = _BOND_RE.match("/BOND @fraapr029")
    assert match is not None
    assert match.group("id") == "fraapr029"


def test_resolve_no_mentions():
    """Text with no mentions should pass through unchanged."""
    text = "what is the 10Y yield for USA"
    rewritten, unresolved = resolve_bond_mentions(text)

    assert len(unresolved) == 0
    assert rewritten == text
    assert "Context —" not in rewritten


def test_resolve_with_mock_bond(monkeypatch):
    """Test resolution with mocked bond data."""
    # Mock BondManager.get to return a fake bond
    from cheapquant_fi.bond_manager import BondManager
    from cheapquant_fi.instruments import Bond
    from datetime import date

    def mock_get(self, id, db_path=None):
        if id == "mockedbond123":
            return Bond(
                issuer="USA",
                maturity=date(2029, 4, 15),
                bond_id="US0001",
                user_friendly_id="mockedbond123",
                currency="USD",
                coupon=2.5,
            )
        return None

    monkeypatch.setattr(BondManager, "get", mock_get)

    text = "what is the maturity of @mockedbond123"
    rewritten, unresolved = resolve_bond_mentions(text)

    # Should not be unresolved
    assert len(unresolved) == 0

    # @ should be stripped from visible text
    assert "@mockedbond123" not in rewritten
    assert "mockedbond123" in rewritten

    # Should contain a context block with resolved bond data
    assert "Context — resolved bond mentions:" in rewritten

    # Context should be valid JSON
    lines = rewritten.split("\n")
    context_line = None
    for line in lines:
        if "Context — resolved bond mentions:" in line:
            context_line = line.replace("Context — resolved bond mentions: ", "")
            break

    assert context_line is not None
    context_data = json.loads(context_line)
    assert "mockedbond123" in context_data
    bond_info = context_data["mockedbond123"]
    assert bond_info["maturity"] == "2029-04-15"
    assert bond_info["issuer"] == "USA"


def test_resolve_multiple_mentions_with_mock(monkeypatch):
    """Test resolution of multiple mentions with mocked bonds."""
    from cheapquant_fi.bond_manager import BondManager
    from cheapquant_fi.instruments import Bond
    from datetime import date

    def mock_get(self, id, db_path=None):
        bonds = {
            "bond1": Bond(
                issuer="FRA",
                maturity=date(2031, 5, 15),
                bond_id="FR0001",
                user_friendly_id="bond1",
            ),
            "bond2": Bond(
                issuer="FRA",
                maturity=date(2032, 5, 15),
                bond_id="FR0002",
                user_friendly_id="bond2",
            ),
        }
        return bonds.get(id)

    monkeypatch.setattr(BondManager, "get", mock_get)

    text = "which bonds mature between @bond1 and @bond2"
    rewritten, unresolved = resolve_bond_mentions(text)

    # Both should be resolved
    assert len(unresolved) == 0
    assert "Context — resolved bond mentions:" in rewritten

    # Parse context
    context_data_str = rewritten.split("Context — resolved bond mentions: ")[1].split("\n")[0]
    context_data = json.loads(context_data_str)
    assert len(context_data) == 2
    assert "bond1" in context_data
    assert "bond2" in context_data


def test_unresolved_mention(monkeypatch):
    """An unresolvable @mention should be recorded."""
    from cheapquant_fi.bond_manager import BondManager

    def mock_get(self, id, db_path=None):
        return None

    monkeypatch.setattr(BondManager, "get", mock_get)

    text = "what is the bond @doesnotexist"
    rewritten, unresolved = resolve_bond_mentions(text)

    # Should be in unresolved list
    assert "doesnotexist" in unresolved

    # Should not have context block since nothing resolved
    assert "Context — resolved bond mentions:" not in rewritten

    # @mention should still be in text
    assert "@doesnotexist" in rewritten


def test_resolve_mixed_resolved_unresolved(monkeypatch):
    """Test with both resolved and unresolved mentions."""
    from cheapquant_fi.bond_manager import BondManager
    from cheapquant_fi.instruments import Bond
    from datetime import date

    def mock_get(self, id, db_path=None):
        if id == "realbond":
            return Bond(
                issuer="USA",
                maturity=date(2029, 4, 15),
                bond_id="US0001",
                user_friendly_id="realbond",
            )
        return None

    monkeypatch.setattr(BondManager, "get", mock_get)

    text = "compare @realbond with @fakebond"
    rewritten, unresolved = resolve_bond_mentions(text)

    # Only fakebond should be unresolved
    assert "fakebond" in unresolved
    assert "realbond" not in unresolved

    # Should have context for the resolved one
    assert "Context — resolved bond mentions:" in rewritten


def test_routing_with_mention(app):
    """Text with @mention should route to bond_analytics."""
    text = "what is the maturity of @fraapr029"
    rewritten, _ = resolve_bond_mentions(text)

    # The rewritten text should route to bond_analytics
    routed = route_query(app, rewritten)
    assert routed is not None
    assert routed.target == "bond_analytics"
