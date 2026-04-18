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


# ─── Dashboard integration tests (Task 14/15) ─────────────────────────────

from pathlib import Path
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from buonaiuto_doc4llm.dashboard import create_app

    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects" / "my-app.json").write_text(
        '{"project_id":"my-app","name":"my-app","technologies":[]}'
    )

    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        yield c, tmp_path


def _seed_interaction(tmp_path, pid: str, tool: str = "search_docs") -> None:
    from buonaiuto_doc4llm.service import DocsHubService
    svc = DocsHubService(tmp_path)
    svc.record_mcp_session(session_id=f"s-{tool}-{pid}", project_id=pid, workspace_path=None,
                           client_name="c", client_version="1")
    svc.record_mcp_interaction(session_id=f"s-{tool}-{pid}", project_id=pid, tool_name=tool,
                               arguments={"technology": "react", "query": "x"},
                               result_chars=100, error=None, latency_ms=9)


def test_projects_page_shows_last_used_for_active_project(client) -> None:
    c, tmp_path = client
    _seed_interaction(tmp_path, "my-app")
    resp = c.get("/projects")
    assert resp.status_code == 200
    body = resp.text
    assert "my-app" in body
    assert "calls / 30d" in body
    assert "View log" in body


def test_project_log_page_renders(client) -> None:
    c, tmp_path = client
    _seed_interaction(tmp_path, "my-app")
    resp = c.get("/projects/my-app/log")
    assert resp.status_code == 200
    body = resp.text
    assert "MCP interaction log" in body
    assert "search_docs" in body
    assert "<svg" in body  # inline chart present


def test_project_log_rows_filter_by_tool(client) -> None:
    c, tmp_path = client
    _seed_interaction(tmp_path, "my-app", tool="search_docs")
    _seed_interaction(tmp_path, "my-app", tool="read_doc")
    resp = c.get("/projects/my-app/log/rows?tool_name=search_docs")
    assert resp.status_code == 200
    assert "search_docs" in resp.text
    assert "read_doc" not in resp.text


def test_unattributed_sessions_card_visible(client) -> None:
    c, tmp_path = client
    from buonaiuto_doc4llm.service import DocsHubService
    svc = DocsHubService(tmp_path)
    svc.record_mcp_session(session_id="u", project_id=None, workspace_path=None,
                           client_name="c", client_version="1")
    svc.record_mcp_interaction(session_id="u", project_id=None, tool_name="t",
                               arguments={}, result_chars=0, error=None, latency_ms=1)
    resp = c.get("/projects")
    assert resp.status_code == 200
    assert "Unattributed sessions" in resp.text
