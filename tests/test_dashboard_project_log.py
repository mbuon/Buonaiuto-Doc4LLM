from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from buonaiuto_doc4llm.dashboard._filters import (
    fromjson,
    humanize_timedelta,
    mcp_args_summary,
    truncate_chars,
)


def test_mcp_args_summary_search_docs() -> None:
    assert mcp_args_summary("search_docs",
                            {"technology": "react", "query": "useState"}) == 'react, "useState"'


def test_mcp_args_summary_read_doc() -> None:
    assert mcp_args_summary("read_doc",
                            {"technology": "stripe", "rel_path": "charges.md"}) == "stripe/charges.md"


def test_mcp_args_summary_list_project_updates() -> None:
    assert mcp_args_summary("list_project_updates", {"project_id": "x"}) == "x"


def test_mcp_args_summary_fetch_docs_all() -> None:
    assert mcp_args_summary("fetch_docs", {}) == "all"
    assert mcp_args_summary("fetch_docs", {"technology": "react"}) == "react"


def test_mcp_args_summary_unknown_tool_shows_first_two_kv() -> None:
    out = mcp_args_summary("mystery_tool", {"a": 1, "b": "two", "c": 3})
    assert "a=1" in out and "b=two" in out


def test_humanize_timedelta() -> None:
    now = datetime.now(timezone.utc)
    assert humanize_timedelta(now) == "just now"
    assert humanize_timedelta(now - timedelta(minutes=5)) == "5m ago"
    assert humanize_timedelta(now - timedelta(hours=2)) == "2h ago"
    assert humanize_timedelta(now - timedelta(days=3)) == "3d ago"
    assert humanize_timedelta(None) == "never"


def test_truncate_chars() -> None:
    assert truncate_chars("hello", 10) == "hello"
    assert truncate_chars("hello world", 5) == "hello…"


def test_fromjson_handles_none_and_invalid() -> None:
    assert fromjson(None) == {}
    assert fromjson("") == {}
    assert fromjson("{not json") == {}
    assert fromjson('{"a": 1}') == {"a": 1}
