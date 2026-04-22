# MCP Streamable HTTP Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MCP Streamable HTTP transport so Claude Desktop and claude.ai can connect to the local MCP server by URL (`http://127.0.0.1:8421/mcp`) instead of launching a subprocess.

**Architecture:** A new `mcp_http_transport.py` module wraps the existing `MCPServer.handle_request()` behind a FastAPI `POST /mcp` endpoint. Per-session state is tracked in a thread-safe `SessionRegistry` keyed by UUID, injected into `handle_request()` via an optional `session_state` parameter. The stdio path is untouched.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn (already used by dashboard), pytest + FastAPI TestClient.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/buonaiuto_doc4llm/mcp_http_transport.py` | Create | `SessionState`, `SessionRegistry`, `create_mcp_http_app()` |
| `src/buonaiuto_doc4llm/mcp_server.py` | Modify | `handle_request()` + `_call_tool()` accept optional `session_state` |
| `src/buonaiuto_doc4llm/__main__.py` | Modify | `serve --http/--http-host/--http-port` flags; new `serve-http` subcommand |
| `run.sh` | Modify | Options 5 and 6 |
| `run.bat` | Modify | Options 5 and 6 |
| `README.md` | Modify | HTTP transport section, updated client configs, launcher table, CLI reference, roadmap |
| `CLAUDE.md` | Modify | HTTP transport section, updated commands |
| `docs/architecture/plan.md` | Modify | Mark HTTP transport as implemented |
| `tests/test_mcp_http_transport.py` | Create | Unit + integration tests for HTTP transport |

---

## Task 1: `SessionState` and `SessionRegistry`

**Files:**
- Create: `src/buonaiuto_doc4llm/mcp_http_transport.py`
- Create: `tests/test_mcp_http_transport.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_http_transport.py
from __future__ import annotations
import threading
from buonaiuto_doc4llm.mcp_http_transport import SessionRegistry, SessionState


def test_allocate_returns_session_state():
    reg = SessionRegistry()
    state = reg.allocate(session_id="abc-123", project_id=None)
    assert isinstance(state, SessionState)
    assert state.session_id == "abc-123"
    assert state.project_id is None


def test_get_returns_allocated_session():
    reg = SessionRegistry()
    reg.allocate(session_id="abc-123", project_id="proj-1")
    state = reg.get("abc-123")
    assert state is not None
    assert state.project_id == "proj-1"


def test_get_unknown_session_returns_none():
    reg = SessionRegistry()
    assert reg.get("does-not-exist") is None


def test_thread_safe_allocation():
    reg = SessionRegistry()
    ids = [f"sid-{i}" for i in range(50)]
    results: list[SessionState] = []
    lock = threading.Lock()

    def allocate(sid: str) -> None:
        state = reg.allocate(session_id=sid, project_id=None)
        with lock:
            results.append(state)

    threads = [threading.Thread(target=allocate, args=(sid,)) for sid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 50
    assert len({s.session_id for s in results}) == 50
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/massimo/Projects_Massimo/Documentation_LLMs
pytest tests/test_mcp_http_transport.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'buonaiuto_doc4llm.mcp_http_transport'`

- [ ] **Step 3: Create `mcp_http_transport.py` with `SessionState` and `SessionRegistry`**

```python
# src/buonaiuto_doc4llm/mcp_http_transport.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_mcp_http_transport.py::test_allocate_returns_session_state \
       tests/test_mcp_http_transport.py::test_get_returns_allocated_session \
       tests/test_mcp_http_transport.py::test_get_unknown_session_returns_none \
       tests/test_mcp_http_transport.py::test_thread_safe_allocation -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/buonaiuto_doc4llm/mcp_http_transport.py tests/test_mcp_http_transport.py
git commit -m "feat: add SessionState and SessionRegistry for HTTP transport"
```

---

## Task 2: `MCPServer.handle_request()` accepts `session_state`

**Files:**
- Modify: `src/buonaiuto_doc4llm/mcp_server.py:75-165` (handle_request) and `:534-584` (_call_tool)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_http_transport.py`:

```python
from pathlib import Path
from buonaiuto_doc4llm.mcp_server import MCPServer
from buonaiuto_doc4llm.mcp_http_transport import SessionState, SessionRegistry


def test_handle_request_uses_session_state_for_tool_logging(tmp_path: Path):
    server = MCPServer(tmp_path)
    server.service.scan()
    state = SessionState(session_id="http-session-1", project_id=None)

    # tools/list should work with session_state passed
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        session_state=state,
    )
    assert "result" in response
    assert "tools" in response["result"]


def test_handle_request_without_session_state_still_works(tmp_path: Path):
    server = MCPServer(tmp_path)
    server.service.scan()

    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert "result" in response
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_mcp_http_transport.py::test_handle_request_uses_session_state_for_tool_logging \
       tests/test_mcp_http_transport.py::test_handle_request_without_session_state_still_works -v
```

Expected: `TypeError: handle_request() got an unexpected keyword argument 'session_state'`

- [ ] **Step 3: Update `handle_request()` signature**

In `src/buonaiuto_doc4llm/mcp_server.py`, change the `handle_request` signature and the `tools/call` dispatch to pass `session_state` down to `_call_tool`:

```python
# Change line 75 from:
def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
# To:
def handle_request(self, request: dict[str, Any], session_state: Any = None) -> dict[str, Any]:
```

Then change the `tools/call` block (line 97-109) from:
```python
        elif method == "tools/call":
            try:
                result = self._call_tool(params["name"], params.get("arguments", {}))
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32000,
                        "message": str(exc),
                        "data": traceback.format_exc(),
                    },
                }
```
To:
```python
        elif method == "tools/call":
            try:
                result = self._call_tool(
                    params["name"],
                    params.get("arguments", {}),
                    session_state=session_state,
                )
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32000,
                        "message": str(exc),
                        "data": traceback.format_exc(),
                    },
                }
```

- [ ] **Step 4: Update `_call_tool()` signature and logging**

In `src/buonaiuto_doc4llm/mcp_server.py`, change `_call_tool` (line 534) from:
```python
    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        session_id = self._session_id
        session_project_id = self._session_project_id
```
To:
```python
    def _call_tool(self, name: str, arguments: dict[str, Any], session_state: Any = None) -> dict[str, Any]:
        started = time.monotonic()
        if session_state is not None:
            session_id = session_state.session_id
            session_project_id = session_state.project_id
        else:
            session_id = self._session_id
            session_project_id = self._session_project_id
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_mcp_http_transport.py::test_handle_request_uses_session_state_for_tool_logging \
       tests/test_mcp_http_transport.py::test_handle_request_without_session_state_still_works -v
```

Expected: 2 passed

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
pytest --tb=short -q
```

Expected: all existing tests pass

- [ ] **Step 7: Commit**

```bash
git add src/buonaiuto_doc4llm/mcp_server.py tests/test_mcp_http_transport.py
git commit -m "feat: handle_request and _call_tool accept optional session_state"
```

---

## Task 3: `create_mcp_http_app()` — FastAPI HTTP endpoint

**Files:**
- Modify: `src/buonaiuto_doc4llm/mcp_http_transport.py`
- Modify: `tests/test_mcp_http_transport.py`

- [ ] **Step 1: Write the failing integration tests**

Add to `tests/test_mcp_http_transport.py`:

```python
from fastapi.testclient import TestClient


def test_get_mcp_returns_server_info(tmp_path: Path):
    server = MCPServer(tmp_path)
    app = create_mcp_http_app(server)
    client = TestClient(app)
    resp = client.get("/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Buonaiuto Doc4LLM"
    assert "version" in data


def test_initialize_returns_session_header(tmp_path: Path):
    server = MCPServer(tmp_path)
    app = create_mcp_http_app(server)
    client = TestClient(app)
    resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "test", "version": "0.1"}},
    })
    assert resp.status_code == 200
    assert "Mcp-Session-Id" in resp.headers
    data = resp.json()
    assert data["result"]["protocolVersion"] == "2025-03-26"


def test_tools_list_requires_session_header(tmp_path: Path):
    server = MCPServer(tmp_path)
    app = create_mcp_http_app(server)
    client = TestClient(app)
    resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    })
    assert resp.status_code == 400


def test_tools_list_with_valid_session(tmp_path: Path):
    server = MCPServer(tmp_path)
    app = create_mcp_http_app(server)
    client = TestClient(app)

    # initialize first
    init_resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "test", "version": "0.1"}},
    })
    session_id = init_resp.headers["Mcp-Session-Id"]

    # tools/list with session
    resp = client.post("/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers={"Mcp-Session-Id": session_id},
    )
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert any(t["name"] == "search_documentation" for t in tools)


def test_unknown_session_id_returns_400(tmp_path: Path):
    server = MCPServer(tmp_path)
    app = create_mcp_http_app(server)
    client = TestClient(app)
    resp = client.post("/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Mcp-Session-Id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 400


def test_oversized_body_rejected(tmp_path: Path):
    server = MCPServer(tmp_path)
    app = create_mcp_http_app(server)
    client = TestClient(app)
    # Build a payload larger than 16 MB
    big = "x" * (17 * 1024 * 1024)
    resp = client.post("/mcp",
        content=big,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
```

Also add the import at top of test file:
```python
from buonaiuto_doc4llm.mcp_http_transport import (
    SessionRegistry, SessionState, create_mcp_http_app,
)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_mcp_http_transport.py::test_get_mcp_returns_server_info \
       tests/test_mcp_http_transport.py::test_initialize_returns_session_header \
       tests/test_mcp_http_transport.py::test_tools_list_requires_session_header \
       tests/test_mcp_http_transport.py::test_tools_list_with_valid_session \
       tests/test_mcp_http_transport.py::test_unknown_session_id_returns_400 \
       tests/test_mcp_http_transport.py::test_oversized_body_rejected -v
```

Expected: `ImportError: cannot import name 'create_mcp_http_app'`

- [ ] **Step 3: Implement `create_mcp_http_app()` in `mcp_http_transport.py`**

Append to `src/buonaiuto_doc4llm/mcp_http_transport.py`:

```python
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

# Mirror the same cap used by the stdio transport.
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
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Buonaiuto Doc4LLM MCP", docs_url=None, redoc_url=None)
    registry = SessionRegistry()

    @app.get("/mcp")
    async def get_info() -> dict[str, Any]:
        return {
            "name": "Buonaiuto Doc4LLM",
            "version": "0.1.0",
            "protocolVersion": "2025-03-26",
        }

    @app.post("/mcp")
    async def post_mcp(request: Request) -> JSONResponse:
        # Enforce body size limit before reading.
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
            params = rpc.get("params", {})
            # Run bootstrap (same path as stdio) — this records the session row.
            server._bootstrap_from_initialize_params(params)
            project_id = server._session_project_id  # may be None if async install pending
            state = registry.allocate(session_id=new_sid, project_id=project_id)
            # Record the session under the HTTP session id.
            try:
                server.service.record_mcp_session(
                    session_id=new_sid,
                    project_id=project_id,
                    workspace_path=None,
                    client_name=(rpc.get("params") or {}).get("clientInfo", {}).get("name"),
                    client_version=(rpc.get("params") or {}).get("clientInfo", {}).get("version"),
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

    return app
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_mcp_http_transport.py -v
```

Expected: all tests pass

- [ ] **Step 5: Run full suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/buonaiuto_doc4llm/mcp_http_transport.py tests/test_mcp_http_transport.py
git commit -m "feat: add create_mcp_http_app with session management and size guard"
```

---

## Task 4: `serve --http` flags and `serve-http` subcommand in `__main__.py`

**Files:**
- Modify: `src/buonaiuto_doc4llm/__main__.py`

- [ ] **Step 1: Add flags to `serve` subparser**

In `build_parser()`, find the `serve` subparser block (around line 194) and add three new arguments after the existing `--dashboard-port` argument:

```python
    serve.add_argument(
        "--http",
        action="store_true",
        help="Also start MCP HTTP transport (Streamable HTTP for Claude Desktop / claude.ai)",
    )
    serve.add_argument(
        "--http-host", default="127.0.0.1",
        help="Bind address for MCP HTTP transport (default: 127.0.0.1)",
    )
    serve.add_argument(
        "--http-port", type=int, default=8421,
        help="Port for MCP HTTP transport (default: 8421)",
    )
```

- [ ] **Step 2: Add `serve-http` subcommand to `build_parser()`**

After the existing `dashboard` subparser block, add:

```python
    serve_http = subparsers.add_parser(
        "serve-http",
        help="Start MCP HTTP transport only (no stdio) — for Claude Desktop / claude.ai",
    )
    serve_http.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    serve_http.add_argument(
        "--port", type=int, default=8421,
        help="Port (default: 8421)",
    )
    serve_http.add_argument(
        "--embeddings",
        action="store_true",
        help="Enable offline semantic search (sentence-transformers + in-memory Qdrant).",
    )
    serve_http.add_argument(
        "--dashboard",
        action="store_true",
        help="Also start the web dashboard in a background thread",
    )
    serve_http.add_argument(
        "--dashboard-host", default="127.0.0.1",
    )
    serve_http.add_argument(
        "--dashboard-port", type=int, default=8420,
    )
```

- [ ] **Step 3: Add `_start_mcp_http_thread()` helper**

Add this function near `_start_dashboard_thread()` in `__main__.py`:

```python
def _start_mcp_http_thread(base_dir: str, host: str, port: int) -> None:
    """Start the MCP HTTP transport in a background daemon thread."""
    import uvicorn
    from buonaiuto_doc4llm.mcp_server import MCPServer
    from buonaiuto_doc4llm.mcp_http_transport import create_mcp_http_app

    mcp_server = MCPServer(base_dir)
    mcp_server.service.scan()
    app = create_mcp_http_app(mcp_server)

    def _run() -> None:
        uvicorn.run(app, host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True, name="mcp-http")
    t.start()
    import sys
    print(f"Buonaiuto Doc4LLM MCP HTTP: http://{host}:{port}/mcp", file=sys.stderr)
```

- [ ] **Step 4: Wire `--http` into the `serve` command handler**

In `main()`, find the `if args.command == "serve":` block (around line 322) and add the HTTP thread start before `server.serve()`:

```python
    if args.command == "serve":
        server = MCPServer(args.base_dir)
        if args.project_path:
            server.service.install_project(
                project_root=args.project_path,
                project_id=args.project_id,
            )
        else:
            server.service.scan()
        if args.dashboard:
            _start_dashboard_thread(args.base_dir, args.dashboard_host, args.dashboard_port)
        if args.http:
            _start_mcp_http_thread(args.base_dir, args.http_host, args.http_port)
        server.serve()
        return
```

- [ ] **Step 5: Add `serve-http` command handler**

Add this block in `main()` after the `serve` block:

```python
    if args.command == "serve-http":
        import uvicorn
        from buonaiuto_doc4llm.mcp_http_transport import create_mcp_http_app

        server = MCPServer(args.base_dir)
        server.service.scan()
        if args.dashboard:
            _start_dashboard_thread(args.base_dir, args.dashboard_host, args.dashboard_port)
        app = create_mcp_http_app(server)
        print(f"Buonaiuto Doc4LLM MCP HTTP: http://{args.host}:{args.port}/mcp")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return
```

- [ ] **Step 6: Smoke-test the new subcommand**

```bash
cd /Users/massimo/Projects_Massimo/Documentation_LLMs
PYTHONPATH=src python -m buonaiuto_doc4llm --help 2>&1 | grep -E "serve-http|serve"
PYTHONPATH=src python -m buonaiuto_doc4llm serve-http --help
```

Expected: `serve-http` appears in subcommands, `--host` and `--port` flags visible.

- [ ] **Step 7: Run full test suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/buonaiuto_doc4llm/__main__.py
git commit -m "feat: serve --http flags and serve-http subcommand"
```

---

## Task 5: Update `run.sh` and `run.bat`

**Files:**
- Modify: `run.sh`
- Modify: `run.bat`

- [ ] **Step 1: Update `run.sh`**

Replace the menu and case block. The full updated file:

```bash
#!/usr/bin/env bash
# Launcher for Buonaiuto Doc4LLM (macOS / Linux)
# Prompts for which mode to start, then runs it.

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
    echo "Error: no Python interpreter found. Set PYTHON_BIN env var." >&2
    exit 1
fi

export PYTHONPATH="$BASE_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

cat <<'EOF'
Buonaiuto Doc4LLM — choose a mode:

  1) MCP stdio server only             (Claude Code / Cursor / Windsurf — subprocess)
  2) MCP stdio server + dashboard      (stdio + website at http://127.0.0.1:8420)
  3) Dashboard only                    (website at http://127.0.0.1:8420)
  4) Watch docs_center/ for changes    (auto re-scan)
  5) MCP HTTP server only              (Claude Desktop / claude.ai — http://127.0.0.1:8421/mcp)
  6) MCP stdio + HTTP + dashboard      (all three in one process)

EOF

read -rp "Enter choice [1-6]: " choice

case "$choice" in
    1)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve
        ;;
    2)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve --dashboard
        ;;
    3)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" dashboard
        ;;
    4)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" watch
        ;;
    5)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve-http
        ;;
    6)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve \
            --http --http-port 8421 \
            --dashboard --dashboard-port 8420
        ;;
    *)
        echo "Invalid choice: $choice" >&2
        exit 1
        ;;
esac
```

- [ ] **Step 2: Update `run.bat`**

Replace the full file:

```bat
@echo off
REM Launcher for Buonaiuto Doc4LLM (Windows)
REM Prompts for which mode to start, then runs it.

setlocal enabledelayedexpansion

set "BASE_DIR=%~dp0"
if "!BASE_DIR:~-1!"=="\" set "BASE_DIR=!BASE_DIR:~0,-1!"

if "!PYTHON_BIN!"=="" (
    where python >nul 2>nul
    if errorlevel 1 (
        where py >nul 2>nul
        if errorlevel 1 (
            echo Error: no Python interpreter found. Set PYTHON_BIN env var.
            exit /b 1
        ) else (
            set "PYTHON_BIN=py"
        )
    ) else (
        set "PYTHON_BIN=python"
    )
)

set "PYTHONPATH=!BASE_DIR!\src;!PYTHONPATH!"

echo Buonaiuto Doc4LLM -- choose a mode:
echo.
echo   1) MCP stdio server only             (Claude Code / Cursor / Windsurf -- subprocess)
echo   2) MCP stdio server + dashboard      (stdio + website at http://127.0.0.1:8420)
echo   3) Dashboard only                    (website at http://127.0.0.1:8420)
echo   4) Watch docs_center\ for changes    (auto re-scan)
echo   5) MCP HTTP server only              (Claude Desktop / claude.ai -- http://127.0.0.1:8421/mcp)
echo   6) MCP stdio + HTTP + dashboard      (all three in one process)
echo.

set /p choice="Enter choice [1-6]: "

if "!choice!"=="1" (
    "!PYTHON_BIN!" -m buonaiuto_doc4llm --base-dir "!BASE_DIR!" serve
) else if "!choice!"=="2" (
    "!PYTHON_BIN!" -m buonaiuto_doc4llm --base-dir "!BASE_DIR!" serve --dashboard
) else if "!choice!"=="3" (
    "!PYTHON_BIN!" -m buonaiuto_doc4llm --base-dir "!BASE_DIR!" dashboard
) else if "!choice!"=="4" (
    "!PYTHON_BIN!" -m buonaiuto_doc4llm --base-dir "!BASE_DIR!" watch
) else if "!choice!"=="5" (
    "!PYTHON_BIN!" -m buonaiuto_doc4llm --base-dir "!BASE_DIR!" serve-http
) else if "!choice!"=="6" (
    "!PYTHON_BIN!" -m buonaiuto_doc4llm --base-dir "!BASE_DIR!" serve --http --http-port 8421 --dashboard --dashboard-port 8420
) else (
    echo Invalid choice: !choice!
    exit /b 1
)

endlocal
```

- [ ] **Step 3: Commit**

```bash
git add run.sh run.bat
git commit -m "feat: add MCP HTTP options 5 and 6 to run.sh and run.bat"
```

---

## Task 6: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update technology stack table**

Find the row:
```
| MCP server | Python 3.11+, JSON-RPC 2.0 over stdio (protocol `2025-03-26`) |
```
Replace with:
```
| MCP server | Python 3.11+, JSON-RPC 2.0 over stdio and Streamable HTTP (protocol `2025-03-26`) |
```

- [ ] **Step 2: Add HTTP transport section before "Claude Code" client section**

Find the line `### 3. Connect your AI tool` and add a new intro paragraph after it:

```markdown
### 3. Connect your AI tool

Two transport modes are available:

| Transport | How it works | Best for |
|---|---|---|
| **stdio** (default) | Client launches the server as a subprocess | Claude Code, Cursor, Windsurf, Zed |
| **HTTP** (new) | Server runs independently; client connects by URL | Claude Desktop, claude.ai web |

To use HTTP transport, start the server first with option 5 or 6 in `run.sh` / `run.bat`, or:

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve-http
# MCP HTTP listening at http://127.0.0.1:8421/mcp
```

Then configure your client with the URL (see Claude Desktop below).

---
```

- [ ] **Step 3: Update Claude Desktop config to show URL-based connection**

Find the Claude Desktop section and replace the JSON config block with:

```markdown
#### Claude Desktop

**Option A — HTTP transport (recommended, no subprocess):**

Start the server first (`run.sh` option 5), then edit
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "buonaiuto-doc4llm": {
      "url": "http://127.0.0.1:8421/mcp"
    }
  }
}
```

**Option B — stdio (subprocess launch):**

```json
{
  "mcpServers": {
    "buonaiuto-doc4llm": {
      "command": "/opt/anaconda3/bin/python",
      "args": [
        "-m", "buonaiuto_doc4llm",
        "--base-dir", "/path/to/Buonaiuto-Doc4LLM",
        "serve"
      ],
      "env": {
        "PYTHONPATH": "/path/to/Buonaiuto-Doc4LLM/src"
      }
    }
  }
}
```
```

- [ ] **Step 4: Update the launchers table**

Find the options table (around line 478) and replace it:

```markdown
| Option | Command it runs | When to use |
|---|---|---|
| 1 | `serve` | MCP stdio server only (Claude Code / Cursor / Windsurf — subprocess) |
| 2 | `serve --dashboard` | MCP stdio server **and** web dashboard at http://127.0.0.1:8420 |
| 3 | `dashboard` | Web dashboard only at http://127.0.0.1:8420 (no MCP) |
| 4 | `watch` | Watch `docs_center/` for changes and auto re-scan |
| 5 | `serve-http` | MCP HTTP server only — Claude Desktop / claude.ai connect via http://127.0.0.1:8421/mcp |
| 6 | `serve --http --dashboard` | MCP stdio + MCP HTTP + dashboard all in one process |
```

- [ ] **Step 5: Add `serve-http` to CLI reference**

Find the CLI reference block (around line 540) and add after the existing `serve` lines:

```bash
# Start MCP HTTP server only (Claude Desktop / claude.ai)
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve-http
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve-http --port 9000
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve-http --host 0.0.0.0 --port 8421

# Start MCP stdio + HTTP + dashboard together
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve \
  --http --http-port 8421 \
  --dashboard --dashboard-port 8420
```

- [ ] **Step 6: Update roadmap table**

Find:
```
| stdio MCP | Streamable HTTP MCP transport |
```
Replace with:
```
| ~~stdio MCP only~~ | Streamable HTTP MCP transport ✓ |
```

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: update README for MCP HTTP transport"
```

---

## Task 7: Update `CLAUDE.md` and `docs/architecture/plan.md`

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/architecture/plan.md`

- [ ] **Step 1: Update `CLAUDE.md` MCP server section**

Find the "Using the MCP Server from external LLMs" section. After the existing `claude mcp add` block, add:

```markdown
**HTTP transport (Claude Desktop / claude.ai)** — start the server first:

```bash
PYTHONPATH=src python3 -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve-http
```

Then configure Claude Desktop with:
```json
{ "mcpServers": { "buonaiuto-doc4llm": { "url": "http://127.0.0.1:8421/mcp" } } }
```
```

- [ ] **Step 2: Update `CLAUDE.md` Commands section**

In the Commands block, add after the `serve` line:

```bash
# Start MCP HTTP transport (for Claude Desktop / claude.ai)
PYTHONPATH=src python3 -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve-http

# Start all transports + dashboard in one process
PYTHONPATH=src python3 -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve \
  --http --http-port 8421 --dashboard
```

- [ ] **Step 3: Update `docs/architecture/plan.md` — mark HTTP transport done**

Find in the "What is missing before Phase 1 exit" section:
```
2. **No MCP Streamable HTTP transport.** The server speaks only stdio. Hosted deployment requires Streamable HTTP per MCP spec. `HostedMCPGateway` stub exists but is not wired.
```
Replace with:
```
2. ~~**No MCP Streamable HTTP transport.**~~ **Implemented.** `serve-http` subcommand and `serve --http` flag start a FastAPI endpoint at `/mcp` using the MCP Streamable HTTP protocol. Claude Desktop and claude.ai connect via `http://127.0.0.1:8421/mcp`.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/architecture/plan.md
git commit -m "docs: update CLAUDE.md and architecture plan for HTTP transport"
```

---

## Task 8: End-to-end smoke test

- [ ] **Step 1: Start `serve-http` in the background**

```bash
cd /Users/massimo/Projects_Massimo/Documentation_LLMs
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve-http &
MCP_PID=$!
sleep 2
```

- [ ] **Step 2: Send `initialize` and capture session ID**

```bash
SID=$(curl -s -X POST http://127.0.0.1:8421/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"smoke-test","version":"0.1"}}}' \
  -D - 2>&1 | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\r')
echo "Session: $SID"
```

Expected: a UUID like `3a7b1c2d-...`

- [ ] **Step 3: Call `tools/list` with session ID**

```bash
curl -s -X POST http://127.0.0.1:8421/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -m json.tool | grep '"name"' | head -5
```

Expected: tool names like `search_documentation`, `read_doc`, etc.

- [ ] **Step 4: Confirm missing session header returns 400**

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8421/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}'
```

Expected: `400`

- [ ] **Step 5: Kill background server**

```bash
kill $MCP_PID 2>/dev/null || true
```

- [ ] **Step 6: Run full test suite one final time**

```bash
pytest --tb=short -q
```

Expected: all tests pass

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat: MCP Streamable HTTP transport complete — serve-http subcommand"
```
