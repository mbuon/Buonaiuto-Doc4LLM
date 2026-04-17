from __future__ import annotations

import json
import sqlite3
import sys
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InteractionLogStore:
    def __init__(self, connect: Callable[[], sqlite3.Connection]):
        self._connect = connect

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

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
