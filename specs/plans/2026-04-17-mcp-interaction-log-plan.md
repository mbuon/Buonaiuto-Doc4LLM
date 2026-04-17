# MCP Interaction Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-project MCP interaction log viewable from the web dashboard, auto-install projects on first MCP connection (with a 24h freshness gate), and run a 3-day cron that refreshes docs for every active project.

**Architecture:** A new `InteractionLogStore` module owns the `mcp_sessions` + `mcp_interactions` tables in the existing SQLite DB. A thin adapter on `DocsHubService` delegates to it so existing call-sites stay unchanged. `MCPServer` gains a session-pinning pattern: `initialize` resolves the workspace basename to a `project_id` (auto-installing on a background thread when needed), pins `self._session_id` / `self._session_project_id` to the instance, and every `_call_tool` invocation is wrapped to record latency/errors/result-size. A new CLI command `refresh-active` plus a second cron entry in `scheduler.py` handle the 3-day refresh. The dashboard gains `/projects/<id>/log` with summary header + inline SVG chart + HTMX-filtered table.

**Tech Stack:** Python 3.11+, SQLite (WAL), FastAPI + Jinja2 + HTMX, pytest with `tmp_path`, threading stdlib for the async install, `urllib.parse` for URI parsing. No new third-party dependencies.

**Reference spec:** `specs/2026-04-17-mcp-interaction-log-design.md`

---

## File plan

### New files

| Path | Purpose | Est. lines |
|---|---|---|
| `src/buonaiuto_doc4llm/interaction_log.py` | `InteractionLogStore` (schema, writes, reads, pruning), `sanitize_arguments()`, sanitise constants | ~330 |
| `src/buonaiuto_doc4llm/project_bootstrap.py` | `ensure_project_installed()`, the 24h freshness gate, the in-flight set, background-thread dispatch | ~180 |
| `src/buonaiuto_doc4llm/refresh_active.py` | `list_active_projects()`, `refresh_active_projects()` (the 3-day cron entry) | ~200 |
| `src/buonaiuto_doc4llm/dashboard/_filters.py` | Jinja filters: `mcp_args_summary`, `humanize_timedelta`, `truncate_chars` | ~80 |
| `src/buonaiuto_doc4llm/dashboard/templates/project_log.html` | Per-project log page | ~150 |
| `src/buonaiuto_doc4llm/dashboard/templates/_project_log_rows.html` | HTMX partial for paginated/filtered rows | ~40 |
| `tests/test_interaction_log_store.py` | Schema, record, query, prune, truncation | ~400 |
| `tests/test_mcp_auto_install.py` | Bootstrap, freshness gate, background thread, error-safety | ~300 |
| `tests/test_refresh_active_projects.py` | Active-project detection, dedup, per-project failures | ~280 |
| `tests/test_dashboard_project_log.py` | Dashboard summary bullets, log page render, HTMX filter | ~180 |

### Modified files

| Path | Change |
|---|---|
| `src/buonaiuto_doc4llm/service.py` | Call `InteractionLogStore.ensure_schema()` in `_init_db`; add 6 thin delegate methods (`record_mcp_session`, `record_mcp_interaction`, `prune_mcp_interactions`, `get_project_interaction_summary`, `list_project_interactions`, `list_unattributed_sessions`); pass `workspace_path` into `install_project` persistence step. |
| `src/buonaiuto_doc4llm/mcp_server.py` | Replace `_bootstrap_from_initialize_params` with `project_bootstrap.ensure_project_installed`; add `self._session_id` + `self._session_project_id` initialisation in `initialize`; wrap `_call_tool` with `_record_tool_invocation`. |
| `src/buonaiuto_doc4llm/__main__.py` | New `refresh-active` subcommand calling `refresh_active.refresh_active_projects()`. |
| `src/buonaiuto_doc4llm/scheduler.py` | Second crontab/launchd entry for `refresh-active` every 3 days at 04:15. |
| `src/buonaiuto_doc4llm/dashboard/routes.py` | Register `_filters.py` filters; extend `_get_projects_with_unread` with summary fields; add `GET /projects/{project_id}/log` + `GET /projects/{project_id}/log/rows`; add `GET /projects/unattributed/log`. |
| `src/buonaiuto_doc4llm/dashboard/templates/projects.html` | Add "View Log" button + last-used/call-count summary per project card; add "Unattributed sessions" card at the bottom. |

### Task order rationale

Tasks go inside-out, unit-test first at every layer: store → workspace resolution → bootstrap → MCP integration → CLI → cron → dashboard backend → dashboard UI → README.

---

## Task 1: Add `InteractionLogStore` with schema + two no-op writer methods

**Files:**
- Create: `src/buonaiuto_doc4llm/interaction_log.py`
- Test: `tests/test_interaction_log_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interaction_log_store.py
from __future__ import annotations

import sqlite3
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction_log_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'buonaiuto_doc4llm.interaction_log'`

- [ ] **Step 3: Create the module with schema**

```python
# src/buonaiuto_doc4llm/interaction_log.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

# Public sentinel for truncated strings
MAX_STRING_LEN = 500
TRUNCATION_TEMPLATE = "<truncated>…[{n} chars]"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mcp_sessions (
    session_id      TEXT PRIMARY KEY,
    project_id      TEXT,
    workspace_path  TEXT,
    client_name     TEXT,
    client_version  TEXT,
    started_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_sessions_project
    ON mcp_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_mcp_sessions_started_at
    ON mcp_sessions(started_at);

CREATE TABLE IF NOT EXISTS mcp_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    project_id      TEXT,
    tool_name       TEXT NOT NULL,
    arguments_json  TEXT NOT NULL,
    result_chars    INTEGER,
    error           TEXT,
    latency_ms      INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_interactions_project_created
    ON mcp_interactions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_interactions_session
    ON mcp_interactions(session_id);
CREATE INDEX IF NOT EXISTS idx_mcp_interactions_created
    ON mcp_interactions(created_at);
"""


class InteractionLogStore:
    def __init__(self, connect: Callable[[], sqlite3.Connection]):
        self._connect = connect

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction_log_store.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/interaction_log.py tests/test_interaction_log_store.py
git commit -m "Add InteractionLogStore with mcp_sessions + mcp_interactions schema"
```

---

## Task 2: Argument sanitisation for long strings

**Files:**
- Modify: `src/buonaiuto_doc4llm/interaction_log.py`
- Test: `tests/test_interaction_log_store.py`

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_interaction_log_store.py
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
```

- [ ] **Step 2: Run to confirm fail**

Run: `pytest tests/test_interaction_log_store.py -v -k sanitize`
Expected: FAIL — `ImportError: cannot import name 'sanitize_arguments'`

- [ ] **Step 3: Implement**

```python
# Append to src/buonaiuto_doc4llm/interaction_log.py
def sanitize_arguments(value: Any) -> Any:
    """Recursively truncate overlong string fields before persistence.

    Strings longer than MAX_STRING_LEN are replaced with a short sentinel
    that preserves the original length so the log stays legible but small.
    """
    if isinstance(value, str):
        if len(value) > MAX_STRING_LEN:
            return TRUNCATION_TEMPLATE.format(n=len(value))
        return value
    if isinstance(value, dict):
        return {k: sanitize_arguments(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_arguments(v) for v in value]
    if isinstance(value, tuple):
        return tuple(sanitize_arguments(v) for v in value)
    return value
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_interaction_log_store.py -v -k sanitize`
Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/interaction_log.py tests/test_interaction_log_store.py
git commit -m "Add sanitize_arguments: truncate strings >500 chars recursively"
```

---

## Task 3: `record_mcp_session` + `record_mcp_interaction`

**Files:**
- Modify: `src/buonaiuto_doc4llm/interaction_log.py`
- Test: `tests/test_interaction_log_store.py`

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_interaction_log_store.py
import json
import sys


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
    # Force _connect to raise; must not propagate
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
```

- [ ] **Step 2: Run to confirm fail**

Run: `pytest tests/test_interaction_log_store.py -v -k "record"`
Expected: FAIL — methods not defined.

- [ ] **Step 3: Implement**

```python
# Append to src/buonaiuto_doc4llm/interaction_log.py


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InteractionLogStore(InteractionLogStore):  # noqa: F811 — extending same class
    pass


# Replace the prior class definition: add these methods inside the original
# InteractionLogStore class. Engineers implementing: merge into the class
# body from Task 1. We show them here as stand-alone for clarity.


def record_session(self, *, session_id: str, project_id: str | None,
                   workspace_path: str | None, client_name: str | None,
                   client_version: str | None) -> None:
    now = _now_iso()
    try:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_sessions
                    (session_id, project_id, workspace_path, client_name,
                     client_version, started_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at
                """,
                (session_id, project_id, workspace_path, client_name,
                 client_version, now, now),
            )
    except sqlite3.OperationalError as exc:
        print(f"[interaction_log] record_session failed: {exc}", file=sys.stderr)


def record_interaction(self, *, session_id: str, project_id: str | None,
                       tool_name: str, arguments: Any,
                       result_chars: int | None, error: str | None,
                       latency_ms: int) -> None:
    clean = sanitize_arguments(arguments)
    payload = json.dumps(clean, default=str, ensure_ascii=False)
    now = _now_iso()
    try:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_interactions
                    (session_id, project_id, tool_name, arguments_json,
                     result_chars, error, latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, project_id, tool_name, payload,
                 result_chars, error, latency_ms, now),
            )
            conn.execute(
                "UPDATE mcp_sessions SET last_seen_at=? WHERE session_id=?",
                (now, session_id),
            )
    except sqlite3.OperationalError as exc:
        print(f"[interaction_log] record_interaction failed: {exc}",
              file=sys.stderr)


def list_sessions(self, *, project_id: str | None = "__ALL__") -> list[dict[str, Any]]:
    with self._connect() as conn:
        if project_id == "__ALL__":
            rows = conn.execute(
                "SELECT * FROM mcp_sessions ORDER BY started_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mcp_sessions WHERE project_id IS ? ORDER BY started_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def list_interactions(self, *, project_id: str | None, limit: int = 100,
                      offset: int = 0, tool_name: str | None = None,
                      since: str | None = None,
                      errors_only: bool = False) -> list[dict[str, Any]]:
    sql = ["SELECT * FROM mcp_interactions WHERE 1=1"]
    args: list[Any] = []
    if project_id is None:
        sql.append("AND project_id IS NULL")
    else:
        sql.append("AND project_id = ?")
        args.append(project_id)
    if tool_name:
        sql.append("AND tool_name = ?")
        args.append(tool_name)
    if since:
        sql.append("AND created_at >= ?")
        args.append(since)
    if errors_only:
        sql.append("AND error IS NOT NULL")
    sql.append("ORDER BY id DESC LIMIT ? OFFSET ?")
    args.extend([limit, offset])
    with self._connect() as conn:
        rows = conn.execute(" ".join(sql), args).fetchall()
        return [dict(r) for r in rows]
```

Integrate these as methods on the existing `InteractionLogStore` class from Task 1 (do not keep the second `class InteractionLogStore` stub — that was illustrative only).

- [ ] **Step 4: Run**

Run: `pytest tests/test_interaction_log_store.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/interaction_log.py tests/test_interaction_log_store.py
git commit -m "InteractionLogStore: record sessions/interactions, list, swallow DB errors"
```

---

## Task 4: Summary, pruning, and unattributed sessions

**Files:**
- Modify: `src/buonaiuto_doc4llm/interaction_log.py`
- Test: `tests/test_interaction_log_store.py`

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_interaction_log_store.py
from datetime import datetime, timedelta, timezone


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
```

- [ ] **Step 2: Run to confirm fail**

Run: `pytest tests/test_interaction_log_store.py -v -k "summary or prune or unattributed"`
Expected: FAIL — methods missing.

- [ ] **Step 3: Implement**

Add these methods to the `InteractionLogStore` class:

```python
def get_summary(self, project_id: str | None, *, days: int = 30) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    pid_sql = "project_id IS NULL" if project_id is None else "project_id = ?"
    pid_args: tuple[Any, ...] = () if project_id is None else (project_id,)
    with self._connect() as conn:
        totals = conn.execute(
            f"SELECT COUNT(*) AS n, MAX(created_at) AS last_used "
            f"FROM mcp_interactions WHERE {pid_sql} AND created_at >= ?",
            (*pid_args, since),
        ).fetchone()
        total_calls = totals["n"] or 0
        last_used_at = totals["last_used"]

        error_count = conn.execute(
            f"SELECT COUNT(*) AS n FROM mcp_interactions "
            f"WHERE {pid_sql} AND created_at >= ? AND error IS NOT NULL",
            (*pid_args, since),
        ).fetchone()["n"] or 0

        tool_counts = [
            dict(r) for r in conn.execute(
                f"SELECT tool_name, COUNT(*) AS count FROM mcp_interactions "
                f"WHERE {pid_sql} AND created_at >= ? "
                f"GROUP BY tool_name ORDER BY count DESC",
                (*pid_args, since),
            ).fetchall()
        ]

        per_day_rows = {
            r["day"]: r["count"] for r in conn.execute(
                f"SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count "
                f"FROM mcp_interactions WHERE {pid_sql} AND created_at >= ? "
                f"GROUP BY day ORDER BY day",
                (*pid_args, since),
            ).fetchall()
        }

        session_count = conn.execute(
            f"SELECT COUNT(DISTINCT session_id) AS n FROM mcp_interactions "
            f"WHERE {pid_sql} AND created_at >= ?",
            (*pid_args, since),
        ).fetchone()["n"] or 0

        client_breakdown = [
            dict(r) for r in conn.execute(
                f"""
                SELECT s.client_name, s.client_version, COUNT(*) AS count
                  FROM mcp_interactions i
                  JOIN mcp_sessions s ON s.session_id = i.session_id
                 WHERE (i.{pid_sql}) AND i.created_at >= ?
                 GROUP BY s.client_name, s.client_version
                 ORDER BY count DESC
                """.replace("(i.project_id IS NULL)", "i.project_id IS NULL"),
                (*pid_args, since),
            ).fetchall()
        ]

    calls_per_day: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    for offset in range(days - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        calls_per_day.append({"day": d, "count": per_day_rows.get(d, 0)})

    return {
        "project_id": project_id,
        "last_used_at": last_used_at,
        "total_calls": total_calls,
        "total_sessions": session_count,
        "window_days": days,
        "unique_tools": len(tool_counts),
        "calls_per_day": calls_per_day,
        "tool_counts": tool_counts,
        "client_breakdown": client_breakdown,
        "error_rate": (error_count / total_calls) if total_calls else 0.0,
    }


def prune(self, *, days: int = 30) -> dict[str, int]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        with self._connect() as conn:
            deleted_i = conn.execute(
                "DELETE FROM mcp_interactions WHERE created_at < ?",
                (cutoff,),
            ).rowcount
            deleted_s = conn.execute(
                "DELETE FROM mcp_sessions "
                "WHERE started_at < ? AND session_id NOT IN "
                "(SELECT DISTINCT session_id FROM mcp_interactions)",
                (cutoff,),
            ).rowcount
            return {"deleted_interactions": deleted_i or 0,
                    "deleted_sessions": deleted_s or 0}
    except sqlite3.OperationalError as exc:
        print(f"[interaction_log] prune failed: {exc}", file=sys.stderr)
        return {"deleted_interactions": 0, "deleted_sessions": 0}


def list_unattributed_sessions(self, *, days: int = 30) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with self._connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, COUNT(i.id) AS call_count, MAX(i.created_at) AS last_used_at
              FROM mcp_sessions s
              LEFT JOIN mcp_interactions i ON i.session_id = s.session_id
             WHERE s.project_id IS NULL AND s.started_at >= ?
             GROUP BY s.session_id
             ORDER BY s.started_at DESC
            """,
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_interaction_log_store.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/interaction_log.py tests/test_interaction_log_store.py
git commit -m "InteractionLogStore: summary aggregation, 30d prune, unattributed listing"
```

---

## Task 5: Wire `InteractionLogStore` into `DocsHubService`

**Files:**
- Modify: `src/buonaiuto_doc4llm/service.py` (constructor, `_init_db`, add 6 delegate methods)
- Test: `tests/test_interaction_log_store.py` (one integration test)

- [ ] **Step 1: Add failing integration test**

```python
# Append to tests/test_interaction_log_store.py
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
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_interaction_log_store.py::test_docshub_service_initialises_log_tables tests/test_interaction_log_store.py::test_docshub_service_delegates_record_and_summary -v`
Expected: FAIL — methods don't exist on `DocsHubService`.

- [ ] **Step 3: Wire the store into `DocsHubService`**

In `src/buonaiuto_doc4llm/service.py`:

After existing imports near the top, add:

```python
from buonaiuto_doc4llm.interaction_log import InteractionLogStore
```

In `DocsHubService.__init__` (around line 397, after `self.db_path = ...`), before the existing `self._init_db()` call, add:

```python
        self.interaction_log = InteractionLogStore(connect=self._connect)
```

In `_init_db` at the end of the `executescript` block (after the existing `CREATE TABLE IF NOT EXISTS observed_packages` section), add:

```python
        self.interaction_log.ensure_schema()
```

At the end of the `DocsHubService` class, add six delegate methods:

```python
    def record_mcp_session(self, *, session_id: str, project_id: str | None,
                           workspace_path: str | None, client_name: str | None,
                           client_version: str | None) -> None:
        self.interaction_log.record_session(
            session_id=session_id, project_id=project_id,
            workspace_path=workspace_path, client_name=client_name,
            client_version=client_version,
        )

    def record_mcp_interaction(self, *, session_id: str, project_id: str | None,
                               tool_name: str, arguments: Any,
                               result_chars: int | None, error: str | None,
                               latency_ms: int) -> None:
        self.interaction_log.record_interaction(
            session_id=session_id, project_id=project_id,
            tool_name=tool_name, arguments=arguments,
            result_chars=result_chars, error=error, latency_ms=latency_ms,
        )

    def prune_mcp_interactions(self, *, days: int = 30) -> dict[str, int]:
        return self.interaction_log.prune(days=days)

    def get_project_interaction_summary(self, project_id: str | None,
                                        days: int = 30) -> dict[str, Any]:
        return self.interaction_log.get_summary(project_id, days=days)

    def list_project_interactions(self, project_id: str | None, *,
                                  limit: int = 100, offset: int = 0,
                                  tool_name: str | None = None,
                                  since: str | None = None,
                                  errors_only: bool = False) -> list[dict[str, Any]]:
        return self.interaction_log.list_interactions(
            project_id=project_id, limit=limit, offset=offset,
            tool_name=tool_name, since=since, errors_only=errors_only,
        )

    def list_unattributed_mcp_sessions(self, *, days: int = 30) -> list[dict[str, Any]]:
        return self.interaction_log.list_unattributed_sessions(days=days)
```

Also add the prune call inside `DocsHubService.scan()` (find the method; it returns the scan summary). Immediately before the existing `return summary` line, add:

```python
        self.interaction_log.prune(days=30)
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_interaction_log_store.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full suite to make sure nothing broke**

Run: `pytest -q`
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/buonaiuto_doc4llm/service.py tests/test_interaction_log_store.py
git commit -m "Wire InteractionLogStore into DocsHubService; prune on scan()"
```

---

## Task 6: Workspace resolution utility

**Files:**
- Create: `src/buonaiuto_doc4llm/project_bootstrap.py`
- Test: `tests/test_mcp_auto_install.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_mcp_auto_install.py
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from buonaiuto_doc4llm.project_bootstrap import (
    extract_workspace_path,
    resolve_project_id_for_basename,
)


def test_extract_workspace_path_from_root_uri() -> None:
    assert extract_workspace_path({"rootUri": "file:///tmp/my-app"}) == Path("/tmp/my-app")


def test_extract_workspace_path_from_workspace_folders() -> None:
    p = extract_workspace_path({
        "workspaceFolders": [{"uri": "file:///tmp/first", "name": "first"}],
    })
    assert p == Path("/tmp/first")


def test_extract_workspace_path_none_when_missing() -> None:
    assert extract_workspace_path({}) is None
    assert extract_workspace_path({"rootUri": "http://not-a-file"}) is None


def test_resolve_project_id_for_basename_matches_existing(tmp_path: Path) -> None:
    projects_root = tmp_path / "docs_center" / "projects"
    projects_root.mkdir(parents=True)
    (projects_root / "my-app.json").write_text(json.dumps({
        "project_id": "my-app", "name": "My App", "technologies": [],
    }))
    pid = resolve_project_id_for_basename(projects_root, "my-app")
    assert pid == "my-app"


def test_resolve_project_id_is_case_insensitive(tmp_path: Path) -> None:
    projects_root = tmp_path / "docs_center" / "projects"
    projects_root.mkdir(parents=True)
    (projects_root / "FooBar.json").write_text(json.dumps({
        "project_id": "FooBar", "name": "Foo", "technologies": [],
    }))
    assert resolve_project_id_for_basename(projects_root, "foobar") == "FooBar"


def test_resolve_project_id_returns_none_on_no_match(tmp_path: Path) -> None:
    projects_root = tmp_path / "docs_center" / "projects"
    projects_root.mkdir(parents=True)
    assert resolve_project_id_for_basename(projects_root, "nope") is None
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_mcp_auto_install.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/buonaiuto_doc4llm/project_bootstrap.py
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from buonaiuto_doc4llm.service import DocsHubService


FRESHNESS_SECONDS = 24 * 60 * 60  # 24h

# Module-level in-flight set of workspace paths currently being installed
_install_in_flight: set[str] = set()
_install_in_flight_lock = threading.Lock()


def extract_workspace_path(params: dict[str, Any]) -> Path | None:
    direct = params.get("project_path") or params.get("projectPath")
    if isinstance(direct, str) and direct.strip():
        return Path(direct.strip())

    folders = params.get("workspaceFolders")
    if isinstance(folders, list):
        for folder in folders:
            if isinstance(folder, dict):
                uri = folder.get("uri")
                if isinstance(uri, str):
                    p = _path_from_uri(uri)
                    if p is not None:
                        return p

    root_uri = params.get("rootUri")
    if isinstance(root_uri, str):
        return _path_from_uri(root_uri)

    return None


def _path_from_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


def resolve_project_id_for_basename(projects_root: Path, basename: str) -> str | None:
    """Return the project_id whose project-file basename matches (case-insensitive)."""
    if not projects_root.exists():
        return None
    target = basename.lower()
    for path in projects_root.glob("*.json"):
        if path.stem.lower() == target:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            pid = data.get("project_id")
            return pid if isinstance(pid, str) and pid else path.stem
    return None
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_mcp_auto_install.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/project_bootstrap.py tests/test_mcp_auto_install.py
git commit -m "Add project_bootstrap: workspace path extraction + basename resolution"
```

---

## Task 7: `ensure_project_installed` with 24h freshness gate and background dispatch

**Files:**
- Modify: `src/buonaiuto_doc4llm/project_bootstrap.py`
- Test: `tests/test_mcp_auto_install.py`

- [ ] **Step 1: Failing tests**

```python
# Append to tests/test_mcp_auto_install.py
from buonaiuto_doc4llm.service import DocsHubService
from buonaiuto_doc4llm.project_bootstrap import (
    ensure_project_installed,
    _install_in_flight,
)


@pytest.fixture
def svc(tmp_path: Path) -> DocsHubService:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    return DocsHubService(tmp_path)


def _make_project_file(svc: DocsHubService, pid: str, mtime_offset_seconds: int = 0) -> Path:
    path = svc.projects_root / f"{pid}.json"
    path.write_text(json.dumps({"project_id": pid, "name": pid, "technologies": []}))
    if mtime_offset_seconds:
        new = time.time() + mtime_offset_seconds
        os.utime(path, (new, new))
    return path


def test_ensure_project_installed_fresh_file_reused(svc: DocsHubService, monkeypatch) -> None:
    _make_project_file(svc, "my-app", mtime_offset_seconds=-60)  # 1 min old
    called = []
    monkeypatch.setattr(
        svc, "install_project",
        lambda **kw: called.append(kw) or {"project_id": "my-app"},
    )
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/my-app"), wait=True)
    assert pid == "my-app"
    assert called == []  # install NOT called


def test_ensure_project_installed_stale_file_triggers_install(svc, monkeypatch) -> None:
    _make_project_file(svc, "my-app", mtime_offset_seconds=-(30 * 3600))  # 30h old
    called = []
    monkeypatch.setattr(
        svc, "install_project",
        lambda **kw: called.append(kw) or {"project_id": "my-app"},
    )
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/my-app"), wait=True)
    assert pid == "my-app"
    assert len(called) == 1


def test_ensure_project_installed_first_time_installs(svc, monkeypatch) -> None:
    called = []

    def fake_install(**kw):
        called.append(kw)
        return {"project_id": "brand-new"}

    monkeypatch.setattr(svc, "install_project", fake_install)
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/brand-new"), wait=True)
    assert pid == "brand-new"
    assert called[0]["project_root"] == Path("/tmp/brand-new")


def test_ensure_project_installed_runs_in_background(svc, monkeypatch) -> None:
    started = threading.Event()
    finish = threading.Event()

    def slow_install(**kw):
        started.set()
        finish.wait(timeout=5)
        return {"project_id": "slow"}

    monkeypatch.setattr(svc, "install_project", slow_install)
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/slow"), wait=False)
    assert pid == "slow"  # predicted from basename immediately
    assert started.wait(timeout=2), "install thread should have started"
    # Install still running — verify non-blocking
    finish.set()


def test_ensure_project_installed_deduplicates_concurrent_calls(svc, monkeypatch) -> None:
    count = [0]
    gate = threading.Event()

    def slow_install(**kw):
        count[0] += 1
        gate.wait(timeout=5)
        return {"project_id": "dup"}

    monkeypatch.setattr(svc, "install_project", slow_install)

    # First call kicks off install
    pid1 = ensure_project_installed(svc, workspace_path=Path("/tmp/dup"), wait=False)
    # Second call while first is still running must not double-dispatch
    pid2 = ensure_project_installed(svc, workspace_path=Path("/tmp/dup"), wait=False)
    assert pid1 == pid2 == "dup"
    gate.set()
    # Wait for background threads to drain
    time.sleep(0.2)
    assert count[0] == 1


def test_ensure_project_installed_install_failure_logs_and_returns_none(svc, monkeypatch, capsys) -> None:
    def boom(**kw):
        raise RuntimeError("no network")

    monkeypatch.setattr(svc, "install_project", boom)
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/fail"), wait=True)
    # We still predict "fail" from basename before the install runs,
    # but on failure the function returns None (fail-safe).
    assert pid is None
    assert "no network" in capsys.readouterr().err


def test_ensure_project_installed_none_when_no_path(svc) -> None:
    assert ensure_project_installed(svc, workspace_path=None, wait=True) is None


def test_ensure_project_installed_clears_in_flight_on_success(svc, monkeypatch) -> None:
    monkeypatch.setattr(svc, "install_project", lambda **kw: {"project_id": "cleanup"})
    ensure_project_installed(svc, workspace_path=Path("/tmp/cleanup"), wait=True)
    assert "/tmp/cleanup" not in _install_in_flight
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_mcp_auto_install.py -v`
Expected: FAIL — `ensure_project_installed` undefined.

- [ ] **Step 3: Implement**

Append to `src/buonaiuto_doc4llm/project_bootstrap.py`:

```python
def _is_fresh(path: Path, max_age_seconds: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < max_age_seconds
    except OSError:
        return False


def ensure_project_installed(
    service: "DocsHubService",
    *,
    workspace_path: Path | None,
    wait: bool = False,
    freshness_seconds: int = FRESHNESS_SECONDS,
) -> str | None:
    """Resolve a workspace path to a project_id, auto-installing when needed.

    Returns the predicted project_id immediately (for session pinning).
    Install is dispatched to a background thread unless wait=True.
    Returns None if workspace_path is missing OR (when wait=True) if install raised.
    """
    if workspace_path is None:
        return None

    basename = workspace_path.name
    if not basename:
        return None

    project_file = service.projects_root / f"{basename}.json"

    # Fast path: file exists and is fresh → reuse as-is
    if project_file.exists() and _is_fresh(project_file, freshness_seconds):
        existing = resolve_project_id_for_basename(service.projects_root, basename)
        return existing or basename

    key = str(workspace_path)
    with _install_in_flight_lock:
        if key in _install_in_flight:
            return basename  # another thread is already on it
        _install_in_flight.add(key)

    error_holder: dict[str, Exception] = {}

    def _run() -> None:
        try:
            service.install_project(
                project_root=workspace_path,
                project_id=basename,
            )
        except Exception as exc:  # noqa: BLE001 — fail-safe
            error_holder["exc"] = exc
            print(
                f"[project_bootstrap] install_project failed for {workspace_path}: {exc}",
                file=sys.stderr,
            )
        finally:
            with _install_in_flight_lock:
                _install_in_flight.discard(key)

    if wait:
        _run()
        return None if "exc" in error_holder else basename

    threading.Thread(target=_run, daemon=True, name=f"install-{basename}").start()
    return basename
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_mcp_auto_install.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/project_bootstrap.py tests/test_mcp_auto_install.py
git commit -m "Add ensure_project_installed: 24h freshness gate + background install"
```

---

## Task 8: Persist `workspace_path` in project files

**Files:**
- Modify: `src/buonaiuto_doc4llm/service.py` (find `install_project` at line 1593)
- Test: `tests/test_mcp_auto_install.py`

- [ ] **Step 1: Failing test**

```python
# Append to tests/test_mcp_auto_install.py
def test_install_project_persists_workspace_path(tmp_path: Path) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    svc = DocsHubService(tmp_path)

    # Minimal fake project folder
    project_dir = tmp_path / "fake-project"
    project_dir.mkdir()
    (project_dir / "package.json").write_text('{"name":"fake","dependencies":{"react":"^18"}}')

    result = svc.install_project(project_root=project_dir, project_id="fake-project")
    assert result["project_id"] == "fake-project"

    pf = tmp_path / "docs_center" / "projects" / "fake-project.json"
    data = json.loads(pf.read_text())
    assert data["workspace_path"] == str(project_dir.resolve())
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_mcp_auto_install.py::test_install_project_persists_workspace_path -v`
Expected: FAIL — `workspace_path` key not present.

- [ ] **Step 3: Inspect the project-file writer in `install_project`**

Find the block in `src/buonaiuto_doc4llm/service.py` that writes `docs_center/projects/<id>.json`. It is inside `install_project` (around line 1593–1900 range). Look for `json.dump(` or `.write_text(json.dumps(`.

- [ ] **Step 4: Add the `workspace_path` field to the persisted JSON**

In the dict that gets written to the project file, add:

```python
"workspace_path": str(Path(project_root).resolve()),
```

Ensure `project_root` is already a local variable in that function (it is — it's the first parameter).

- [ ] **Step 5: Run**

Run: `pytest tests/test_mcp_auto_install.py::test_install_project_persists_workspace_path -v`
Expected: PASS.

Run: `pytest -q`
Expected: no regressions. Any existing test that asserted the exact JSON shape of a project file may need to tolerate the new key — update any such assertion to use `>=` semantics (e.g. `assert data["project_id"] == "foo"` instead of `assert data == {...}`).

- [ ] **Step 6: Commit**

```bash
git add src/buonaiuto_doc4llm/service.py tests/test_mcp_auto_install.py
git commit -m "Persist workspace_path in docs_center/projects/<id>.json"
```

---

## Task 9: Session pinning + tool-call wrapper in `MCPServer`

**Files:**
- Modify: `src/buonaiuto_doc4llm/mcp_server.py`
- Test: extend `tests/test_mcp_auto_install.py`

- [ ] **Step 1: Failing tests**

```python
# Append to tests/test_mcp_auto_install.py
from buonaiuto_doc4llm.mcp_server import MCPServer


def test_mcp_server_pins_session_on_initialize(tmp_path, monkeypatch) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    # Pre-create a fresh project file so no install runs
    (tmp_path / "docs_center" / "projects" / "my-app.json").write_text(
        json.dumps({"project_id": "my-app", "name": "my-app",
                    "technologies": [], "workspace_path": "/tmp/my-app"})
    )

    server = MCPServer(str(tmp_path))
    response = server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "clientInfo": {"name": "claude-code", "version": "0.2.103"},
            "rootUri": "file:///tmp/my-app",
        },
    })
    assert "result" in response
    assert server._session_id is not None
    assert server._session_project_id == "my-app"

    # Session row must exist
    rows = server.service.interaction_log.list_sessions()
    assert any(r["session_id"] == server._session_id for r in rows)


def test_mcp_server_records_interaction_on_tool_call(tmp_path) -> None:
    (tmp_path / "docs_center" / "technologies" / "react").mkdir(parents=True)
    (tmp_path / "docs_center" / "technologies" / "react" / "hooks.md").write_text(
        "# useState\nUse this to add state to components."
    )
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects" / "my-app.json").write_text(
        json.dumps({"project_id": "my-app", "name": "my-app",
                    "technologies": ["react"], "workspace_path": "/tmp/my-app"})
    )

    server = MCPServer(str(tmp_path))
    server.service.scan()
    server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"rootUri": "file:///tmp/my-app"},
    })
    server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "search_docs",
                   "arguments": {"technology": "react", "query": "useState"}},
    })

    rows = server.service.list_project_interactions("my-app")
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "search_docs"
    assert rows[0]["latency_ms"] >= 0
    assert rows[0]["result_chars"] and rows[0]["result_chars"] > 0
    assert rows[0]["error"] is None


def test_mcp_server_records_error_on_tool_failure(tmp_path) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects" / "my-app.json").write_text(
        json.dumps({"project_id": "my-app", "name": "my-app",
                    "technologies": [], "workspace_path": "/tmp/my-app"})
    )
    server = MCPServer(str(tmp_path))
    server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"rootUri": "file:///tmp/my-app"},
    })
    response = server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "search_docs",
                   "arguments": {"technology": "nonexistent", "query": "x"}},
    })
    # The tool may return an empty result or raise — in either case
    # an interaction row must exist. If it raised, error is populated.
    rows = server.service.list_project_interactions("my-app")
    assert len(rows) == 1


def test_mcp_server_logging_failure_does_not_break_tool_call(tmp_path, monkeypatch) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)

    server = MCPServer(str(tmp_path))
    server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"rootUri": "file:///tmp/no-such"},
    })

    # Force record_interaction to raise
    def boom(**kw):
        raise RuntimeError("log write failed")

    monkeypatch.setattr(server.service, "record_mcp_interaction", boom)

    response = server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "list_supported_libraries", "arguments": {}},
    })
    # Tool call must still return normally
    assert "result" in response or "error" in response
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_mcp_auto_install.py -v -k "mcp_server"`
Expected: FAIL — attributes/behaviour missing.

- [ ] **Step 3: Modify `MCPServer`**

In `src/buonaiuto_doc4llm/mcp_server.py`:

Near the top, add:

```python
import json
import time
import uuid

from buonaiuto_doc4llm.project_bootstrap import (
    ensure_project_installed,
    extract_workspace_path,
)
```

Inside the `MCPServer.__init__` method (or wherever the constructor lives — class body in this file), add session-state attributes. If `__init__` does not exist explicitly, add it:

```python
class MCPServer:
    def __init__(self, base_dir: str | Path, *,
                 service: DocsHubService | None = None) -> None:
        # Preserve any existing construction logic — the key additions are:
        self.service = service or DocsHubService(base_dir)
        self._session_id: str | None = None
        self._session_project_id: str | None = None
```

(If `__init__` already exists, just add the two `self._session_*` lines.)

Replace the existing `_bootstrap_from_initialize_params` method (line ~651) with:

```python
    def _bootstrap_from_initialize_params(self, params: dict[str, Any]) -> dict[str, Any] | None:
        workspace_path = extract_workspace_path(params)
        client_info = params.get("clientInfo") or {}

        self._session_id = str(uuid.uuid4())
        self._session_project_id = ensure_project_installed(
            self.service, workspace_path=workspace_path, wait=False,
        )
        self.service.record_mcp_session(
            session_id=self._session_id,
            project_id=self._session_project_id,
            workspace_path=str(workspace_path) if workspace_path else None,
            client_name=client_info.get("name"),
            client_version=client_info.get("version"),
        )
        return None  # kept for backwards compat with the return signature
```

Rename the existing `_call_tool` method to `_dispatch_tool`. Then add a new `_call_tool` wrapper:

```python
    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        error_msg: str | None = None
        result_chars: int | None = None
        try:
            result = self._dispatch_tool(name, arguments)
            try:
                result_chars = len(json.dumps(result, default=str))
            except (TypeError, ValueError):
                result_chars = None
            return result
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if self._session_id is None:
                # Tool called before initialize — generate a one-shot session
                self._session_id = str(uuid.uuid4())
                self.service.record_mcp_session(
                    session_id=self._session_id,
                    project_id=None, workspace_path=None,
                    client_name=None, client_version=None,
                )
            try:
                self.service.record_mcp_interaction(
                    session_id=self._session_id,
                    project_id=self._session_project_id,
                    tool_name=name,
                    arguments=arguments,
                    result_chars=result_chars,
                    error=error_msg,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:
                print(f"[mcp_server] record_mcp_interaction failed: {exc}",
                      file=sys.stderr)
```

Make sure `import sys` is present at the top of `mcp_server.py` (it likely is — check).

- [ ] **Step 4: Run**

Run: `pytest tests/test_mcp_auto_install.py -v`
Expected: all tests PASS.

Run: `pytest -q`
Expected: full suite still passes. Any existing test that asserted on `_bootstrap_from_initialize_params` returning a summary dict may need to be updated — the new contract returns `None` (session is recorded as a side-effect instead).

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/mcp_server.py tests/test_mcp_auto_install.py
git commit -m "MCPServer: pin session on initialize, record every tool call"
```

---

## Task 10: `list_active_projects` + `refresh_active_projects` — pure logic

**Files:**
- Create: `src/buonaiuto_doc4llm/refresh_active.py`
- Test: `tests/test_refresh_active_projects.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_refresh_active_projects.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from buonaiuto_doc4llm.refresh_active import (
    list_active_projects,
    refresh_active_projects,
)
from buonaiuto_doc4llm.service import DocsHubService


@pytest.fixture
def svc(tmp_path: Path) -> DocsHubService:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    return DocsHubService(tmp_path)


def _install_project_file(svc: DocsHubService, pid: str, *, technologies: list[str],
                          workspace_path: str | None = None,
                          mtime_offset_seconds: int = 0) -> Path:
    payload = {
        "project_id": pid, "name": pid, "technologies": technologies,
    }
    if workspace_path:
        payload["workspace_path"] = workspace_path
    p = svc.projects_root / f"{pid}.json"
    p.write_text(json.dumps(payload))
    if mtime_offset_seconds:
        new = time.time() + mtime_offset_seconds
        os.utime(p, (new, new))
    return p


def _log_interaction(svc: DocsHubService, pid: str) -> None:
    svc.record_mcp_session(
        session_id=f"s-{pid}", project_id=pid, workspace_path=None,
        client_name="t", client_version="0",
    )
    svc.record_mcp_interaction(
        session_id=f"s-{pid}", project_id=pid, tool_name="search_docs",
        arguments={}, result_chars=0, error=None, latency_ms=1,
    )


def test_list_active_projects_only_includes_recent_activity(svc: DocsHubService) -> None:
    _install_project_file(svc, "active", technologies=["react"])
    _install_project_file(svc, "idle", technologies=["vue"])
    svc.sync_projects()
    _log_interaction(svc, "active")

    pids = {p["project_id"] for p in list_active_projects(svc, days=30)}
    assert pids == {"active"}


def test_refresh_active_projects_dry_run_returns_plan(svc: DocsHubService, monkeypatch) -> None:
    _install_project_file(svc, "active", technologies=["react", "nextjs"])
    svc.sync_projects()
    _log_interaction(svc, "active")

    # Guarantee fetch is never invoked in dry-run
    def forbidden(*_a, **_kw):
        raise AssertionError("fetch should not run in dry-run")

    monkeypatch.setattr("buonaiuto_doc4llm.refresh_active._fetch_technology", forbidden)

    plan = refresh_active_projects(svc, days=30, dry_run=True)
    assert plan["dry_run"] is True
    assert "active" in {p["project_id"] for p in plan["projects"]}
    assert set(plan["technologies_to_fetch"]) == {"react", "nextjs"}


def test_refresh_active_projects_deduplicates_technologies(svc, monkeypatch) -> None:
    _install_project_file(svc, "a", technologies=["react"])
    _install_project_file(svc, "b", technologies=["react", "stripe"])
    svc.sync_projects()
    _log_interaction(svc, "a")
    _log_interaction(svc, "b")

    fetched: list[str] = []

    def fake_fetch(svc_arg, tech: str) -> dict:
        fetched.append(tech)
        return {"technology": tech, "status": "unchanged"}

    monkeypatch.setattr("buonaiuto_doc4llm.refresh_active._fetch_technology", fake_fetch)
    refresh_active_projects(svc, days=30, dry_run=False)
    assert sorted(fetched) == ["react", "stripe"]  # react only once


def test_refresh_active_projects_reinstalls_stale_project_files(svc, monkeypatch) -> None:
    _install_project_file(svc, "active", technologies=["react"],
                          workspace_path=str(svc.base_dir / "active-project"),
                          mtime_offset_seconds=-(30 * 3600))
    (svc.base_dir / "active-project").mkdir()
    svc.sync_projects()
    _log_interaction(svc, "active")

    reinstalls: list[dict] = []
    monkeypatch.setattr(
        svc, "install_project",
        lambda **kw: reinstalls.append(kw) or {"project_id": "active"},
    )
    monkeypatch.setattr(
        "buonaiuto_doc4llm.refresh_active._fetch_technology",
        lambda svc, tech: {"technology": tech, "status": "ok"},
    )
    refresh_active_projects(svc, days=30, dry_run=False)
    assert len(reinstalls) == 1
    assert reinstalls[0]["project_id"] == "active"


def test_refresh_active_projects_skips_reinstall_when_workspace_path_missing(svc, monkeypatch) -> None:
    _install_project_file(svc, "legacy", technologies=["react"],
                          mtime_offset_seconds=-(30 * 3600))
    svc.sync_projects()
    _log_interaction(svc, "legacy")

    monkeypatch.setattr(svc, "install_project",
                        lambda **kw: pytest.fail("install should not run"))
    monkeypatch.setattr(
        "buonaiuto_doc4llm.refresh_active._fetch_technology",
        lambda svc, tech: {"technology": tech, "status": "ok"},
    )
    result = refresh_active_projects(svc, days=30, dry_run=False)
    assert "legacy" in {p["project_id"] for p in result["projects"]}


def test_refresh_active_projects_continues_on_per_project_failure(svc, monkeypatch) -> None:
    _install_project_file(svc, "bad", technologies=["react"],
                          workspace_path=str(svc.base_dir / "bad-path"),
                          mtime_offset_seconds=-(30 * 3600))
    _install_project_file(svc, "good", technologies=["stripe"])
    svc.sync_projects()
    _log_interaction(svc, "bad")
    _log_interaction(svc, "good")

    def install(**kw):
        if kw["project_id"] == "bad":
            raise RuntimeError("no manifests")
        return {"project_id": kw["project_id"]}

    monkeypatch.setattr(svc, "install_project", install)
    monkeypatch.setattr(
        "buonaiuto_doc4llm.refresh_active._fetch_technology",
        lambda svc, tech: {"technology": tech, "status": "ok"},
    )

    result = refresh_active_projects(svc, days=30, dry_run=False)
    pids = {p["project_id"]: p for p in result["projects"]}
    assert pids["bad"]["install_error"] is not None
    assert pids["good"].get("install_error") in (None,)
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_refresh_active_projects.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/buonaiuto_doc4llm/refresh_active.py
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from buonaiuto_doc4llm.service import DocsHubService

FRESHNESS_SECONDS = 24 * 60 * 60


def list_active_projects(service: "DocsHubService", *, days: int = 30) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with service._connect() as conn:
        active_ids = [
            r["project_id"] for r in conn.execute(
                """
                SELECT DISTINCT project_id FROM mcp_interactions
                 WHERE project_id IS NOT NULL AND created_at >= ?
                """,
                (since,),
            ).fetchall()
        ]
    if not active_ids:
        return []

    out: list[dict[str, Any]] = []
    for pid in active_ids:
        path = service.projects_root / f"{pid}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "project_id": pid,
            "name": data.get("name", pid),
            "technologies": list(data.get("technologies") or []),
            "workspace_path": data.get("workspace_path"),
            "project_file_mtime": path.stat().st_mtime,
        })
    return out


def _fetch_technology(service: "DocsHubService", technology: str) -> dict[str, Any]:
    """Thin seam so tests can monkeypatch out HTTP."""
    from buonaiuto_doc4llm.__main__ import _build_fetcher  # reuse registry wiring
    fetcher = _build_fetcher(service.base_dir)
    return fetcher.fetch(technology)


def refresh_active_projects(
    service: "DocsHubService", *, days: int = 30, dry_run: bool = False,
) -> dict[str, Any]:
    projects = list_active_projects(service, days=days)
    techs_union: set[str] = set()
    per_project: list[dict[str, Any]] = []

    for p in projects:
        needs_reinstall = (
            p["workspace_path"]
            and (time.time() - p["project_file_mtime"] >= FRESHNESS_SECONDS)
        )
        entry: dict[str, Any] = {
            "project_id": p["project_id"],
            "technologies": p["technologies"],
            "needs_reinstall": bool(needs_reinstall),
            "install_error": None,
        }
        if needs_reinstall and not dry_run:
            try:
                service.install_project(
                    project_root=Path(p["workspace_path"]),
                    project_id=p["project_id"],
                )
            except Exception as exc:  # noqa: BLE001
                entry["install_error"] = f"{type(exc).__name__}: {exc}"
                print(
                    f"[refresh_active] install_project({p['project_id']}) failed: {exc}",
                    file=sys.stderr,
                )
        techs_union.update(p["technologies"])
        per_project.append(entry)

    fetches: list[dict[str, Any]] = []
    if not dry_run:
        for tech in sorted(techs_union):
            try:
                fetches.append(_fetch_technology(service, tech))
            except Exception as exc:  # noqa: BLE001
                fetches.append({"technology": tech, "status": "error",
                                "error": f"{type(exc).__name__}: {exc}"})
                print(f"[refresh_active] fetch({tech}) failed: {exc}", file=sys.stderr)
        service.scan()

    return {
        "dry_run": dry_run,
        "days": days,
        "projects": per_project,
        "technologies_to_fetch": sorted(techs_union),
        "fetches": fetches,
    }
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_refresh_active_projects.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/refresh_active.py tests/test_refresh_active_projects.py
git commit -m "Add refresh_active_projects: dedup techs, re-install stale files, isolate failures"
```

---

## Task 11: `refresh-active` CLI subcommand

**Files:**
- Modify: `src/buonaiuto_doc4llm/__main__.py`
- Test: `tests/test_refresh_active_projects.py`

- [ ] **Step 1: Failing test**

```python
# Append to tests/test_refresh_active_projects.py
import subprocess
import sys as _sys


def test_refresh_active_cli_dry_run(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    svc = DocsHubService(tmp_path)
    _install_project_file(svc, "x", technologies=["react"])
    svc.sync_projects()
    _log_interaction(svc, "x")

    env = dict(**os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [_sys.executable, "-m", "buonaiuto_doc4llm",
         "--base-dir", str(tmp_path), "refresh-active", "--dry-run"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert any(p["project_id"] == "x" for p in payload["projects"])
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_refresh_active_projects.py::test_refresh_active_cli_dry_run -v`
Expected: FAIL — subcommand not defined.

- [ ] **Step 3: Add the subparser**

In `src/buonaiuto_doc4llm/__main__.py`, inside `build_parser()` (around line 132–244), add after the `schedule` subparser block:

```python
    refresh = subparsers.add_parser(
        "refresh-active",
        help="Refresh docs for every project that has called the MCP server recently",
    )
    refresh.add_argument("--days", type=int, default=30,
                         help="Treat projects as active if they have interactions in this window (default: 30)")
    refresh.add_argument("--dry-run", action="store_true",
                         help="Show the plan without fetching or re-installing")
```

In `main()`, add a new dispatch branch alongside the others:

```python
    if args.command == "refresh-active":
        from buonaiuto_doc4llm.refresh_active import refresh_active_projects
        payload = refresh_active_projects(
            service, days=args.days, dry_run=args.dry_run,
        )
        print_json(payload)
        return
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_refresh_active_projects.py::test_refresh_active_cli_dry_run -v`
Expected: PASS.

Run: `pytest -q`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/__main__.py tests/test_refresh_active_projects.py
git commit -m "Add CLI subcommand: python -m buonaiuto_doc4llm refresh-active"
```

---

## Task 12: Second scheduler entry for 3-day `refresh-active`

**Files:**
- Modify: `src/buonaiuto_doc4llm/scheduler.py`
- Test: `tests/test_scheduler_refresh_active.py` (new, small)

- [ ] **Step 1: Failing test**

```python
# tests/test_scheduler_refresh_active.py
from __future__ import annotations

from pathlib import Path

import pytest

from buonaiuto_doc4llm import scheduler


@pytest.fixture(autouse=True)
def _fake_platform(monkeypatch):
    # Force Linux-crontab path for deterministic string generation
    monkeypatch.setattr(scheduler, "_is_macos", lambda: False)


def test_crontab_includes_fetch_and_refresh_entries(monkeypatch, tmp_path) -> None:
    captured: list[str] = []

    def fake_write_crontab(content: str) -> None:
        captured.append(content)

    monkeypatch.setattr(scheduler, "_write_crontab", fake_write_crontab)
    monkeypatch.setattr(scheduler, "_read_crontab", lambda: "")

    result = scheduler.install_schedule(tmp_path, hour=4, minute=0)
    assert "status" in result or "installed" in result.get("status", str(result))
    assert len(captured) == 1
    content = captured[0]
    assert "fetch" in content
    assert "refresh-active" in content
    # Refresh entry should be every 3 days
    assert "*/3" in content
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_scheduler_refresh_active.py -v`
Expected: FAIL — current implementation only installs the fetch entry.

- [ ] **Step 3: Inspect the scheduler file**

Read `src/buonaiuto_doc4llm/scheduler.py`. It will have functions like `install_schedule`, `uninstall_schedule`, `_read_crontab`, `_write_crontab`, and platform-specific paths for macOS launchd and Linux crontab.

- [ ] **Step 4: Add the second entry**

For the Linux crontab path, the current code builds a crontab line like:
```
0 4 * * * cd /path && /path/to/python -m buonaiuto_doc4llm --base-dir /path fetch >> /path/fetch.log 2>&1
```

Add a second line below it:
```
15 4 */3 * * cd /path && /path/to/python -m buonaiuto_doc4llm --base-dir /path refresh-active >> /path/refresh-active.log 2>&1
```

Wrap both lines with the existing start/end markers (e.g. `# BEGIN buonaiuto-doc4llm` / `# END buonaiuto-doc4llm`) so `uninstall_schedule` removes them together.

For the macOS launchd path, add a second plist file `com.buonaiuto.doc4llm.refresh-active.plist` with:
- `StartCalendarInterval`: every 3 days — launchd doesn't support "every N days" natively, so use a `StartInterval` of `259200` (3 days in seconds) instead, which is the idiomatic approach.
- Command: `python -m buonaiuto_doc4llm --base-dir <base> refresh-active`

Both plists should be loaded on install and unloaded on uninstall. Extend `schedule_status` to report the status of both jobs as a list.

- [ ] **Step 5: Run**

Run: `pytest tests/test_scheduler_refresh_active.py -v`
Expected: PASS.

Run: `pytest -q`
Expected: no regressions. If an existing scheduler test asserts on a single crontab line exactly, update it to tolerate the second entry.

- [ ] **Step 6: Commit**

```bash
git add src/buonaiuto_doc4llm/scheduler.py tests/test_scheduler_refresh_active.py
git commit -m "Scheduler: install a second cron entry for 3-day refresh-active"
```

---

## Task 13: Dashboard Jinja filters

**Files:**
- Create: `src/buonaiuto_doc4llm/dashboard/_filters.py`
- Test: in `tests/test_dashboard_project_log.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dashboard_project_log.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from buonaiuto_doc4llm.dashboard._filters import (
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
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_dashboard_project_log.py::test_mcp_args_summary_search_docs -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/buonaiuto_doc4llm/dashboard/_filters.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def mcp_args_summary(tool_name: str, args: dict[str, Any] | Any) -> str:
    if not isinstance(args, dict):
        return str(args)

    if tool_name in ("search_docs", "search_documentation"):
        tech = args.get("technology") or args.get("libraries") or ""
        if isinstance(tech, list):
            tech = ", ".join(str(t) for t in tech)
        q = args.get("query", "")
        return f'{tech}, "{q}"'
    if tool_name in ("read_doc", "read_full_page"):
        return f'{args.get("technology", "")}/{args.get("rel_path", "")}'
    if tool_name in ("list_project_updates", "ack_project_updates"):
        return str(args.get("project_id", ""))
    if tool_name == "fetch_docs":
        return args.get("technology") or "all"
    if tool_name == "install_project":
        return str(args.get("project_path", ""))

    items = list(args.items())[:2]
    return ", ".join(f"{k}={v}" for k, v in items) or "—"


def humanize_timedelta(moment: datetime | str | None) -> str:
    if moment is None:
        return "never"
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return moment
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - moment
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def truncate_chars(value: str, limit: int) -> str:
    if value is None:
        return ""
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_dashboard_project_log.py -v`
Expected: all filter tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/dashboard/_filters.py tests/test_dashboard_project_log.py
git commit -m "Dashboard: add mcp_args_summary / humanize_timedelta / truncate_chars filters"
```

---

## Task 14: Dashboard routes for `/projects/<id>/log` + per-project summary on `/projects`

**Files:**
- Modify: `src/buonaiuto_doc4llm/dashboard/routes.py`
- Modify: `src/buonaiuto_doc4llm/dashboard/templates/projects.html`
- Test: extend `tests/test_dashboard_project_log.py`

- [ ] **Step 1: Failing tests**

```python
# Append to tests/test_dashboard_project_log.py
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
        c.service = app.state.service  # convenience handle if exposed
        yield c, tmp_path


def _seed_interaction(tmp_path, pid: str, tool: str = "search_docs") -> None:
    from buonaiuto_doc4llm.service import DocsHubService
    svc = DocsHubService(tmp_path)
    svc.record_mcp_session(session_id="s", project_id=pid, workspace_path=None,
                           client_name="c", client_version="1")
    svc.record_mcp_interaction(session_id="s", project_id=pid, tool_name=tool,
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
    assert "View Log" in body


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
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_dashboard_project_log.py -v`
Expected: FAIL — routes / template additions missing.

- [ ] **Step 3: Register Jinja filters**

In `src/buonaiuto_doc4llm/dashboard/routes.py` (or wherever `templates = Jinja2Templates(...)` is constructed), add:

```python
from buonaiuto_doc4llm.dashboard._filters import (
    humanize_timedelta, mcp_args_summary, truncate_chars,
)

templates.env.filters["mcp_args_summary"] = mcp_args_summary
templates.env.filters["humanize_timedelta"] = humanize_timedelta
templates.env.filters["truncate_chars"] = truncate_chars
```

- [ ] **Step 4: Enrich `_get_projects_with_unread`**

Inside `_get_projects_with_unread` (line ~106), after the existing unread-count loop, also populate:

```python
        for p in projects:
            summary = service.get_project_interaction_summary(p["project_id"], days=30)
            p["last_used_at"] = summary["last_used_at"]
            p["call_count_30d"] = summary["total_calls"]
```

- [ ] **Step 5: Add log routes**

After the existing `/projects` route, add:

```python
    @app.get("/projects/{project_id}/log", response_class=HTMLResponse)
    async def project_log(request: Request, project_id: str) -> HTMLResponse:
        service = request.app.state.service
        service.prune_mcp_interactions(days=30)
        is_unattributed = project_id == "unattributed"
        effective_id: str | None = None if is_unattributed else project_id
        summary = service.get_project_interaction_summary(effective_id, days=30)
        rows = service.list_project_interactions(effective_id, limit=50)
        return _render(request, "project_log.html", {
            "project_id": project_id,
            "summary": summary,
            "rows": rows,
            "is_unattributed": is_unattributed,
        })

    @app.get("/projects/{project_id}/log/rows", response_class=HTMLResponse)
    async def project_log_rows(
        request: Request,
        project_id: str,
        tool_name: str = "",
        since_hours: int = 720,  # 30d
        errors_only: bool = False,
        offset: int = 0,
    ) -> HTMLResponse:
        service = request.app.state.service
        is_unattributed = project_id == "unattributed"
        effective_id: str | None = None if is_unattributed else project_id
        since_iso = (
            datetime.now(timezone.utc) - timedelta(hours=since_hours)
        ).isoformat(timespec="seconds")
        rows = service.list_project_interactions(
            effective_id,
            limit=50,
            offset=offset,
            tool_name=tool_name or None,
            since=since_iso,
            errors_only=errors_only,
        )
        return _render(request, "_project_log_rows.html", {
            "rows": rows,
            "project_id": project_id,
            "next_offset": offset + 50 if len(rows) == 50 else None,
        })
```

Ensure `from datetime import datetime, timedelta, timezone` is imported at the top of the file (likely already present).

- [ ] **Step 6: Update `projects.html`**

Immediately after the existing "Acknowledge" button block, add:

```html
      <a href="/projects/{{ project.project_id | urlencode }}/log" class="btn btn-sm">
        View log
      </a>
    </div>
  </div>
  <div class="card-body">
    <div class="text-muted text-sm" style="margin-bottom: 6px;">
      last used {{ project.last_used_at | humanize_timedelta }}
      · {{ project.call_count_30d or 0 }} calls / 30d
    </div>
    <div class="form-label">Subscribed Technologies</div>
```

(Replace only the opening of the existing `<div class="card-body">` — keep the subscribed-technologies block below.)

Then after the `{% for project in projects %}` loop block, add:

```html
{% set unattributed = service.list_unattributed_mcp_sessions(days=30) %}
{% if unattributed %}
<div class="card">
  <div class="card-header">
    <span class="card-title text-amber">Unattributed sessions ({{ unattributed|length }})</span>
  </div>
  <div class="card-body">
    <div class="text-muted text-sm">
      MCP sessions whose workspace path did not match any
      <code>docs_center/projects/*.json</code> file.
    </div>
    <a href="/projects/unattributed/log" class="btn btn-sm" style="margin-top: 8px;">
      View log
    </a>
  </div>
</div>
{% endif %}
```

For the `service` accessor, expose it from the projects route context:

```python
            "service": service,
```

(Add this key to the `ctx` dict passed into `_render(request, "projects.html", ctx)`.)

- [ ] **Step 7: Run**

Run: `pytest tests/test_dashboard_project_log.py -v`
Expected: all tests PASS.

Run: `pytest -q`
Expected: no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/buonaiuto_doc4llm/dashboard/routes.py src/buonaiuto_doc4llm/dashboard/templates/projects.html tests/test_dashboard_project_log.py
git commit -m "Dashboard: /projects/<id>/log page + per-project summary + unattributed card"
```

---

## Task 15: `project_log.html` + `_project_log_rows.html` templates

**Files:**
- Create: `src/buonaiuto_doc4llm/dashboard/templates/project_log.html`
- Create: `src/buonaiuto_doc4llm/dashboard/templates/_project_log_rows.html`

- [ ] **Step 1: Write `_project_log_rows.html`**

```html
{# src/buonaiuto_doc4llm/dashboard/templates/_project_log_rows.html #}
{% for row in rows %}
<tr>
  <td class="text-muted">{{ row.created_at | humanize_timedelta }}</td>
  <td><code>{{ row.tool_name }}</code></td>
  <td>{{ row.tool_name | default('') }}{% if row.arguments_json %}
    {{ row.tool_name | mcp_args_summary(row.arguments_json | fromjson) }}
    {% endif %}</td>
  <td class="text-right">{{ row.latency_ms }}ms</td>
  <td class="text-right">{{ (row.result_chars or 0) | filesizeformat }}</td>
  <td>{% if row.error %}<span class="badge badge-red">error</span>{% else %}<span class="badge badge-green">ok</span>{% endif %}</td>
</tr>
{% endfor %}
{% if next_offset %}
<tr hx-get="/projects/{{ project_id }}/log/rows?offset={{ next_offset }}"
    hx-trigger="revealed" hx-swap="outerHTML">
  <td colspan="6" class="text-center text-muted">Loading more…</td>
</tr>
{% endif %}
```

Add a `fromjson` Jinja filter to `_filters.py`:

```python
import json as _json
def fromjson(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return _json.loads(value)
    except (ValueError, TypeError):
        return {}
```

And register it in `routes.py` alongside the others:

```python
templates.env.filters["fromjson"] = fromjson
```

- [ ] **Step 2: Write `project_log.html`**

```html
{# src/buonaiuto_doc4llm/dashboard/templates/project_log.html #}
{% extends "base.html" %}
{% block title %}Buonaiuto Doc4LLM &middot; {{ project_id }} log{% endblock %}

{% block content %}
<div class="page-header">
  <div class="page-title">
    {% if is_unattributed %}Unattributed sessions{% else %}{{ project_id }}{% endif %}
    &middot; MCP interaction log
  </div>
  <div class="page-subtitle">
    Last used {{ summary.last_used_at | humanize_timedelta }} &middot;
    {{ summary.total_calls }} calls / {{ summary.window_days }}d &middot;
    {{ summary.unique_tools }} tools &middot;
    {{ summary.total_sessions }} sessions &middot;
    error rate {{ '%.1f'|format(summary.error_rate * 100) }}%
  </div>
  {% if summary.client_breakdown %}
  <div class="text-muted text-sm" style="margin-top: 4px;">
    Clients:
    {% for c in summary.client_breakdown %}
      {{ c.client_name or '—' }}{% if c.client_version %} {{ c.client_version }}{% endif %}
      ({{ c.count }}){% if not loop.last %},{% endif %}
    {% endfor %}
  </div>
  {% endif %}
</div>

<div class="grid" style="grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 16px;">
  <div class="card">
    <div class="card-header"><span class="card-title">Calls per day</span></div>
    <div class="card-body">
      {% set max_count = summary.calls_per_day | map(attribute='count') | max %}
      <svg width="100%" height="120" viewBox="0 0 300 120" preserveAspectRatio="none">
        {% for d in summary.calls_per_day %}
          {% set bar_h = (d.count / max_count * 110) if max_count else 0 %}
          <rect x="{{ loop.index0 * 10 }}" y="{{ 120 - bar_h }}"
                width="8" height="{{ bar_h }}" fill="#4a90e2">
            <title>{{ d.day }}: {{ d.count }} calls</title>
          </rect>
        {% endfor %}
      </svg>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><span class="card-title">Top tools</span></div>
    <div class="card-body">
      <table class="table table-sm">
        {% for t in summary.tool_counts[:10] %}
        <tr>
          <td><code>{{ t.tool_name }}</code></td>
          <td class="text-right">{{ t.count }}</td>
        </tr>
        {% endfor %}
      </table>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-header">
    <span class="card-title">Recent calls</span>
    <form hx-get="/projects/{{ project_id }}/log/rows"
          hx-target="#log-rows" hx-swap="innerHTML"
          style="display: flex; gap: 8px;">
      <input type="text" name="tool_name" class="input input-sm"
             placeholder="Filter tool (e.g. search_docs)">
      <select name="since_hours" class="input input-sm">
        <option value="1">Last 1h</option>
        <option value="24">Last 24h</option>
        <option value="168">Last 7d</option>
        <option value="720" selected>Last 30d</option>
      </select>
      <label class="text-sm"><input type="checkbox" name="errors_only" value="true"> Errors only</label>
      <button class="btn btn-sm">Filter</button>
    </form>
  </div>
  <div class="card-body">
    <table class="table">
      <thead>
        <tr>
          <th>Time</th><th>Tool</th><th>Key args</th>
          <th class="text-right">Latency</th>
          <th class="text-right">Size</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="log-rows">
        {% include "_project_log_rows.html" %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Run**

Run: `pytest tests/test_dashboard_project_log.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/buonaiuto_doc4llm/dashboard/templates/project_log.html src/buonaiuto_doc4llm/dashboard/templates/_project_log_rows.html src/buonaiuto_doc4llm/dashboard/_filters.py src/buonaiuto_doc4llm/dashboard/routes.py
git commit -m "Dashboard: project_log.html with summary + SVG chart + HTMX filter table"
```

---

## Task 16: End-to-end smoke test

**Files:**
- Test: `tests/test_mcp_auto_install.py` (extend)

- [ ] **Step 1: Failing test**

```python
# Append to tests/test_mcp_auto_install.py
def test_end_to_end_first_connect_installs_and_logs(tmp_path, monkeypatch) -> None:
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)

    # Create a minimal fake project folder
    proj = tmp_path / "my-new-project"
    proj.mkdir()
    (proj / "package.json").write_text('{"name":"fake","dependencies":{"react":"^18"}}')

    server = MCPServer(str(tmp_path))

    # Stub out HTTP fetch so we don't go to the network
    monkeypatch.setattr(
        "buonaiuto_doc4llm.service.HttpDocFetcher",
        lambda *a, **kw: type("F", (), {"fetch_all": lambda self: [], "fetch": lambda self, t: {}})(),
        raising=False,
    )

    server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"rootUri": f"file://{proj}"},
    })
    # Wait for background install to finish
    import time as _t
    for _ in range(50):
        if (tmp_path / "docs_center" / "projects" / "my-new-project.json").exists():
            break
        _t.sleep(0.1)
    assert (tmp_path / "docs_center" / "projects" / "my-new-project.json").exists()

    # Follow-up tool call is logged against the project
    server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "list_supported_libraries", "arguments": {}},
    })
    rows = server.service.list_project_interactions("my-new-project")
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "list_supported_libraries"
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_mcp_auto_install.py::test_end_to_end_first_connect_installs_and_logs -v`
Expected: PASS after the previous tasks' plumbing. If it fails, check that:
- `_bootstrap_from_initialize_params` runs `ensure_project_installed` (not the old synchronous install)
- The background thread reaches `install_project`
- `_dispatch_tool` is correctly called by the new `_call_tool` wrapper

- [ ] **Step 3: Commit**

```bash
git add tests/test_mcp_auto_install.py
git commit -m "Add end-to-end smoke test: first connect auto-installs + logs tool call"
```

---

## Task 17: Full suite + docs

**Files:**
- Run: full test suite
- Modify: `README.md` (already updated in the spec commit — verify no further changes needed)

- [ ] **Step 1: Run the full suite**

Run: `pytest -q`
Expected: every test passes.

- [ ] **Step 2: Run the MCP server manually and confirm the dashboard works**

```bash
PYTHONPATH=src /opt/anaconda3/bin/python -m buonaiuto_doc4llm \
  --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs \
  dashboard
```

Open `http://127.0.0.1:8420/projects` in a browser. Each project card should show a "View log" button and a "last used … · N calls / 30d" line. Click **View log** on an existing project — the page should render with the chart, top-tools panel, and table (empty if no MCP interactions yet). Stop the dashboard.

- [ ] **Step 3: Confirm `refresh-active --dry-run` works**

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm refresh-active --dry-run
```

Expected: a JSON payload with `"dry_run": true` and the list of active projects and their union of technologies.

- [ ] **Step 4: Commit any trailing fixes**

If manual verification revealed a UI or wording issue, fix inline, re-run tests, and commit with a descriptive message.

```bash
git commit -am "Polish: <describe any fix>"
```

- [ ] **Step 5: Final summary commit is not needed — all real work is already committed.**

---

## Completion criteria

- All 40+ new tests pass.
- Full `pytest -q` suite passes.
- `/projects` shows last-used + call counts and a "View log" button per project.
- `/projects/<id>/log` renders with a summary header, SVG chart, top-tools panel, and HTMX-filtered table.
- First MCP `initialize` against an unknown folder writes `docs_center/projects/<basename>.json` within a few seconds (background thread).
- `refresh-active --dry-run` emits a plan; with no `--dry-run`, it fetches subscribed technologies for every active project.
- `schedule install` registers two cron entries; `schedule status` reports both.
