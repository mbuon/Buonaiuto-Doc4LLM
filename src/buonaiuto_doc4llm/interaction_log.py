from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# Public sentinel for truncated strings
MAX_STRING_LEN = 500
# ASCII-only ellipsis so Windows cp1252 terminals don't mojibake it.
TRUNCATION_TEMPLATE = "<truncated>...[{n} chars]"

# Upper bound on per-field lengths persisted to the log (client_name etc.).
MAX_CLIENT_FIELD_LEN = 256
# Max depth before sanitize_arguments stops descending into nested payloads.
MAX_SANITIZE_DEPTH = 32
# Hard cap on rows returned by list/query methods.
MAX_ROWS = 1000

# Module-level sentinel object meaning "no project_id filter at all".
# Distinct from None (which selects project_id IS NULL).
_ALL = object()


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
CREATE INDEX IF NOT EXISTS idx_mcp_sessions_last_seen_at
    ON mcp_sessions(last_seen_at);

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
    # Microsecond precision so events within the same wall-clock second still
    # sort deterministically when ORDER BY created_at is used alongside id.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sanitize_arguments(value: Any, _depth: int = 0) -> Any:
    """Recursively truncate overlong string fields before persistence.

    Strings longer than MAX_STRING_LEN are replaced with a short sentinel
    that preserves the original length so the log stays legible but small.
    Recursion depth is bounded to guard against self-referential structures
    and pathological JSON.
    """
    if _depth >= MAX_SANITIZE_DEPTH:
        return TRUNCATION_TEMPLATE.format(n=0) + "[max-depth]"
    if isinstance(value, str):
        if len(value) > MAX_STRING_LEN:
            return TRUNCATION_TEMPLATE.format(n=len(value))
        return value
    if isinstance(value, (bytes, bytearray)):
        n = len(value)
        if n > MAX_STRING_LEN:
            return TRUNCATION_TEMPLATE.format(n=n)
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return TRUNCATION_TEMPLATE.format(n=n) + "[bytes]"
    if isinstance(value, dict):
        return {k: sanitize_arguments(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_arguments(v, _depth + 1) for v in value]
    if isinstance(value, tuple):
        # Normalize to list so JSON round-trip is stable (json serializes
        # both tuples and lists as arrays; reading back always yields list).
        return [sanitize_arguments(v, _depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        return [sanitize_arguments(v, _depth + 1) for v in value]
    return value


def _clamp(value: str | None, max_len: int = MAX_CLIENT_FIELD_LEN) -> str | None:
    """Clamp an untrusted client-supplied string to at most max_len chars."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if len(value) > max_len:
        return value[:max_len]
    return value


class InteractionLogStore:
    def __init__(self, connect: Callable[[], sqlite3.Connection]):
        self._connect = connect
        # Serialises session-id allocation and cross-session record updates
        # to protect against multi-threaded MCP transports (stdio is single-
        # threaded today, but streamable HTTP is on the roadmap).
        self._lock = threading.Lock()

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    # ──────────────────────────────────────────────────────────────────
    # Writes
    # ──────────────────────────────────────────────────────────────────

    def record_session(self, *, session_id: str, project_id: str | None,
                       workspace_path: str | None, client_name: str | None,
                       client_version: str | None) -> None:
        """Upsert a session row.

        On conflict, existing fields are filled in with COALESCE so that a
        real record_session call after an earlier stub (created by
        record_interaction) backfills missing attribution.
        """
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
                        project_id     = COALESCE(mcp_sessions.project_id,     excluded.project_id),
                        workspace_path = COALESCE(mcp_sessions.workspace_path, excluded.workspace_path),
                        client_name    = COALESCE(mcp_sessions.client_name,    excluded.client_name),
                        client_version = COALESCE(mcp_sessions.client_version, excluded.client_version),
                        started_at     = MIN(mcp_sessions.started_at, excluded.started_at),
                        last_seen_at   = MAX(mcp_sessions.last_seen_at, excluded.last_seen_at)
                    """,
                    (session_id, project_id, _clamp(workspace_path, 4096),
                     _clamp(client_name), _clamp(client_version),
                     now, now),
                )
        except sqlite3.Error as exc:
            print(f"[interaction_log] record_session failed: {exc}",
                  file=sys.stderr)

    def record_interaction(self, *, session_id: str, project_id: str | None,
                           tool_name: str, arguments: Any,
                           result_chars: int | None, error: str | None,
                           latency_ms: int) -> None:
        """Insert one interaction row.

        Also upserts a stub session row so that orphan interactions stay
        queryable. If a real session row exists, COALESCE keeps the real
        metadata.
        """
        clean = sanitize_arguments(arguments)
        try:
            payload = json.dumps(clean, default=str, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            payload = json.dumps({"_serialization_error": str(exc)[:200]})
        latency_ms = max(0, int(latency_ms))
        if result_chars is not None:
            result_chars = max(0, int(result_chars))
        now = _now_iso()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO mcp_sessions
                        (session_id, project_id, workspace_path, client_name,
                         client_version, started_at, last_seen_at)
                    VALUES (?, ?, NULL, NULL, NULL, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        project_id   = COALESCE(mcp_sessions.project_id, excluded.project_id),
                        last_seen_at = MAX(mcp_sessions.last_seen_at, excluded.last_seen_at)
                    """,
                    (session_id, project_id, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO mcp_interactions
                        (session_id, project_id, tool_name, arguments_json,
                         result_chars, error, latency_ms, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, project_id, _clamp(tool_name), payload,
                     result_chars, _clamp(error, 2048), latency_ms, now),
                )
        except sqlite3.Error as exc:
            print(f"[interaction_log] record_interaction failed "
                  f"(session={session_id!r}, tool={tool_name!r}): {exc}",
                  file=sys.stderr)

    # ──────────────────────────────────────────────────────────────────
    # Reads
    # ──────────────────────────────────────────────────────────────────

    def list_sessions(self, *, project_id: Any = _ALL,
                      limit: int = 200) -> list[dict[str, Any]]:
        """List sessions, optionally filtered by project_id.

        - project_id=_ALL (default): return every session.
        - project_id=None: return only sessions whose project_id IS NULL.
        - project_id="foo": return only sessions where project_id='foo'.
        """
        limit = max(1, min(int(limit), MAX_ROWS))
        with self._connect() as conn:
            if project_id is _ALL:
                rows = conn.execute(
                    "SELECT * FROM mcp_sessions ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            elif project_id is None:
                rows = conn.execute(
                    "SELECT * FROM mcp_sessions WHERE project_id IS NULL "
                    "ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mcp_sessions WHERE project_id = ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def list_interactions(self, *, project_id: Any = _ALL, limit: int = 100,
                          offset: int = 0, tool_name: str | None = None,
                          since: str | None = None,
                          errors_only: bool = False) -> list[dict[str, Any]]:
        """List interactions with optional filters and bounded paging.

        - project_id=_ALL: every interaction.
        - project_id=None: only project_id IS NULL (unattributed).
        - project_id="foo": exact match.
        """
        limit = max(1, min(int(limit), MAX_ROWS))
        offset = max(0, int(offset))
        sql = ["SELECT * FROM mcp_interactions WHERE 1=1"]
        args: list[Any] = []
        if project_id is _ALL:
            pass
        elif project_id is None:
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

    def get_summary(self, project_id: Any = _ALL, *, days: int = 30) -> dict[str, Any]:
        """Aggregate per-project stats over the last `days` days.

        project_id=_ALL aggregates across every project plus unattributed;
        None filters to unattributed only; any string matches exactly.
        """
        days = max(1, min(int(days), 365))
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        since = since_dt.isoformat(timespec="microseconds")

        if project_id is _ALL:
            pid_sql = "1=1"
            pid_args: tuple[Any, ...] = ()
        elif project_id is None:
            pid_sql = "project_id IS NULL"
            pid_args = ()
        else:
            pid_sql = "project_id = ?"
            pid_args = (project_id,)

        with self._connect() as conn:
            # Open an explicit transaction so concurrent writers don't give
            # us inconsistent aggregate snapshots.
            conn.execute("BEGIN DEFERRED")
            try:
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

                if project_id is _ALL:
                    client_sql = (
                        "SELECT s.client_name, s.client_version, COUNT(*) AS count "
                        "FROM mcp_interactions i JOIN mcp_sessions s "
                        "ON s.session_id = i.session_id "
                        "WHERE i.created_at >= ? "
                        "GROUP BY s.client_name, s.client_version "
                        "ORDER BY count DESC"
                    )
                    client_args: tuple[Any, ...] = (since,)
                elif project_id is None:
                    client_sql = (
                        "SELECT s.client_name, s.client_version, COUNT(*) AS count "
                        "FROM mcp_interactions i JOIN mcp_sessions s "
                        "ON s.session_id = i.session_id "
                        "WHERE i.project_id IS NULL AND i.created_at >= ? "
                        "GROUP BY s.client_name, s.client_version "
                        "ORDER BY count DESC"
                    )
                    client_args = (since,)
                else:
                    client_sql = (
                        "SELECT s.client_name, s.client_version, COUNT(*) AS count "
                        "FROM mcp_interactions i JOIN mcp_sessions s "
                        "ON s.session_id = i.session_id "
                        "WHERE i.project_id = ? AND i.created_at >= ? "
                        "GROUP BY s.client_name, s.client_version "
                        "ORDER BY count DESC"
                    )
                    client_args = (project_id, since)
                client_breakdown = [dict(r) for r in conn.execute(client_sql, client_args).fetchall()]
            finally:
                conn.rollback()  # Deferred read-only txn; rollback releases it.

        calls_per_day: list[dict[str, Any]] = []
        today = datetime.now(timezone.utc).date()
        for offset in range(days - 1, -1, -1):
            d = (today - timedelta(days=offset)).isoformat()
            calls_per_day.append({"day": d, "count": per_day_rows.get(d, 0)})

        # Serialisable project_id representation for templates/JSON output.
        pid_out: str | None
        if project_id is _ALL:
            pid_out = None  # "all" → None in the payload (for back-compat)
        else:
            pid_out = project_id

        return {
            "project_id": pid_out,
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
        """Delete interactions + orphan sessions older than `days` days.

        Sessions are pruned based on last_seen_at (not started_at), so a
        long-lived session that keeps being touched is retained.
        """
        days = max(1, int(days))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="microseconds")
        try:
            with self._connect() as conn:
                deleted_i = conn.execute(
                    "DELETE FROM mcp_interactions WHERE created_at < ?",
                    (cutoff,),
                ).rowcount
                deleted_s = conn.execute(
                    "DELETE FROM mcp_sessions "
                    "WHERE last_seen_at < ? AND session_id NOT IN "
                    "(SELECT DISTINCT session_id FROM mcp_interactions)",
                    (cutoff,),
                ).rowcount
                return {"deleted_interactions": deleted_i or 0,
                        "deleted_sessions": deleted_s or 0}
        except sqlite3.Error as exc:
            print(f"[interaction_log] prune failed: {exc}", file=sys.stderr)
            return {"deleted_interactions": 0, "deleted_sessions": 0}

    def list_unattributed_sessions(self, *, days: int = 30,
                                   limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_ROWS))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="microseconds")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.session_id, s.project_id, s.workspace_path,
                       s.client_name, s.client_version,
                       s.started_at, s.last_seen_at,
                       COUNT(i.id) AS call_count,
                       MAX(i.created_at) AS last_used_at
                  FROM mcp_sessions s
                  LEFT JOIN mcp_interactions i ON i.session_id = s.session_id
                 WHERE s.project_id IS NULL AND s.started_at >= ?
                 GROUP BY s.session_id
                 ORDER BY s.started_at DESC
                 LIMIT ?
                """,
                (since, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ──────────────────────────────────────────────────────────────────
    # Utilities used by the MCP server
    # ──────────────────────────────────────────────────────────────────

    def new_session_id(self) -> str:
        """Allocate a new session id under a lock so concurrent transports
        can't race on it."""
        import uuid
        with self._lock:
            return str(uuid.uuid4())

    def backfill_session_project(self, session_id: str, project_id: str | None) -> None:
        """Set project_id on a session that was recorded before the
        background install finished. No-op if the session already has one."""
        if project_id is None:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE mcp_sessions SET project_id=? "
                    "WHERE session_id=? AND project_id IS NULL",
                    (project_id, session_id),
                )
                conn.execute(
                    "UPDATE mcp_interactions SET project_id=? "
                    "WHERE session_id=? AND project_id IS NULL",
                    (project_id, session_id),
                )
        except sqlite3.Error as exc:
            print(f"[interaction_log] backfill_session_project failed: {exc}",
                  file=sys.stderr)
