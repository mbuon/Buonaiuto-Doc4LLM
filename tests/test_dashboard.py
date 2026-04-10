"""Tests for the web dashboard."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buonaiuto_doc4llm.dashboard import create_app


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Create a test client with seed data."""
    base_dir = tmp_path / "base"
    # Seed a technology with a doc
    _write(
        base_dir / "docs_center" / "technologies" / "react" / "manifest.json",
        json.dumps({"technology": "react", "version": "19.0", "display_name": "React"}),
    )
    _write(
        base_dir / "docs_center" / "technologies" / "react" / "docs" / "hooks.md",
        "# React Hooks\n\nuseState, useEffect, useRef",
    )
    # Seed a project
    _write(
        base_dir / "docs_center" / "projects" / "myapp.json",
        json.dumps({"project_id": "myapp", "name": "My App", "technologies": ["react"]}),
    )

    app = create_app(base_dir)
    # Trigger initial scan so docs are indexed
    app.state.service.scan()
    app.state.service.sync_projects()

    return TestClient(app)


def test_overview_page(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Buonaiuto" in resp.text
    assert "Overview" in resp.text
    assert "react" in resp.text


def test_technologies_page(client: TestClient) -> None:
    resp = client.get("/technologies")
    assert resp.status_code == 200
    assert "react" in resp.text
    assert "Indexed Libraries" in resp.text


def test_documents_page(client: TestClient) -> None:
    resp = client.get("/documents")
    assert resp.status_code == 200
    assert "hooks.md" in resp.text


def test_documents_page_filter_by_tech(client: TestClient) -> None:
    resp = client.get("/documents?technology=react")
    assert resp.status_code == 200
    assert "hooks.md" in resp.text


def test_documents_page_search(client: TestClient) -> None:
    resp = client.get("/documents?q=hooks")
    assert resp.status_code == 200
    assert "hooks.md" in resp.text


def test_projects_page(client: TestClient) -> None:
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert "myapp" in resp.text
    assert "react" in resp.text


def test_activity_page(client: TestClient) -> None:
    resp = client.get("/activity")
    assert resp.status_code == 200
    assert "Activity" in resp.text
    # Should have the "added" event from scan
    assert "added" in resp.text


def test_activity_filter_by_type(client: TestClient) -> None:
    resp = client.get("/activity?event_type=added")
    assert resp.status_code == 200
    assert "added" in resp.text


def test_schedule_page(client: TestClient) -> None:
    resp = client.get("/schedule")
    assert resp.status_code == 200
    assert "Schedule" in resp.text


def test_api_scan(client: TestClient) -> None:
    resp = client.post("/api/scan")
    assert resp.status_code == 200
    assert "Scan complete" in resp.text


def test_api_read_doc(client: TestClient) -> None:
    resp = client.get("/api/read-doc?technology=react&rel_path=docs/hooks.md")
    assert resp.status_code == 200
    assert "React Hooks" in resp.text


def test_api_ack(client: TestClient) -> None:
    resp = client.post("/api/ack?project_id=myapp")
    assert resp.status_code == 200
    assert "Acknowledged" in resp.text


def test_static_css(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "--bg-primary" in resp.text


def test_static_htmx(client: TestClient) -> None:
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
