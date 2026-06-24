"""Tests for cqfi query planner extensions."""

from cheapquant_fi.agent.planner import CQFIRulePlanner, resolve_query_mode

TOOLS = ["list_tables", "get_schema", "run_sql", "describe_dataset"]


def test_list_tables_natural_language():
    planner = CQFIRulePlanner()
    calls = planner.plan("what tables exist in the input data", TOOLS)
    assert len(calls) == 1
    assert calls[0].name == "list_tables"


def test_sql_prefix_still_works():
    planner = CQFIRulePlanner()
    calls = planner.plan("sql: SELECT 1", TOOLS)
    assert calls[0].name == "run_sql"


def test_auto_llm_when_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    agent, single = resolve_query_mode(
        use_agent=False, use_single_shot=False, force_rule=False
    )
    assert not agent
    assert single


def test_force_rule_disables_auto_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    agent, single = resolve_query_mode(
        use_agent=False, use_single_shot=False, force_rule=True
    )
    assert not agent
    assert not single
