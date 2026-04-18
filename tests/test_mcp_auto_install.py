from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from buonaiuto_doc4llm.project_bootstrap import (
    ensure_project_installed,
    extract_workspace_path,
    resolve_project_id_for_basename,
    _install_in_flight,
)
from buonaiuto_doc4llm.service import DocsHubService


# ─── Task 6: workspace extraction + basename resolution ───────────────────

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


# ─── Task 7: ensure_project_installed with freshness + background ─────────

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


def test_ensure_project_installed_stale_file_triggers_install(svc: DocsHubService, monkeypatch) -> None:
    _make_project_file(svc, "my-app", mtime_offset_seconds=-(30 * 3600))  # 30h old
    called = []
    monkeypatch.setattr(
        svc, "install_project",
        lambda **kw: called.append(kw) or {"project_id": "my-app"},
    )
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/my-app"), wait=True)
    assert pid == "my-app"
    assert len(called) == 1


def test_ensure_project_installed_first_time_installs(svc: DocsHubService, monkeypatch) -> None:
    called = []

    def fake_install(**kw):
        called.append(kw)
        return {"project_id": "brand-new"}

    monkeypatch.setattr(svc, "install_project", fake_install)
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/brand-new"), wait=True)
    assert pid == "brand-new"
    assert called[0]["project_root"] == Path("/tmp/brand-new")


def test_ensure_project_installed_runs_in_background(svc: DocsHubService, monkeypatch) -> None:
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


def test_ensure_project_installed_deduplicates_concurrent_calls(svc: DocsHubService, monkeypatch) -> None:
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


def test_ensure_project_installed_install_failure_logs_and_returns_none(svc: DocsHubService, monkeypatch, capsys) -> None:
    def boom(**kw):
        raise RuntimeError("no network")

    monkeypatch.setattr(svc, "install_project", boom)
    pid = ensure_project_installed(svc, workspace_path=Path("/tmp/fail"), wait=True)
    assert pid is None
    assert "no network" in capsys.readouterr().err


def test_ensure_project_installed_none_when_no_path(svc: DocsHubService) -> None:
    assert ensure_project_installed(svc, workspace_path=None, wait=True) is None


def test_ensure_project_installed_clears_in_flight_on_success(svc: DocsHubService, monkeypatch) -> None:
    monkeypatch.setattr(svc, "install_project", lambda **kw: {"project_id": "cleanup"})
    ensure_project_installed(svc, workspace_path=Path("/tmp/cleanup"), wait=True)
    assert "/tmp/cleanup" not in _install_in_flight


# ─── Task 8: persist workspace_path in project file ─────────────────────

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


# ─── Task 9: MCPServer session pinning + tool-call wrapper ──────────────

from buonaiuto_doc4llm.mcp_server import MCPServer


def test_mcp_server_pins_session_on_initialize(tmp_path) -> None:
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
