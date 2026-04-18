from __future__ import annotations

from pathlib import Path

import pytest

from buonaiuto_doc4llm import scheduler


@pytest.fixture(autouse=True)
def _force_linux(monkeypatch):
    # Force crontab path for deterministic string generation
    monkeypatch.setattr("platform.system", lambda: "Linux")


def test_crontab_includes_fetch_and_refresh_entries(monkeypatch, tmp_path: Path) -> None:
    captured: list[str] = []

    def fake_run(cmd, input=None, text=None, check=None, capture_output=None):
        if cmd == ["crontab", "-"]:
            captured.append(input)
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()
        if cmd == ["crontab", "-l"]:
            class R:
                returncode = 1
                stdout = ""
                stderr = ""
            return R()
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = scheduler.install_schedule(tmp_path, hour=4, minute=0)
    assert result["installed"] is True
    assert captured, "crontab stdin must have been written"
    content = captured[-1]
    assert "fetch" in content
    assert "refresh-active" in content
    assert "*/3" in content  # every 3 days
    assert scheduler._CRON_MARKER_FETCH in content
    assert scheduler._CRON_MARKER_REFRESH in content


def test_crontab_status_reports_both_entries(monkeypatch, tmp_path: Path) -> None:
    # Pretend both markers are in crontab
    content = (
        "0 4 * * * PYTHONPATH=/x /p -m buonaiuto_doc4llm --base-dir /x fetch "
        f"{scheduler._CRON_MARKER_FETCH}\n"
        "15 4 */3 * * PYTHONPATH=/x /p -m buonaiuto_doc4llm --base-dir /x refresh-active "
        f"{scheduler._CRON_MARKER_REFRESH}\n"
    )

    def fake_run(cmd, input=None, text=None, check=None, capture_output=None):
        if cmd == ["crontab", "-l"]:
            class R:
                returncode = 0
                stdout = content
                stderr = ""
            return R()
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    status = scheduler.schedule_status()
    assert status["installed"] is True
    names = {e["name"]: e["installed"] for e in status["entries"]}
    assert names["daily-fetch"] is True
    assert names["refresh-active"] is True
