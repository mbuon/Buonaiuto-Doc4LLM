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
