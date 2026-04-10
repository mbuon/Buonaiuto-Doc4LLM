"""Tests for the auto-discovery module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ingestion.doc_discovery import (
    _add_to_registry,
    _extract_domain,
    _parse_google_results,
    _to_base_url,
    discover_doc_sources,
    discover_and_register,
)


# ------------------------------------------------------------------
# URL helpers
# ------------------------------------------------------------------

def test_extract_domain_strips_www() -> None:
    assert _extract_domain("https://www.stripe.com/docs") == "stripe.com"


def test_extract_domain_bare() -> None:
    assert _extract_domain("https://react.dev/learn") == "react.dev"


def test_extract_domain_returns_none_for_garbage() -> None:
    assert _extract_domain("not-a-url") is None


def test_to_base_url() -> None:
    assert _to_base_url("https://docs.django.com/en/5.0/intro/") == "https://docs.django.com"


# ------------------------------------------------------------------
# Google result parsing
# ------------------------------------------------------------------

def test_parse_google_results_extracts_urls() -> None:
    html = '''
    <a href="/url?q=https://docs.djangoproject.com/en/5.0/&amp;sa=U">Django</a>
    <a href="/url?q=https://stackoverflow.com/questions/123&amp;sa=U">SO</a>
    <a href="/url?q=https://djangoproject.com/download/&amp;sa=U">Download</a>
    '''
    urls = _parse_google_results(html)
    # stackoverflow should be filtered out
    assert "https://docs.djangoproject.com/en/5.0/" in urls
    assert "https://djangoproject.com/download/" in urls
    assert not any("stackoverflow" in u for u in urls)


# ------------------------------------------------------------------
# Registry persistence
# ------------------------------------------------------------------

def test_add_to_registry_creates_new_entry(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"version": 1, "libraries": []}))

    result = _add_to_registry(
        technology="django",
        sources=["https://docs.djangoproject.com/llms-full.txt"],
        package_names=["django"],
        registry_path=registry,
    )

    assert result is True
    data = json.loads(registry.read_text())
    assert len(data["libraries"]) == 1
    assert data["libraries"][0]["library_id"] == "django"
    assert data["libraries"][0]["sources"] == ["https://docs.djangoproject.com/llms-full.txt"]


def test_add_to_registry_skips_duplicates(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "version": 1,
        "libraries": [{"library_id": "react", "package_names": ["react"], "sources": []}],
    }))

    result = _add_to_registry(
        technology="react",
        sources=["https://react.dev/llms-full.txt"],
        package_names=["react"],
        registry_path=registry,
    )

    assert result is False
    data = json.loads(registry.read_text())
    assert len(data["libraries"]) == 1


# ------------------------------------------------------------------
# End-to-end discovery (mocked search + probe)
# ------------------------------------------------------------------

def test_discover_doc_sources_with_mock_search() -> None:
    """Discovery finds llms.txt when search returns a docs domain."""
    def fake_search(query: str) -> list[str]:
        return ["https://docs.djangoproject.com/en/5.0/"]

    with patch("ingestion.doc_discovery._requests") as mock_requests:
        # HEAD probe returns 200 for llms-full.txt
        mock_resp = mock_requests.head.return_value
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/plain"}

        sources = discover_doc_sources("django", search_fn=fake_search)

    assert len(sources) > 0
    assert any("llms-full.txt" in s for s in sources)


def test_discover_doc_sources_returns_empty_when_no_llms_txt() -> None:
    """Discovery returns empty when probing finds no llms.txt."""
    def fake_search(query: str) -> list[str]:
        return ["https://some-docs-site.com/getting-started"]

    with patch("ingestion.doc_discovery._requests") as mock_requests:
        # HEAD probe returns 404
        mock_resp = mock_requests.head.return_value
        mock_resp.status_code = 404
        mock_resp.headers = {}

        sources = discover_doc_sources("obscure-lib", search_fn=fake_search)

    assert sources == []


def test_discover_and_register_persists_to_registry(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"version": 1, "libraries": []}))

    def fake_search(query: str) -> list[str]:
        return ["https://docs.example.com/guide"]

    with patch("ingestion.doc_discovery._requests") as mock_requests:
        mock_resp = mock_requests.head.return_value
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/plain"}

        result = discover_and_register(
            technology="example-lib",
            registry_path=registry,
            package_names=["example-lib"],
            search_fn=fake_search,
        )

    assert result["discovered"] is True
    assert result["registered"] is True

    data = json.loads(registry.read_text())
    assert any(lib["library_id"] == "example-lib" for lib in data["libraries"])


def test_discover_and_register_not_found(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"version": 1, "libraries": []}))

    def fake_search(query: str) -> list[str]:
        return []

    result = discover_and_register(
        technology="nonexistent",
        registry_path=registry,
        search_fn=fake_search,
    )

    assert result["discovered"] is False
    assert result["registered"] is False
