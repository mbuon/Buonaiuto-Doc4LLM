"""Integration tests for the fetch_docs path through DocsHubService and MCPServer."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from buonaiuto_doc4llm.mcp_server import MCPServer
from buonaiuto_doc4llm.service import DocsHubService


# ---------------------------------------------------------------------------
# DocsHubService.fetch_docs
# ---------------------------------------------------------------------------

class TestServiceFetchDocs:
    def test_fetch_docs_all_returns_fetch_results_and_scan_summary(self, tmp_path: Path) -> None:
        service = DocsHubService(tmp_path)
        mock_result = {"technology": "react", "fetched": True, "bytes": 1234}

        with patch("buonaiuto_doc4llm.service.DocsHubService.fetch_docs", wraps=service.fetch_docs) as _:
            with patch("ingestion.http_fetcher.HttpDocFetcher.fetch_all", return_value=[mock_result]):
                result = service.fetch_docs()

        assert "fetch_results" in result
        assert "scan_summary" in result
        assert result["fetch_results"][0]["technology"] == "react"

    def test_fetch_docs_single_technology_calls_fetch_not_fetch_all(self, tmp_path: Path) -> None:
        service = DocsHubService(tmp_path)
        mock_result = {"technology": "nextjs", "fetched": True, "bytes": 500}

        with patch("ingestion.http_fetcher.HttpDocFetcher.fetch", return_value=mock_result) as mock_fetch:
            with patch("ingestion.http_fetcher.HttpDocFetcher.fetch_all") as mock_fetch_all:
                result = service.fetch_docs(technology="nextjs")

        mock_fetch.assert_called_once_with("nextjs")
        mock_fetch_all.assert_not_called()
        assert result["fetch_results"][0]["technology"] == "nextjs"

    def test_fetch_docs_scan_is_called_after_fetch(self, tmp_path: Path) -> None:
        service = DocsHubService(tmp_path)

        with patch("ingestion.http_fetcher.HttpDocFetcher.fetch_all", return_value=[]):
            with patch.object(service, "scan", wraps=service.scan) as mock_scan:
                service.fetch_docs()

        mock_scan.assert_called_once()

    def test_fetch_docs_unknown_technology_raises_value_error(self, tmp_path: Path) -> None:
        service = DocsHubService(tmp_path)

        with patch(
            "ingestion.http_fetcher.HttpDocFetcher.fetch",
            side_effect=ValueError("unknown technology 'nonexistent'"),
        ):
            with pytest.raises(ValueError, match="unknown"):
                service.fetch_docs(technology="nonexistent")

    def test_fetch_docs_http_error_propagates(self, tmp_path: Path) -> None:
        service = DocsHubService(tmp_path)

        with patch(
            "ingestion.http_fetcher.HttpDocFetcher.fetch_all",
            side_effect=RuntimeError("HTTP 503 fetching https://react.dev/llms-full.txt"),
        ):
            with pytest.raises(RuntimeError, match="503"):
                service.fetch_docs()


# ---------------------------------------------------------------------------
# MCPServer fetch_docs tool
# ---------------------------------------------------------------------------

class TestMCPFetchDocsTool:
    def test_fetch_docs_tool_present_in_tools_list(self, tmp_path: Path) -> None:
        server = MCPServer(tmp_path)
        response = server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        tools = response["result"]["tools"]
        names = [t["name"] for t in tools]
        assert "fetch_docs" in names

    def test_fetch_docs_tool_has_optional_technology_parameter(self, tmp_path: Path) -> None:
        server = MCPServer(tmp_path)
        response = server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        tools = {t["name"]: t for t in response["result"]["tools"]}
        schema = tools["fetch_docs"]["inputSchema"]
        assert "technology" in schema["properties"]
        # technology is optional — not in required
        assert "required" not in schema or "technology" not in schema.get("required", [])

    def test_fetch_docs_tool_call_dispatches_to_service(self, tmp_path: Path) -> None:
        server = MCPServer(tmp_path)
        mock_payload = {
            "fetch_results": [{"technology": "react", "fetched": True}],
            "scan_summary": [],
        }

        with patch.object(server.service, "fetch_docs", return_value=mock_payload) as mock_fd:
            response = server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "fetch_docs",
                        "arguments": {"technology": "react"},
                    },
                }
            )

        mock_fd.assert_called_once_with(technology="react")
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["fetch_results"][0]["technology"] == "react"

    def test_fetch_docs_tool_call_without_technology_fetches_all(self, tmp_path: Path) -> None:
        server = MCPServer(tmp_path)
        mock_payload = {"fetch_results": [], "scan_summary": []}

        with patch.object(server.service, "fetch_docs", return_value=mock_payload) as mock_fd:
            server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "fetch_docs", "arguments": {}},
                }
            )

        mock_fd.assert_called_once_with(technology=None)

    def test_fetch_docs_tool_error_returns_jsonrpc_error(self, tmp_path: Path) -> None:
        server = MCPServer(tmp_path)

        with patch.object(
            server.service,
            "fetch_docs",
            side_effect=ValueError("unknown technology 'bogus'"),
        ):
            response = server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "fetch_docs",
                        "arguments": {"technology": "bogus"},
                    },
                }
            )

        assert "error" in response
        assert "bogus" in response["error"]["message"]
