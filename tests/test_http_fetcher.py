"""Tests for ingestion.http_fetcher — failing first, then passing after implementation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingestion.http_fetcher import HttpDocFetcher
from ingestion.registry_loader import load_registry


def _make_registry_file(tmp_path: Path, library_id: str = "react", url: str = "https://react.dev/llms-full.txt") -> Path:
    registry = {
        "version": 1,
        "libraries": [
            {
                "library_id": library_id,
                "display_name": library_id.title(),
                "package_names": [library_id],
                "sources": [url],
            }
        ],
    }
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(registry), encoding="utf-8")
    return p


def _make_fetcher(tmp_path: Path, library_id: str = "react", url: str = "https://react.dev/llms-full.txt") -> HttpDocFetcher:
    registry_path = _make_registry_file(tmp_path, library_id, url)
    mappings = load_registry(registry_path)
    base_dir = tmp_path
    (base_dir / "docs_center" / "technologies").mkdir(parents=True)
    (base_dir / "state").mkdir(parents=True)
    db_path = base_dir / "state" / "docs_hub.db"
    return HttpDocFetcher(base_dir=base_dir, db_path=db_path, registry=mappings)


def _make_response(status_code: int, text: str = "", etag: str | None = None, last_modified: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = {}
    if etag:
        resp.headers["ETag"] = etag
    if last_modified:
        resp.headers["Last-Modified"] = last_modified
    return resp


class TestFetchStateTable:
    def test_fetch_state_table_created_on_init(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        with sqlite3.connect(fetcher.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "fetch_state" in tables


class TestFetchWritesFiles:
    def test_fetch_writes_content_to_technology_dir(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        resp = _make_response(200, "# React Docs\nSome content here.", etag='"abc123"')

        with patch("requests.get", return_value=resp):
            result = fetcher.fetch("react")

        dest = tmp_path / "docs_center" / "technologies" / "react" / "llms-full.txt"
        assert dest.exists()
        assert "React Docs" in dest.read_text(encoding="utf-8")
        assert result["fetched"] is True
        assert result["technology"] == "react"

    def test_fetch_creates_manifest_json(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        resp = _make_response(200, "# React\nContent.", etag='"v1"')

        with patch("requests.get", return_value=resp):
            fetcher.fetch("react")

        manifest = tmp_path / "docs_center" / "technologies" / "react" / "manifest.json"
        assert manifest.exists()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["display_name"] == "React"

    def test_fetch_saves_fetch_state_after_success(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        resp = _make_response(200, "# Docs", etag='"etag-v1"', last_modified="Thu, 01 Jan 2026 00:00:00 GMT")

        with patch("requests.get", return_value=resp):
            fetcher.fetch("react")

        with sqlite3.connect(fetcher.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM fetch_state WHERE technology = 'react'").fetchone()

        assert row is not None
        assert row["etag"] == '"etag-v1"'
        assert row["last_modified"] == "Thu, 01 Jan 2026 00:00:00 GMT"
        assert row["last_status_code"] == 200


class TestFetchConditional:
    def test_fetch_skips_when_304_not_modified(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        # First fetch to store state
        resp_200 = _make_response(200, "# React", etag='"abc"')
        with patch("requests.get", return_value=resp_200):
            fetcher.fetch("react")

        # Second fetch with 304
        resp_304 = _make_response(304)
        with patch("requests.get", return_value=resp_304) as mock_get:
            result = fetcher.fetch("react")

        assert result["fetched"] is False
        assert result["reason"] == "not_modified"

    def test_fetch_sends_etag_header_on_second_request(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        resp_200 = _make_response(200, "# React", etag='"stored-etag"')
        with patch("requests.get", return_value=resp_200):
            fetcher.fetch("react")

        resp_304 = _make_response(304)
        with patch("requests.get", return_value=resp_304) as mock_get:
            fetcher.fetch("react")

        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers.get("If-None-Match") == '"stored-etag"'


class TestFetchErrors:
    def test_fetch_raises_on_http_4xx(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        resp = _make_response(404, "Not Found")

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="404"):
                fetcher.fetch("react")

    def test_fetch_raises_on_http_5xx(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        resp = _make_response(503, "Service Unavailable")

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="503"):
                fetcher.fetch("react")

    def test_fetch_raises_on_connection_error(self, tmp_path: Path) -> None:
        import requests as req_lib
        fetcher = _make_fetcher(tmp_path)

        with patch("requests.get", side_effect=req_lib.ConnectionError("refused")):
            with pytest.raises(RuntimeError, match="connect"):
                fetcher.fetch("react")

    def test_fetch_unknown_technology_raises_value_error(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        with pytest.raises(ValueError, match="unknown"):
            fetcher.fetch("nonexistent-library")


class TestFetchAll:
    def test_fetch_all_returns_result_per_library(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"
        registry = {
            "version": 1,
            "libraries": [
                {
                    "library_id": "react",
                    "display_name": "React",
                    "package_names": ["react"],
                    "sources": ["https://react.dev/llms-full.txt"],
                },
                {
                    "library_id": "nextjs",
                    "display_name": "Next.js",
                    "package_names": ["next"],
                    "sources": ["https://nextjs.org/llms-full.txt"],
                },
            ],
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        mappings = load_registry(registry_path)
        base_dir = tmp_path / "hub"
        (base_dir / "docs_center" / "technologies").mkdir(parents=True)
        (base_dir / "state").mkdir(parents=True)
        fetcher = HttpDocFetcher(
            base_dir=base_dir,
            db_path=base_dir / "state" / "docs_hub.db",
            registry=mappings,
        )

        resp = _make_response(200, "# Docs", etag='"v1"')
        with patch("requests.get", return_value=resp):
            results = fetcher.fetch_all()

        assert len(results) == 2
        ids = {r["technology"] for r in results}
        assert ids == {"react", "nextjs"}
