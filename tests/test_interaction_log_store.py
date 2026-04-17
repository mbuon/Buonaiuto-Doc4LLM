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
