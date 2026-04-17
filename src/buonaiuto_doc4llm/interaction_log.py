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
