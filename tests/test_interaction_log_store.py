from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
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


def _insert_raw_interaction(store: InteractionLogStore, *, session_id: str,
                            project_id: str | None, tool_name: str,
                            created_at: datetime, error: str | None = None,
                            latency_ms: int = 10, result_chars: int = 100) -> None:
    """Bypass the Python helper so we can set created_at in the past."""
    with store._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mcp_sessions "
            "(session_id, project_id, workspace_path, client_name, client_version,"
            " started_at, last_seen_at) "
            "VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
            (session_id, project_id,
             created_at.isoformat(timespec="seconds"),
             created_at.isoformat(timespec="seconds")),
        )
        conn.execute(
            "INSERT INTO mcp_interactions "
            "(session_id, project_id, tool_name, arguments_json, result_chars,"
            " error, latency_ms, created_at) "
            "VALUES (?, ?, ?, '{}', ?, ?, ?, ?)",
            (session_id, project_id, tool_name, result_chars, error, latency_ms,
             created_at.isoformat(timespec="seconds")),
        )


def test_summary_aggregates(store: InteractionLogStore) -> None:
    now = datetime.now(timezone.utc)
    for i in range(5):
        _insert_raw_interaction(
            store, session_id="s", project_id="p",
            tool_name="search_docs", created_at=now - timedelta(minutes=i),
        )
    _insert_raw_interaction(
        store, session_id="s", project_id="p", tool_name="read_doc",
        created_at=now, error="boom",
    )
    s = store.get_summary("p", days=30)
    assert s["total_calls"] == 6
    assert s["unique_tools"] == 2
    tool_counts = {t["tool_name"]: t["count"] for t in s["tool_counts"]}
    assert tool_counts["search_docs"] == 5
    assert tool_counts["read_doc"] == 1
    assert s["error_rate"] == pytest.approx(1 / 6)
    assert len(s["calls_per_day"]) == 30


def test_summary_returns_zero_shape_for_inactive_project(store: InteractionLogStore) -> None:
    s = store.get_summary("nobody", days=30)
    assert s["total_calls"] == 0
    assert s["last_used_at"] is None
    assert s["tool_counts"] == []
    assert len(s["calls_per_day"]) == 30


def test_prune_deletes_old_interactions_and_orphan_sessions(store: InteractionLogStore) -> None:
    now = datetime.now(timezone.utc)
    _insert_raw_interaction(store, session_id="old", project_id="p",
                            tool_name="t", created_at=now - timedelta(days=45))
    _insert_raw_interaction(store, session_id="new", project_id="p",
                            tool_name="t", created_at=now - timedelta(days=1))
    result = store.prune(days=30)
    assert result["deleted_interactions"] >= 1
    assert result["deleted_sessions"] >= 1
    # New row survives
    assert len(store.list_interactions(project_id="p")) == 1


def test_list_unattributed_sessions(store: InteractionLogStore) -> None:
    now = datetime.now(timezone.utc)
    _insert_raw_interaction(store, session_id="u", project_id=None,
                            tool_name="t", created_at=now)
    rows = store.list_unattributed_sessions(days=30)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "u"


from buonaiuto_doc4llm.service import DocsHubService


def test_docshub_service_initialises_log_tables(tmp_path) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    svc = DocsHubService(tmp_path)
    with sqlite3.connect(svc.db_path) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "mcp_sessions" in names
    assert "mcp_interactions" in names


def test_docshub_service_delegates_record_and_summary(tmp_path) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    svc = DocsHubService(tmp_path)
    svc.record_mcp_session(
        session_id="s", project_id="p", workspace_path="/tmp/p",
        client_name="test", client_version="0.0.1",
    )
    svc.record_mcp_interaction(
        session_id="s", project_id="p", tool_name="search_docs",
        arguments={"q": "hello"}, result_chars=50, error=None, latency_ms=5,
    )
    summary = svc.get_project_interaction_summary("p", days=30)
    assert summary["total_calls"] == 1
    assert summary["unique_tools"] == 1


# ─── Hardening tests: bug-hunt fixes ──────────────────────────────────

def test_record_session_backfills_missing_client_info_via_coalesce(store: InteractionLogStore) -> None:
    # Stub session created by an interaction with NULL client fields
    store.record_interaction(
        session_id="sx", project_id=None, tool_name="t",
        arguments={}, result_chars=0, error=None, latency_ms=1,
    )
    # Later, the real initialize arrives with full metadata
    store.record_session(
        session_id="sx", project_id="pp", workspace_path="/tmp/pp",
        client_name="claude-code", client_version="0.5",
    )
    rows = [s for s in store.list_sessions() if s["session_id"] == "sx"]
    assert rows[0]["client_name"] == "claude-code"
    assert rows[0]["client_version"] == "0.5"
    assert rows[0]["workspace_path"] == "/tmp/pp"
    assert rows[0]["project_id"] == "pp"


def test_backfill_session_project_updates_sessions_and_interactions(store: InteractionLogStore) -> None:
    store.record_session(
        session_id="sy", project_id=None, workspace_path=None,
        client_name=None, client_version=None,
    )
    store.record_interaction(
        session_id="sy", project_id=None, tool_name="t",
        arguments={}, result_chars=0, error=None, latency_ms=1,
    )
    store.backfill_session_project("sy", "myapp")
    sessions = [s for s in store.list_sessions() if s["session_id"] == "sy"]
    assert sessions[0]["project_id"] == "myapp"
    rows = store.list_interactions(project_id="myapp")
    assert len(rows) == 1


def test_prune_uses_last_seen_at_not_started_at(store: InteractionLogStore) -> None:
    """A session started 45d ago but active yesterday must be retained."""
    with store._connect() as conn:
        # Session started 45d ago but with fresh last_seen_at
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(timespec="microseconds")
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="microseconds")
        conn.execute(
            "INSERT INTO mcp_sessions "
            "(session_id, project_id, workspace_path, client_name, client_version,"
            " started_at, last_seen_at) "
            "VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
            ("live", "p", old, recent),
        )
    store.prune(days=30)
    # Live session must survive
    assert any(s["session_id"] == "live" for s in store.list_sessions())


def test_list_sessions_all_vs_none_vs_filtered(store: InteractionLogStore) -> None:
    store.record_session(session_id="a", project_id="p1", workspace_path=None,
                         client_name=None, client_version=None)
    store.record_session(session_id="b", project_id=None, workspace_path=None,
                         client_name=None, client_version=None)
    # Default (no arg) returns both
    all_sessions = store.list_sessions()
    assert {s["session_id"] for s in all_sessions} == {"a", "b"}
    # Explicit None returns only unattributed
    unattr = store.list_sessions(project_id=None)
    assert {s["session_id"] for s in unattr} == {"b"}
    # Named filter
    named = store.list_sessions(project_id="p1")
    assert {s["session_id"] for s in named} == {"a"}


def test_sanitize_arguments_bounds_recursion_depth() -> None:
    # Build a deeply nested structure well past MAX_SANITIZE_DEPTH
    from buonaiuto_doc4llm.interaction_log import MAX_SANITIZE_DEPTH
    nested: Any = "leaf"
    for _ in range(MAX_SANITIZE_DEPTH + 10):
        nested = {"deeper": nested}
    # Should not raise RecursionError
    result = sanitize_arguments(nested)
    # The sentinel appears somewhere in the tree.
    import json as _json
    serialized = _json.dumps(result)
    assert "max-depth" in serialized


def test_sanitize_arguments_tuple_normalized_to_list() -> None:
    out = sanitize_arguments({"t": (1, 2, 3)})
    assert out == {"t": [1, 2, 3]}


def test_sanitize_arguments_handles_bytes() -> None:
    out = sanitize_arguments({"b": b"hello"})
    assert out == {"b": "hello"}
    out_big = sanitize_arguments({"b": b"x" * 10_000})
    assert out_big["b"].startswith("<truncated>")
