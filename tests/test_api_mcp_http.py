from pathlib import Path

import pytest

from api.mcp_http import HostedMCPGateway
from buonaiuto_doc4llm.service import DocsHubService


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_service(tmp_path: Path) -> DocsHubService:
    _write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    _write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    _write(
        tmp_path / "docs_center/technologies/react/docs/hooks.md",
        "# Hooks\n\nHooks let you use state.",
    )
    service = DocsHubService(tmp_path)
    service.scan()
    return service


def test_hosted_gateway_rejects_invalid_api_key(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    gateway = HostedMCPGateway(
        service=service,
        authenticate=lambda api_key: None,
    )
    with pytest.raises(PermissionError, match="Invalid API key"):
        gateway.query(
            api_key="bad",
            query_text="hooks",
            libraries=[{"id": "react", "version": "19.0"}],
            stream=False,
        )


def test_hosted_gateway_streams_sse_events(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    gateway = HostedMCPGateway(
        service=service,
        authenticate=lambda api_key: "local" if api_key == "good" else None,
    )

    events = gateway.query(
        api_key="good",
        query_text="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        stream=True,
        trace_id="trace-sse-123",
    )

    assert events[0]["event"] == "library_resolved"
    assert any(event["event"] == "chunk" for event in events)
    assert events[-1]["event"] == "done"
    assert all(event["data"]["trace_id"] == "trace-sse-123" for event in events)


def test_hosted_gateway_returns_generated_trace_id_in_payload(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    gateway = HostedMCPGateway(
        service=service,
        authenticate=lambda api_key: "local" if api_key == "good" else None,
    )

    payload = gateway.query(
        api_key="good",
        query_text="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        stream=False,
    )

    assert isinstance(payload["trace_id"], str)
    assert payload["trace_id"]
    assert payload["results"][0]["trace_id"] == payload["trace_id"]
