from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from buonaiuto_doc4llm.interaction_log import InteractionLogStore


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@pytest.fixture
def store(tmp_path: Path) -> InteractionLogStore:
    db = tmp_path / "state.db"
    conn = _connect(db)
    s = InteractionLogStore(connect=lambda: _connect(db))
    s.ensure_schema()
    conn.close()
    return s


def test_ensure_schema_creates_both_tables(tmp_path: Path, store: InteractionLogStore) -> None:
    with _connect(tmp_path / "state.db") as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r["name"] for r in rows}
    assert "mcp_sessions" in names
    assert "mcp_interactions" in names


def test_ensure_schema_is_idempotent(tmp_path: Path, store: InteractionLogStore) -> None:
    # Second call must not raise
    store.ensure_schema()
    store.ensure_schema()


from buonaiuto_doc4llm.interaction_log import sanitize_arguments


def test_sanitize_arguments_truncates_long_strings() -> None:
    big = "x" * 10_000
    out = sanitize_arguments({"query": big, "short": "ok"})
    assert out["short"] == "ok"
    assert out["query"].startswith("<truncated>")
    assert "10000 chars" in out["query"]


def test_sanitize_arguments_recurses_into_lists_and_dicts() -> None:
    big = "y" * 600
    out = sanitize_arguments({"nested": {"deep": [big, "fine"]}, "kept": 42})
    assert out["nested"]["deep"][0].startswith("<truncated>")
    assert out["nested"]["deep"][1] == "fine"
    assert out["kept"] == 42


def test_sanitize_arguments_short_strings_pass_through() -> None:
    out = sanitize_arguments({"a": "hello", "b": ["world", 1, None, True]})
    assert out == {"a": "hello", "b": ["world", 1, None, True]}


def test_sanitize_arguments_handles_non_dict_input() -> None:
    # Tool call might pass a list or a scalar; don't crash
    assert sanitize_arguments("x" * 10) == "x" * 10
    assert sanitize_arguments(["x" * 700])[0].startswith("<truncated>")


def test_sanitize_arguments_boundary_at_max_string_len() -> None:
    from buonaiuto_doc4llm.interaction_log import MAX_STRING_LEN

    at_limit = "a" * MAX_STRING_LEN
    over_limit = "a" * (MAX_STRING_LEN + 1)

    # exactly MAX_STRING_LEN chars → kept verbatim (rule is "longer than")
    assert sanitize_arguments(at_limit) == at_limit
    # one over → truncated
    assert sanitize_arguments(over_limit).startswith("<truncated>")


def test_record_and_query_session(tmp_path, store: InteractionLogStore) -> None:
    store.record_session(
        session_id="s-1",
        project_id="my-app",
        workspace_path="/tmp/my-app",
        client_name="claude-code",
        client_version="0.2.103",
    )
    rows = store.list_sessions()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s-1"
    assert rows[0]["project_id"] == "my-app"
    assert rows[0]["client_name"] == "claude-code"


def test_record_interaction_persists_row(tmp_path, store: InteractionLogStore) -> None:
    store.record_session(
        session_id="s-2", project_id="p", workspace_path="/tmp/p",
        client_name="cli", client_version="0.1",
    )
    store.record_interaction(
        session_id="s-2",
        project_id="p",
        tool_name="search_docs",
        arguments={"technology": "react", "query": "useState"},
        result_chars=2048,
        error=None,
        latency_ms=37,
    )
    rows = store.list_interactions(project_id="p")
    assert len(rows) == 1
    r = rows[0]
    assert r["tool_name"] == "search_docs"
    assert r["latency_ms"] == 37
    assert r["result_chars"] == 2048
    assert r["error"] is None
    assert json.loads(r["arguments_json"]) == {"technology": "react", "query": "useState"}


def test_record_interaction_truncates_long_argument_strings(store: InteractionLogStore) -> None:
    store.record_session(session_id="s-3", project_id="p", workspace_path=None,
                         client_name=None, client_version=None)
    big = "z" * 10_000
    store.record_interaction(
        session_id="s-3", project_id="p", tool_name="read_doc",
        arguments={"content": big}, result_chars=10, error=None, latency_ms=1,
    )
    rows = store.list_interactions(project_id="p")
    stored = json.loads(rows[0]["arguments_json"])
    assert stored["content"].startswith("<truncated>")


def test_record_interaction_swallows_sqlite_errors(store: InteractionLogStore, monkeypatch, capsys) -> None:
    def boom() -> sqlite3.Connection:
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(store, "_connect", boom)
    # Should not raise
    store.record_interaction(
        session_id="x", project_id=None, tool_name="t",
        arguments={}, result_chars=0, error=None, latency_ms=0,
    )
    assert "disk is full" in capsys.readouterr().err


def test_record_session_swallows_sqlite_errors(store: InteractionLogStore, monkeypatch, capsys) -> None:
    def boom() -> sqlite3.Connection:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(store, "_connect", boom)
    store.record_session(session_id="s", project_id=None, workspace_path=None,
                         client_name=None, client_version=None)
    assert "locked" in capsys.readouterr().err


def test_record_interaction_without_prior_session_creates_stub(store: InteractionLogStore) -> None:
    # No record_session() call — interaction must still persist AND a stub
    # session row must be created so listings stay coherent.
    store.record_interaction(
        session_id="orphan", project_id="p", tool_name="t",
        arguments={}, result_chars=0, error=None, latency_ms=1,
    )
    rows = store.list_interactions(project_id="p")
    assert len(rows) == 1
    sessions = store.list_sessions()
    assert any(s["session_id"] == "orphan" for s in sessions)


def test_record_interaction_preserves_existing_session_fields(store: InteractionLogStore) -> None:
    store.record_session(
        session_id="s", project_id="p", workspace_path="/tmp/p",
        client_name="claude-code", client_version="1.0",
    )
    store.record_interaction(
        session_id="s", project_id="p", tool_name="t",
        arguments={}, result_chars=0, error=None, latency_ms=1,
    )
    sessions = [s for s in store.list_sessions() if s["session_id"] == "s"]
    assert sessions[0]["client_name"] == "claude-code"
    assert sessions[0]["workspace_path"] == "/tmp/p"


def test_list_interactions_clamps_limit_and_offset(store: InteractionLogStore) -> None:
    # Seed 5 rows
    for i in range(5):
        store.record_interaction(
            session_id="s", project_id="p", tool_name=f"t{i}",
            arguments={}, result_chars=0, error=None, latency_ms=1,
        )
    # Negative offset is clamped to 0 → still returns all 5
    assert len(store.list_interactions(project_id="p", offset=-10)) == 5
    # Limit over 1000 is clamped to 1000 (only 5 rows exist, so we still get 5)
    assert len(store.list_interactions(project_id="p", limit=10_000_000)) == 5
    # Limit below 1 is clamped to 1
    assert len(store.list_interactions(project_id="p", limit=0)) == 1
