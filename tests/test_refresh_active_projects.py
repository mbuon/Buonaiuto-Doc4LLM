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

    # Use a flag (not pytest.fail) so the assertion survives the except
    # Exception handler inside refresh_active_projects.
    calls: list[dict] = []
    monkeypatch.setattr(svc, "install_project",
                        lambda **kw: calls.append(kw))
    monkeypatch.setattr(
        "buonaiuto_doc4llm.refresh_active._fetch_technology",
        lambda svc, tech: {"technology": tech, "status": "ok"},
    )
    result = refresh_active_projects(svc, days=30, dry_run=False)
    assert calls == [], f"install_project must not run but was called with: {calls}"
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


def test_refresh_active_cli_dry_run(tmp_path: Path) -> None:
    import subprocess
    import sys as _sys

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
