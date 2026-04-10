from __future__ import annotations

from typing import Callable

from buonaiuto_doc4llm.service import DocsHubService
from telemetry import ensure_trace_id


class HostedMCPGateway:
    def __init__(
        self,
        service: DocsHubService,
        authenticate: Callable[[str], str | None],
    ):
        self.service = service
        self.authenticate = authenticate

    def query(
        self,
        api_key: str,
        query_text: str,
        libraries: list[dict] | None = None,
        limit: int = 5,
        stream: bool = False,
        trace_id: str | None = None,
    ) -> dict | list[dict]:
        workspace_id = self.authenticate(api_key)
        if workspace_id is None:
            raise PermissionError("Invalid API key")

        resolved_trace_id = ensure_trace_id(trace_id)
        payload = self.service.search_documentation(
            query=query_text,
            libraries=libraries,
            limit=limit,
            workspace_id=workspace_id,
            trace_id=resolved_trace_id,
        )
        if not stream:
            return payload
        return self._to_sse_events(payload)

    @staticmethod
    def _to_sse_events(payload: dict) -> list[dict]:
        trace_id = payload["trace_id"]
        events: list[dict] = [
            {
                "event": "library_resolved",
                "data": {
                    "library_id": payload.get("library_id"),
                    "version": payload.get("version"),
                    "retrieval_mode": payload.get("retrieval_mode"),
                    "trace_id": trace_id,
                },
            }
        ]

        results = payload.get("results", [])
        for index, chunk in enumerate(results):
            events.append(
                {
                    "event": "chunk",
                    "data": {
                        "chunk_id": index,
                        "library_id": chunk.get("technology"),
                        "version": chunk.get("version"),
                        "source_path": chunk.get("rel_path"),
                        "score": chunk.get("score", 0.0),
                        "text": chunk.get("snippet", ""),
                        "trace_id": trace_id,
                    },
                }
            )

        events.append(
            {
                "event": "done",
                "data": {
                    "total_chunks": len(results),
                    "trace_id": trace_id,
                },
            }
        )
        return events
