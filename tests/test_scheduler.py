"""Tests for the daily fetch scheduler."""
from __future__ import annotations

from pathlib import Path

from buonaiuto_doc4llm.scheduler import (
    LAUNCHD_LABEL,
    _build_plist,
    _cron_line,
    _CRON_MARKER,
)


def test_build_plist_contains_fetch_command(tmp_path: Path) -> None:
    plist = _build_plist(tmp_path, hour=4, minute=30)
    assert LAUNCHD_LABEL in plist
    assert "<string>fetch</string>" in plist
    assert "<string>--base-dir</string>" in plist
    assert str(tmp_path) in plist
    assert "<integer>4</integer>" in plist
    assert "<integer>30</integer>" in plist
    assert "PYTHONPATH" in plist


def test_build_plist_log_paths(tmp_path: Path) -> None:
    plist = _build_plist(tmp_path, hour=0, minute=0)
    expected_log = str(tmp_path / "state" / "logs" / "daily-fetch.stdout.log")
    assert expected_log in plist


def test_cron_line_format(tmp_path: Path) -> None:
    line = _cron_line(tmp_path, hour=3, minute=15)
    assert line.startswith("15 3 * * *")
    assert "fetch" in line
    assert str(tmp_path) in line
    assert _CRON_MARKER in line


def test_cron_line_includes_pythonpath(tmp_path: Path) -> None:
    line = _cron_line(tmp_path, hour=0, minute=0)
    assert "PYTHONPATH=" in line
