"""Tests for LangSmith tracing configuration."""

import os

from cheapquant_fi.config import configure_langsmith


def test_langsmith_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CQFI_LANGSMITH", raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    configure_langsmith()
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_langsmith_opt_in(monkeypatch):
    monkeypatch.setenv("CQFI_LANGSMITH", "1")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    configure_langsmith()
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
