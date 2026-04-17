# MCP Interaction Log — Design

**Date:** 2026-04-17
**Status:** Approved for implementation
**Topic:** Per-project audit log of MCP tool calls, viewable from the web dashboard

## 1. Problem

Today there is no way to tell, from the web dashboard, whether a project declared in `docs_center/projects/<name>.json` is actually being used by an MCP client. `/projects` shows the subscription list and the unread-updates count, but not "did Claude Code or Cursor ever call `search_docs` on behalf of this project?". When a user sets up an MCP integration and something does not seem to be working, there is no log to inspect.

## 2. Goals

- Every MCP tool call is persisted with enough metadata to answer **"is this project actively calling the server, and what is it calling?"**.
- Each call is attributed to a project whenever possible, automatically — the user must not annotate every call by hand.
- A new page `/projects/<project_id>/log` on the web dashboard shows the log with a summary header, a per-day chart, a per-tool breakdown, and a filterable raw table.
- Log storage is bounded so the SQLite DB does not grow without limit.
- Logging failures never break a legitimate tool call.

## 3. Non-goals

- Real-time streaming / push updates. The page polls on navigation only.
- Cross-project aggregate analytics. Each project's log is independent.
- Exporting or forwarding the log to external systems (OpenTelemetry, etc.). That is a platform-phase concern tracked elsewhere.
- Multi-tenant access control. The dashboard is a local-first tool.

## 4. Architecture

```
MCP client ──initialize(workspaceFolders)──▶ MCPServer
                                              │
                                              ├─ resolve workspace → project_id
                                              ├─ INSERT mcp_sessions
                                              │     (session_id pinned on MCPServer instance)
                                              │
MCP client ──tools/call──▶ MCPServer ─────────┤
                           │                  ├─ run tool
                           │                  ├─ INSERT mcp_interactions
                           ▼                  │     (session_id, project_id, tool,
                      tool result             │      latency_ms, result_chars, error)
                                              │
                                              ▼
                                     /projects/<id>/log reads these tables
```

Four components:

1. **`mcp_sessions` table** — one row per MCP client session.
2. **`mcp_interactions` table** — one row per tool call.
3. **Instrumentation in `MCPServer`** — resolves workspace to project on `initialize`, wraps `_call_tool` for latency + error + result-size capture.
4. **Dashboard additions** — per-project summary on `/projects`, new `/projects/<id>/log` page with HTMX-driven filter/paging.

## 5. Data model

Both tables live in the existing `state/buonaiuto_doc4llm.db`. Schema created in `DocsHubService._ensure_schema()` alongside the existing tables (no Alembic; prototype path).

```sql
CREATE TABLE IF NOT EXISTS mcp_sessions (
    session_id      TEXT PRIMARY KEY,              -- uuid4
    project_id      TEXT,                          -- NULL if unresolved
    workspace_path  TEXT,                          -- raw path from initialize
    client_name     TEXT,
    client_version  TEXT,
    started_at      TEXT NOT NULL,                 -- ISO-8601 UTC
    last_seen_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_sessions_project ON mcp_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_mcp_sessions_started_at ON mcp_sessions(started_at);

CREATE TABLE IF NOT EXISTS mcp_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    project_id      TEXT,                          -- denormalised from session
    tool_name       TEXT NOT NULL,
    arguments_json  TEXT NOT NULL,                 -- long strings truncated
    result_chars    INTEGER,                       -- NULL on error
    error           TEXT,                          -- NULL on success
    latency_ms      INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_interactions_project_created
    ON mcp_interactions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_interactions_session
    ON mcp_interactions(session_id);
CREATE INDEX IF NOT EXISTS idx_mcp_interactions_created
    ON mcp_interactions(created_at);
```

### Argument sanitisation

Before insertion, each value in `arguments` that is a string longer than 500 chars is replaced with `"<truncated>…[<original length> chars]"`. Nested structures are walked recursively. The logic lives inside `DocsHubService.record_mcp_interaction` so it cannot be forgotten by callers.

### Retention

`DocsHubService.prune_mcp_interactions(days: int = 30)`:

1. `DELETE FROM mcp_interactions WHERE created_at < now - days`.
2. `DELETE FROM mcp_sessions WHERE session_id NOT IN (SELECT DISTINCT session_id FROM mcp_interactions) AND started_at < now - days`.

Called from:

- `DocsHubService.scan()` — so every CLI scan / filesystem-watch rescan also prunes.
- The dashboard's `/projects/<id>/log` handler — lazy prune on page render.

## 6. Workspace → project_id resolution

On `initialize`, the MCP spec allows clients to send:

- `params.rootUri` (legacy single-folder), or
- `params.workspaceFolders[0].uri` (array form, current spec).

Algorithm (deterministic, no side-effects):

1. Extract the first non-empty path from `rootUri`, else `workspaceFolders[0].uri`. Normalise `file://` URIs to filesystem paths.
2. Take the basename of that path.
3. Load every `docs_center/projects/*.json`; look for exactly one project whose `project_id` or `name` matches the basename (case-insensitive).
4. Return that `project_id` if unique; otherwise return `None`.

No auto-install, no best-guess matching. If a session cannot be resolved, its rows appear under **"Unattributed sessions (N)"** on `/projects`. The user runs `install_project` once to fix it.

## 7. Instrumentation in `MCPServer`

Since the MCP stdio server serves one client per process, one session is active at a time. A single `self._session_id` / `self._session_project_id` pair on the `MCPServer` instance is sufficient.

On `initialize`:

```python
session_id = str(uuid.uuid4())
client_info = params.get("clientInfo", {})
workspace_path = _extract_workspace_path(params)
project_id = self.service.resolve_project_for_workspace(workspace_path)
self.service.record_mcp_session(
    session_id=session_id,
    project_id=project_id,
    workspace_path=workspace_path,
    client_name=client_info.get("name"),
    client_version=client_info.get("version"),
)
self._session_id = session_id
self._session_project_id = project_id
```

On every `tools/call` (wrapper around the renamed `_dispatch_tool`):

```python
started = time.monotonic()
error_msg: str | None = None
result_chars: int | None = None
try:
    result = self._dispatch_tool(name, arguments)
    result_chars = len(json.dumps(result, default=str))
    return result
except Exception as exc:
    error_msg = f"{type(exc).__name__}: {exc}"
    raise
finally:
    self.service.record_mcp_interaction(
        session_id=self._session_id,
        project_id=self._session_project_id,
        tool_name=name,
        arguments=arguments,
        result_chars=result_chars,
        error=error_msg,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
```

`record_mcp_session` / `record_mcp_interaction` swallow `sqlite3.OperationalError` and log to stderr — a logging failure must never break a tool call.

## 8. Service API additions

```python
# DocsHubService
def resolve_project_for_workspace(self, workspace_path: str | None) -> str | None
def record_mcp_session(self, *, session_id, project_id, workspace_path,
                       client_name, client_version) -> None
def record_mcp_interaction(self, *, session_id, project_id, tool_name,
                           arguments, result_chars, error, latency_ms) -> None
def prune_mcp_interactions(self, days: int = 30) -> dict[str, int]
def get_project_interaction_summary(self, project_id: str | None,
                                    days: int = 30) -> dict
def list_project_interactions(self, project_id: str | None, *,
                              limit: int = 100, offset: int = 0,
                              tool_name: str | None = None,
                              since: str | None = None,
                              errors_only: bool = False) -> list[dict]
def list_unattributed_sessions(self, days: int = 30) -> list[dict]
```

### Summary payload

```json
{
  "project_id": "ordina28",
  "last_used_at": "2026-04-17T09:12:44Z",
  "total_calls": 1247,
  "total_sessions": 12,
  "window_days": 30,
  "unique_tools": 14,
  "calls_per_day": [{"day": "2026-03-19", "count": 42}, "..."],
  "tool_counts": [{"tool_name": "search_docs", "count": 342}, "..."],
  "client_breakdown": [{"client_name": "claude-code",
                        "client_version": "0.2.103",
                        "count": 900}, "..."],
  "error_rate": 0.018
}
```

`project_id=None` means "unattributed sessions".

## 9. Dashboard changes

### Routes (`src/buonaiuto_doc4llm/dashboard/routes.py`)

```
GET  /projects/<project_id>/log             → full HTML page
GET  /projects/<project_id>/log/rows        → HTMX partial for table filter/paging
GET  /projects/unattributed/log             → same, project_id=None
```

`_get_projects_with_unread` is extended with one call to `get_project_interaction_summary` per project, so the projects list itself shows last-used-at and 30-day call count without a second round-trip. Indexes on `mcp_interactions(project_id, created_at DESC)` keep this fast.

### `projects.html` change

```html
<a href="/projects/{{ project.project_id | urlencode }}/log" class="btn btn-sm">
  View log
</a>
<span class="text-muted text-sm">
  last used {{ project.last_used_human or "never" }}
  · {{ project.call_count_30d or 0 }} calls / 30d
</span>
```

An "Unattributed sessions (N)" card appears at the bottom when `N > 0`.

### `project_log.html` layout (new template)

```
Header:
  <project_id> · MCP interaction log
  Last used 2h ago · 1,247 calls / 30d · 14 tools · 12 sessions · error rate 1.8%
  Clients: claude-code 0.2.103 (900), cursor 0.42 (347)

Panel A (server-rendered inline SVG bar chart, one bar/day, 30 bars)
Panel B (top tools, counts, link "show all")

Recent calls table:
  Filters: [Tool ▾] [Last ▾: 1h / 24h / 7d / 30d] [Errors only ▢]
  Columns: Time · Tool · Key args · Latency · Size · Status
  Pagination: "Load more" (HTMX, 50 per page)
```

The chart is pure inline SVG generated server-side — consistent with the existing FastAPI + Jinja2 + HTMX stack. Filters use `hx-get` → `/projects/<id>/log/rows` with `hx-target="#log-rows"` to swap only the table body.

### Jinja filter `mcp_args_summary`

Per-tool one-liner rendering of `arguments_json` in the "Key args" column:

- `search_docs`, `search_documentation` → `technology, "query"`
- `read_doc`, `read_full_page` → `technology/rel_path`
- `list_project_updates`, `ack_project_updates` → `project_id`
- `fetch_docs` → `technology or "all"`
- others → first two key-value pairs of `arguments_json`

Lives in `src/buonaiuto_doc4llm/dashboard/_filters.py` (new or extended).

## 10. README & docs

`README.md` — the **Projects `/projects`** subsection under "Web dashboard · Pages" gains:

- A **View Log** button bullet.
- A description of `/projects/<project_id>/log`: summary header, per-day chart, top-tools panel, filterable paginated table, "Unattributed sessions" card on the listing page.

## 11. Testing

`tests/test_mcp_interaction_log.py` (new) — uses real SQLite via `tmp_path`, no mocks:

1. `test_schema_created` — tables exist after first `DocsHubService(...)`.
2. `test_record_and_query_session`.
3. `test_record_and_query_interaction` across 2 sessions.
4. `test_argument_truncation` — 10 000-char string truncated to sentinel.
5. `test_retention_prune_deletes_old_rows`.
6. `test_orphan_session_pruning`.
7. `test_logging_failure_does_not_break_tool_call` — monkeypatch raises, tool still returns.
8. `test_workspace_resolution_basename_match`.
9. `test_workspace_resolution_no_match_returns_none`.
10. `test_mcp_server_records_initialize_and_tool_call` — full `handle_request` round-trip.
11. `test_error_is_recorded` — `error` populated, `result_chars = NULL`.
12. `test_unattributed_sessions_listing`.

`tests/test_dashboard_project_log.py` (new) — extends existing dashboard pytest client setup:

13. `test_projects_page_shows_last_used`.
14. `test_project_log_page_renders` — GET `/projects/foo/log` → 200 + chart + table rows.
15. `test_project_log_rows_filter_by_tool`.
16. `test_unattributed_sessions_card_visible_when_present`.

## 12. File layout

| File | Change | Est. lines |
|---|---|---|
| `src/buonaiuto_doc4llm/service.py` | +schema, +7 methods | ~250 |
| `src/buonaiuto_doc4llm/interaction_log.py` (new, if `service.py` > 1000 lines after addition) | extract log methods into helper class | ~250 |
| `src/buonaiuto_doc4llm/mcp_server.py` | +session pin, +`_call_tool` wrapper | ~60 |
| `src/buonaiuto_doc4llm/dashboard/routes.py` | +2 routes, +summary join | ~80 |
| `src/buonaiuto_doc4llm/dashboard/_filters.py` | +`mcp_args_summary` | ~60 |
| `src/buonaiuto_doc4llm/dashboard/templates/project_log.html` | new | ~120 |
| `src/buonaiuto_doc4llm/dashboard/templates/projects.html` | +View Log button | +10 |
| `tests/test_mcp_interaction_log.py` | new | ~350 |
| `tests/test_dashboard_project_log.py` | new | ~100 |
| `README.md` | feature description | +20 |

The 400-line new-file guideline is respected. If `service.py` crosses 1000 lines with the additions, the log-related methods are extracted into a dedicated `interaction_log.py` class held by `DocsHubService`.

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Logging adds latency to every tool call. | Single `INSERT` with prepared statement. Indexes on write columns only. Budget: well under 1 ms per call on a local disk. |
| Log table grows without bound. | 30-day retention, pruned on `scan()` and on log-page load. |
| Argument payloads leak large document bodies into the DB. | 500-char string truncation in `record_mcp_interaction`. |
| A logging failure breaks a legitimate tool call. | `record_mcp_*` swallow `sqlite3.OperationalError`, log to stderr. Test #7 guards this. |
| Workspace resolution returns wrong project due to duplicate basenames. | Return `None` on ambiguity (not a best guess). Duplicate basenames across projects are rare in practice; the user can disambiguate by renaming a project file. |
| Old MCP clients that do not send `initialize` workspace info. | `project_id = NULL`; calls appear under "Unattributed sessions (N)". |

## 14. Rollout

This is a local prototype — no migration gate beyond the new `CREATE TABLE IF NOT EXISTS` statements, which run on the next `DocsHubService` construction. Existing databases gain the two new empty tables automatically; no data migration needed.
