# MCP Streamable HTTP Transport — Design Spec

**Date:** 2026-04-22
**Status:** Approved
**Priority:** Phase 1 — local private deployment

---

## Problem

The existing MCP server speaks JSON-RPC 2.0 over stdio. This means it can only be used by
clients that launch it as a subprocess (Claude Code, Cursor, Windsurf). Claude Desktop and
claude.ai web chat cannot reach a stdio server — they require an HTTP URL. The result is that
web-based Claude conversations never call the MCP server to retrieve updated documentation.

## Goal

Add an **MCP Streamable HTTP transport** so any MCP-capable client — including Claude Desktop
and claude.ai — can connect by URL instead of subprocess launch.

The HTTP transport reuses the existing `MCPServer.handle_request()` and `DocsHubService`
exactly. No tool logic is duplicated. The stdio path is unchanged.

---

## Architecture

### Shared instance model

One `MCPServer` instance (and one `DocsHubService`) is shared across all HTTP clients. This
matches how hosted MCP services like Context7 operate.

The current `MCPServer` stores per-session state (`_session_id`, `_session_project_id`) as
instance variables, which would collide under concurrent HTTP connections. These are extracted
into a `SessionState` dataclass and a thread-safe `SessionRegistry`. The stdio path continues
to use instance variables (unchanged behaviour); the HTTP path passes `session_state` explicitly
into `handle_request()`.

### New file: `src/buonaiuto_doc4llm/mcp_http_transport.py`

Contains:

- `SessionState` — dataclass: `session_id`, `project_id`, `created_at`.
- `SessionRegistry` — thread-safe `dict[str, SessionState]` with `allocate()` and `get()`.
- `create_mcp_http_app(server: MCPServer) -> FastAPI` — factory that returns a FastAPI app
  with two routes:
  - `GET /mcp` — returns server info JSON (MCP spec discoverability requirement).
  - `POST /mcp` — main endpoint. On `initialize`: allocates session, bootstraps, sets
    `Mcp-Session-Id` response header. All other methods: requires `Mcp-Session-Id` header,
    looks up session, calls `server.handle_request(request, session_state=state)`.

### Modified: `src/buonaiuto_doc4llm/mcp_server.py`

- `handle_request()` gains `session_state: SessionState | None = None` parameter.
- When `session_state` is provided (HTTP path), tool call logging uses its `session_id` and
  `project_id` instead of `self._session_id` / `self._session_project_id`.
- stdio path: passes `None` — existing behaviour is fully preserved.

### Modified: `src/buonaiuto_doc4llm/__main__.py`

`serve` subcommand gains three new flags:

| Flag | Default | Purpose |
|---|---|---|
| `--http` | off | Also start MCP HTTP transport |
| `--http-host` | `127.0.0.1` | Bind address for HTTP transport |
| `--http-port` | `8421` | Port for HTTP transport |

When `--http` is set, `create_mcp_http_app()` is started via uvicorn in a background daemon
thread, then the stdio `serve()` loop runs as before.

New `serve-http` subcommand: HTTP-only (no stdio). Same `--host` / `--port` flags. For use
when running as a standalone HTTP server without any stdio client.

### Modified: `run.sh` and `run.bat`

Both launchers gain two new options:

| Option | Command | When to use |
|---|---|---|
| 5 | `serve-http` | MCP HTTP server only (Claude Desktop / claude.ai — URL-based) |
| 6 | `serve --http --dashboard` | MCP stdio + MCP HTTP + dashboard (all three) |

---

## Data Flow

```
Claude Desktop / claude.ai
        │
        │  POST /mcp  {"method":"initialize",...}
        ▼
MCPHttpApp  →  SessionRegistry.allocate(new_sid)
            →  MCPServer._bootstrap_from_initialize_params(params)
            →  service.record_mcp_session(session_id, ...)
            ←  200 + Mcp-Session-Id: <uuid>

        │  POST /mcp  {"method":"tools/call",...}
        │  Mcp-Session-Id: <uuid>
        ▼
MCPHttpApp  →  SessionRegistry.get(uuid)  →  SessionState
            →  MCPServer.handle_request(request, session_state=state)
            →  service.record_mcp_interaction(session_id, ...)
            ←  200  {"jsonrpc":"2.0","result":{...}}
```

---

## Session Lifecycle

- Session created on `initialize` — `Mcp-Session-Id` UUID returned in response header.
- Subsequent requests must carry `Mcp-Session-Id` header. Missing or unknown → HTTP 400.
- Sessions live in memory only (no TTL). Cleared on process restart.
- `mcp_sessions` and `mcp_interactions` tables are written exactly as with stdio — dashboard
  shows HTTP sessions alongside stdio sessions with no change needed.

---

## Security

- Default bind: `127.0.0.1` (loopback only). No external exposure without explicit `--http-host 0.0.0.0`.
- No API key required for local use. API key auth is a Phase 2 concern (hosted SaaS).
- Input size limit: same `MAX_JSONRPC_LINE_BYTES = 16 MB` guard applied to HTTP body.

---

## Client Configuration After Implementation

### Claude Desktop

Replace subprocess config with URL-based config:

```json
{
  "mcpServers": {
    "buonaiuto-doc4llm": {
      "url": "http://127.0.0.1:8421/mcp"
    }
  }
}
```

Start the server first:

```bash
./run.sh   # choose option 5 (serve-http)
# or
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve-http
```

### Claude Code (unchanged — stdio still works)

```bash
claude mcp add --scope project buonaiuto-doc4llm \
  /opt/anaconda3/bin/python \
  -- -m buonaiuto_doc4llm --base-dir /path/to/repo serve
```

### Combined (stdio + HTTP + dashboard in one process)

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve \
  --http --http-port 8421 \
  --dashboard --dashboard-port 8420
```

---

## Testing

- `SessionRegistry`: unit tests for thread-safe allocation, lookup, unknown-id 400.
- `MCPHttpApp`: integration tests via FastAPI `TestClient` — `initialize` returns session header,
  subsequent `tools/call` with header returns tool result, missing header returns 400.
- Existing stdio tests: unchanged.

---

## Files Changed

| File | Change |
|---|---|
| `src/buonaiuto_doc4llm/mcp_http_transport.py` | New — `SessionRegistry`, `SessionState`, `create_mcp_http_app()` |
| `src/buonaiuto_doc4llm/mcp_server.py` | `handle_request()` accepts optional `session_state` |
| `src/buonaiuto_doc4llm/__main__.py` | `serve --http/--http-host/--http-port`, new `serve-http` subcommand |
| `run.sh` | Options 5 and 6 |
| `run.bat` | Options 5 and 6 |
| `README.md` | HTTP transport section, updated client config, updated launcher table, updated CLI reference, updated roadmap |
| `CLAUDE.md` | HTTP transport section, updated commands |
| `docs/architecture/plan.md` | Mark HTTP transport as implemented |

---

## Out of Scope

- API key authentication (Phase 2 / hosted SaaS)
- TLS / HTTPS (not needed for localhost; add when binding to 0.0.0.0)
- Session TTL / eviction (not needed for single-user local use)
- Streaming SSE responses (current tools return complete JSON; streaming is a Phase 2 concern)
