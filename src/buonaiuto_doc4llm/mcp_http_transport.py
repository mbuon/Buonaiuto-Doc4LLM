from __future__ import annotations

import json
import sys
import threading
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from buonaiuto_doc4llm.mcp_server import MCPServer

MAX_HTTP_BODY_BYTES = 16 * 1024 * 1024  # 16 MB


@dataclass
class SessionState:
    session_id: str
    project_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def allocate(self, *, session_id: str, project_id: str | None) -> SessionState:
        state = SessionState(session_id=session_id, project_id=project_id)
        with self._lock:
            self._sessions[session_id] = state
        return state

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)

    def update_project(self, session_id: str, project_id: str) -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is not None and state.project_id is None:
                state.project_id = project_id


def create_mcp_http_app(server: "MCPServer"):  # -> FastAPI
    """Build and return a FastAPI application exposing the MCP server over HTTP.

    The lazy import of fastapi keeps the module importable in environments that
    do not have fastapi installed (e.g. when only the stdio MCP transport is used).
    """
    from fastapi import FastAPI
    from fastapi.requests import Request  # noqa: F401 — imported for annotation below
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Buonaiuto Doc4LLM MCP", docs_url=None, redoc_url=None)
    registry = SessionRegistry()

    # We define the routes inside a helper so that the local `Request` import is
    # in the global scope of *this* function — FastAPI resolves annotations from
    # the function's __globals__, which for nested `def` is the enclosing module's
    # globals.  Using get_annotations(eval_str=True) with the local import would
    # fail.  Instead we patch the app's route resolver by declaring the handlers
    # at module scope via exec so they inherit the correct globals, OR we simply
    # avoid PEP 563 deferred evaluation for these route functions by annotating
    # with the actual type object rather than a string.
    #
    # The cleanest approach: wrap the route body in a closure that receives
    # the `Request` type explicitly, bypassing annotation-string resolution.

    async def _get_info():
        return JSONResponse(content={
            "name": "Buonaiuto Doc4LLM",
            "version": "0.1.0",
            "protocolVersion": "2025-03-26",
        })

    # Attach without type annotation so FastAPI does not try to resolve it.
    app.get("/mcp")(_get_info)

    # For the POST handler we need FastAPI to inject the Request object.
    # We achieve this by setting the annotation dict directly on the function
    # object AFTER definition so PEP 563 string conversion is bypassed.
    from fastapi import Request as _Request  # noqa: F811

    async def _post_mcp(request):  # annotation added below
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_HTTP_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": "Request body too large"},
            )

        body = await request.body()
        if len(body) > MAX_HTTP_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": "Request body too large"},
            )

        try:
            rpc = json.loads(body)
        except json.JSONDecodeError as exc:
            return JSONResponse(
                status_code=400,
                content={"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": f"Parse error: {exc}"}},
            )

        method = rpc.get("method", "")
        response_headers: dict[str, str] = {}

        if method == "initialize":
            new_sid = str(_uuid.uuid4())
            params = rpc.get("params") or {}
            server._bootstrap_from_initialize_params(params)
            project_id = server._session_project_id
            state = registry.allocate(session_id=new_sid, project_id=project_id)
            try:
                client_info = params.get("clientInfo") or {}
                server.service.record_mcp_session(
                    session_id=new_sid,
                    project_id=project_id,
                    workspace_path=None,
                    client_name=client_info.get("name"),
                    client_version=client_info.get("version"),
                )
            except Exception as exc:
                print(f"[mcp_http] record_mcp_session failed: {exc}", file=sys.stderr)
            response_headers["Mcp-Session-Id"] = new_sid
            result = server.handle_request(rpc, session_state=state)
        else:
            sid_header = request.headers.get("mcp-session-id")
            if not sid_header:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Missing Mcp-Session-Id header"},
                )
            state = registry.get(sid_header)
            if state is None:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Unknown session: {sid_header}"},
                )
            result = server.handle_request(rpc, session_state=state)

        return JSONResponse(content=result, headers=response_headers)

    # Inject the real Request type as annotation so FastAPI's dependency
    # injection recognises it as the raw request object (not a query param).
    _post_mcp.__annotations__["request"] = _Request

    app.post("/mcp")(_post_mcp)

    return app
