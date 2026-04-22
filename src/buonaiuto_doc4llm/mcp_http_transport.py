import json
import sys
import threading
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

try:
    from fastapi import FastAPI, Request as _Request
    from fastapi.responses import JSONResponse
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

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

    FastAPI is imported at module level (with a try/except so the module stays
    importable without FastAPI).  All annotations in this file are runtime-valid
    Python 3.10+ — no PEP 563 deferred evaluation needed.
    """
    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "fastapi is required for the HTTP transport. "
            "Install it with: pip install fastapi[all]"
        )

    app = FastAPI(title="Buonaiuto Doc4LLM MCP", docs_url=None, redoc_url=None)
    registry = SessionRegistry()

    async def _get_info():
        return JSONResponse(content={
            "name": "Buonaiuto Doc4LLM",
            "version": "0.1.0",
            "protocolVersion": "2025-03-26",
        })

    app.get("/mcp")(_get_info)

    async def _post_mcp(request: _Request):
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
            # Let handle_request run _bootstrap_from_initialize_params exactly
            # once.  Pass a temporary state so _call_tool uses the HTTP session
            # id if any tool is called during initialize (not expected, but safe).
            temp_state = SessionState(session_id=new_sid, project_id=None)
            result = server.handle_request(rpc, session_state=temp_state)
            # Extract project_id from the bootstrap summary returned in the result.
            bootstrap = (result.get("result") or {}).get("bootstrap") or {}
            project_id = bootstrap.get("project_id")
            state = registry.allocate(session_id=new_sid, project_id=project_id)
            # Record the HTTP session row under the HTTP session id.
            try:
                params = rpc.get("params") or {}
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
        else:
            sid_header = request.headers.get("mcp-session-id")
            if not sid_header:
                rpc_id = rpc.get("id") if isinstance(rpc, dict) else None
                return JSONResponse(
                    status_code=400,
                    content={"jsonrpc": "2.0", "id": rpc_id,
                             "error": {"code": -32600, "message": "Missing Mcp-Session-Id header"}},
                )
            state = registry.get(sid_header)
            if state is None:
                rpc_id = rpc.get("id") if isinstance(rpc, dict) else None
                return JSONResponse(
                    status_code=400,
                    content={"jsonrpc": "2.0", "id": rpc_id,
                             "error": {"code": -32600, "message": f"Unknown session: {sid_header}"}},
                )
            result = server.handle_request(rpc, session_state=state)

        # Notifications return {} — no body, HTTP 202.
        if not result:
            from fastapi.responses import Response
            return Response(status_code=202, headers=response_headers)
        return JSONResponse(content=result, headers=response_headers)

    app.post("/mcp")(_post_mcp)

    return app
