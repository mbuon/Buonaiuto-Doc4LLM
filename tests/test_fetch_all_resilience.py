"""Tests for fetch_all() resilience — per-technology errors must not abort the whole run."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingestion.http_fetcher import HttpDocFetcher
from ingestion.registry_loader import load_registry


def _make_fetcher_two_libs(tmp_path: Path) -> HttpDocFetcher:
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
    return HttpDocFetcher(
        base_dir=base_dir,
        db_path=base_dir / "state" / "docs_hub.db",
        registry=mappings,
    )


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "# Docs\nContent."
    resp.content = b"# Docs\nContent."
    resp.headers = {"ETag": '"v1"'}
    return resp


def _error_response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "error"
    resp.headers = {}
    return resp


class TestFetchAllResilience:
    def test_fetch_all_continues_after_one_http_error(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher_two_libs(tmp_path)

        responses = iter([_error_response(503), _ok_response()])

        with patch("requests.get", side_effect=lambda *a, **kw: next(responses)):
            results = fetcher.fetch_all()

        assert len(results) == 2
        errors = [r for r in results if r.get("error")]
        successes = [r for r in results if r.get("fetched") is True]
        assert len(errors) == 1
        assert len(successes) == 1

    def test_fetch_all_error_result_contains_technology_and_message(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher_two_libs(tmp_path)

        import requests as req_lib
        responses = iter([
            req_lib.ConnectionError("refused"),
            _ok_response(),
        ])

        def fake_get(*args: object, **kwargs: object) -> MagicMock:
            r = next(responses)
            if isinstance(r, Exception):
                raise r
            return r  # type: ignore[return-value]

        # ConnectionError is raised, not returned — simulate properly
        call_count = 0

        def side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise req_lib.ConnectionError("refused")
            return _ok_response()

        with patch("requests.get", side_effect=side_effect):
            results = fetcher.fetch_all()

        error_result = next(r for r in results if r.get("error"))
        assert "technology" in error_result
        assert error_result["fetched"] is False
        assert "message" in error_result

    def test_fetch_all_all_succeed_returns_all_fetched_true(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher_two_libs(tmp_path)

        with patch("requests.get", return_value=_ok_response()):
            results = fetcher.fetch_all()

        assert all(r["fetched"] is True for r in results)
        assert len(results) == 2

    def test_fetch_all_all_fail_returns_all_error_results(self, tmp_path: Path) -> None:
        fetcher = _make_fetcher_two_libs(tmp_path)

        with patch("requests.get", return_value=_error_response(404)):
            results = fetcher.fetch_all()

        assert len(results) == 2
        assert all(r.get("error") for r in results)
        assert all(r["fetched"] is False for r in results)

    def test_fetch_all_partial_failure_scan_still_runs(self, tmp_path: Path) -> None:
        """fetch_docs() must call scan() even when some fetches fail."""
        from buonaiuto_doc4llm.service import DocsHubService

        # Use a controlled registry with one source per library so 503
        # exhausts all sources for the first tech and triggers an error.
        registry_path = tmp_path / "registry.json"
        import json as _json
        registry = {
            "version": 1,
            "libraries": [
                {
                    "library_id": "fail-lib",
                    "display_name": "Fail Lib",
                    "package_names": ["fail-lib"],
                    "sources": ["https://fail.example.com/llms.txt"],
                },
                {
                    "library_id": "ok-lib",
                    "display_name": "OK Lib",
                    "package_names": ["ok-lib"],
                    "sources": ["https://ok.example.com/llms.txt"],
                },
            ],
        }
        registry_path.write_text(_json.dumps(registry), encoding="utf-8")

        service = DocsHubService(tmp_path)
        call_count = 0

        def side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            url = args[0] if args else kwargs.get("url", "")
            if "fail.example.com" in str(url):
                return _error_response(503)
            return _ok_response()

        with patch("requests.get", side_effect=side_effect):
            with patch.object(service, "scan", wraps=service.scan) as mock_scan:
                result = service.fetch_docs(registry_path=registry_path)

        mock_scan.assert_called_once()
        errors = [r for r in result["fetch_results"] if r.get("error")]
        assert len(errors) >= 1
