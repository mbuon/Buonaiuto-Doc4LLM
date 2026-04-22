# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Project Name**: Documentation_LLMs
**Project Directory**: /Users/massimo/Projects_Massimo/Documentation_LLMs
**Purpose**: Documentation retrieval platform matching Context7 — reliable, version-aware, citation-friendly access to technical documentation through MCP and HTTP APIs.

The `src/buonaiuto_doc4llm/` package is the seed prototype. The full platform vision is in `docs/architecture/plan.md` (v6).

For agent execution rules, parallel dispatch, skills, and worktree protocols see: `CLAUDE.agents.md`

---

## Using the MCP Server from external LLMs

The MCP server speaks JSON-RPC over stdio. A `.mcp.json` file at the repo root
tells Claude Code, Claude Desktop, and Codex how to launch it automatically.

**Claude Code** — add the server from any terminal:
```bash
claude mcp add --scope project buonaiuto-doc4llm \
  /opt/anaconda3/bin/python \
  -- -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve
```
Or just open this folder in Claude Code — it reads `.mcp.json` automatically.

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "buonaiuto-doc4llm": {
      "command": "/opt/anaconda3/bin/python",
      "args": ["-m", "buonaiuto_doc4llm", "--base-dir", "/Users/massimo/Projects_Massimo/Documentation_LLMs", "serve"],
      "env": { "PYTHONPATH": "/Users/massimo/Projects_Massimo/Documentation_LLMs/src" }
    }
  }
}
```

**Codex / other MCP clients** — point them at `.mcp.json` in this repo root,
or copy the `mcpServers` block into their config file.

**HTTP transport (Claude Desktop / claude.ai)** — start the server first:

```bash
PYTHONPATH=src python3 -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve-http
```

Then configure Claude Desktop with:
```json
{ "mcpServers": { "buonaiuto-doc4llm": { "url": "http://127.0.0.1:8421/mcp" } } }
```

**Key rule:** `--base-dir` must always come *before* the `serve` subcommand.

---

## Commands

Use `PYTHONPATH=src` when running without installing the package.

```bash
# Scan docs_center/ and record update events into SQLite
PYTHONPATH=src python3 -m buonaiuto_doc4llm scan

# List unread updates for a project
PYTHONPATH=src python3 -m buonaiuto_doc4llm updates <project_id>

# Acknowledge updates (advance the project cursor)
PYTHONPATH=src python3 -m buonaiuto_doc4llm ack <project_id>

# Search indexed docs
PYTHONPATH=src python3 -m buonaiuto_doc4llm search <technology> <query>

# Read a single document
PYTHONPATH=src python3 -m buonaiuto_doc4llm read-doc <technology> <rel_path>

# Watch docs_center/ for changes and auto-rescan (0.75s debounce)
PYTHONPATH=src python3 -m buonaiuto_doc4llm watch

# Start the MCP stdio server (--base-dir must come before the subcommand)
PYTHONPATH=src python3 -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve

# Start MCP HTTP transport (for Claude Desktop / claude.ai — connects by URL)
PYTHONPATH=src python3 -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve-http

# Start all transports + dashboard in one process
PYTHONPATH=src python3 -m buonaiuto_doc4llm --base-dir /Users/massimo/Projects_Massimo/Documentation_LLMs serve \
  --http --http-port 8421 --dashboard

# Or from any directory using the default (cwd):
PYTHONPATH=src python3 -m buonaiuto_doc4llm serve
```

### Tests

```bash
pytest                        # all tests
pytest tests/test_service.py  # single file
pytest -k test_ack            # single test by name
pytest --tb=short -q          # compact output for CI
```

`pythonpath = ["src"]` and `testpaths = ["tests"]` are set in `pyproject.toml` — no `PYTHONPATH` prefix needed for pytest.

---

## Architecture

### Two-Layer Design

1. **Sync layer** (outside this codebase) — copies official docs into `docs_center/technologies/<tech>/`. Can be manual, `git pull`, `rsync`, or a pipeline.
2. **Offline docs hub** (this codebase) — scans what is there, detects changes, maps them to project subscriptions, and serves them via MCP.

### Directory Layout at Runtime

```
docs_center/
  technologies/<tech>/
    manifest.json           # optional: display_name, version
    <any text files>        # .md, .mdx, .txt, .rst, .json — indexed recursively
  projects/<name>.json      # declares project_id + technology subscriptions
state/
  buonaiuto_doc4llm.db      # SQLite — all runtime state
src/buonaiuto_doc4llm/
  service.py                # DocsHubService — all business logic
  mcp_server.py             # MCPServer — JSON-RPC/MCP stdio wrapper
  __main__.py               # CLI entry point (argparse)
src/control/                # Phase 2: auth, workspaces, API keys, billing, quotas
src/retrieval/              # Phase 1: Qdrant client, embedder, chunker, reranker
src/ingestion/              # Phase 1: fetcher, scheduler, source mapper, trust scorer
src/api/                    # Phase 1+: FastAPI HTTP endpoints, MCP HTTP transport
frontend/                   # Phase 2: Next.js dashboard, onboarding, billing UI
docs/architecture/
  plan.md                   # full platform architecture plan (v6)
docs/claude.ai/
  claude_memory.md          # agent personal memory (project-local)
  List_Prompts_Executed.md  # append-only prompt history
  tasks/                    # per-prompt activity log files
zscripts/backup/            # timestamped file backups (mirrors src/ hierarchy)
```

### Core Data Model (SQLite — prototype)

| Table | Purpose |
|-------|---------|
| `documents` | Indexed snapshot of every file. Primary key: `(technology, rel_path)`. Stores SHA-256 checksum and version from manifest. |
| `update_events` | Append-only log of `added / updated / deleted` events. Never truncated. |
| `projects` + `project_subscriptions` | Loaded from `docs_center/projects/*.json`. Each project subscribes to a set of technologies. |
| `project_cursors` | Per-project `last_seen_event_id`. Drives unread filtering. |
| `feedback` | Mandatory quality feedback per doc retrieval. Stores `satisfied` (bool), `reason`, `requester_id`, query, and document reference. |

### Key Flows

**`scan()`**: reads every technology directory → SHA-256 each file → compare against `documents` table → write `added/updated/deleted` events → upsert `documents`.

**`list_project_updates(project_id)`**: joins `update_events` with `project_subscriptions`, filters by `id > last_seen_event_id` when `unread_only=True`.

**`ack_project_updates(project_id)`**: advances `project_cursors.last_seen_event_id` to max event id (or explicit `through_event_id`).

**`search_docs(technology, query)`**: naive substring match. Primary upgrade target — replace with hybrid Qdrant retrieval per the architecture plan.

**`MCPServer`**: thin JSON-RPC dispatcher over stdin/stdout. All tool and prompt logic lives in `DocsHubService`. Protocol version: `2025-03-26`.

### Resource URI Schemes

- `doc://<technology>/<rel_path>` — full document text
- `updates://<project_id>` — unread update events as JSON

### MCP Surface (prototype)

Tools: `scan_docs`, `list_project_updates`, `ack_project_updates`, `read_doc`, `read_full_page`, `search_docs`, `search_documentation`, `list_supported_libraries`, `install_project`, `fetch_docs`, `submit_feedback`, `list_feedback`, `feedback_stats`

Prompt: `documentation_updates_summary`

### Mandatory Feedback Flow

After receiving documentation via `read_doc`, `search_docs`, or `search_documentation`, the requester **must** call `submit_feedback` with:
- `satisfied` (boolean) — was the documentation what you were looking for?
- `reason` (string, required) — explain why it did or did not help.

Both fields are mandatory and enforced by validation. The `feedback_stats` tool and the `/feedback` dashboard page surface satisfaction rates, per-technology and per-document breakdowns, and recent feedback entries.

### Smart Document Reading

Large documents (e.g. Stripe's 384KB `accept-a-payment.md`) are automatically handled:

- **Token budget**: `read_doc` and `read_full_page` default to `max_tokens=20000` (~80K chars). Pass `query` to prioritize relevant sections.
- **Section-level reading**: Pass `section="Verify Events"` to read only that heading (case-insensitive substring match). When a doc is truncated, the response includes a `table_of_contents` listing all section names — use those for targeted follow-up reads.
- **Metadata in every response**: `char_count`, `total_tokens`, `locale` (auto-detected: en/de/fr/es), `last_scanned_at`, `last_fetched_at`.
- **Size in search results**: `search_docs` and `search_documentation` include `char_count` and `last_scanned_at` per result — so callers can decide upfront whether to read the full doc or request a section.

---

## Platform Roadmap

| Prototype | Platform Target |
|-----------|----------------|
| `DocsHubService.scan()` | Ingestion worker + update event log |
| `search_docs()` (substring) | Hybrid BM25 + dense retrieval (Qdrant) + cross-encoder reranking |
| `MCPServer` (stdio only) | MCP server + Streamable HTTP transport |
| `docs_center/projects/*.json` | Workspace project subscriptions |
| SQLite | PostgreSQL/Supabase (control plane) + Qdrant (retrieval) |
| `docs_center/technologies/` | Ingestion pipeline output |

**Phase 1 exits when:** MRR@10 ≥ 0.70 on the seed library benchmark set.

---

## Development Conventions

### Extending `DocsHubService`

All business logic lives in `service.py`. `MCPServer` is a pure dispatcher — do not add logic there. Tests use `tmp_path` (pytest fixture) and construct a real `DocsHubService` instance — no mocks of the DB layer.

### Extending the MCP Server

- New tool: add schema to `_list_tools()` and handler to `_call_tool()`.
- New prompt: add to `prompts/list` and `prompts/get` in `handle_request()`, implement logic in `DocsHubService`.

### Adding a New Technology

Drop files into `docs_center/technologies/<tech>/` and run `scan`. Optionally add `manifest.json` with `display_name`, `version`, `description`.

### Adding a New Project

Create `docs_center/projects/<name>.json`:

```json
{
  "project_id": "my-app",
  "name": "My App",
  "technologies": ["react", "nextjs"]
}
```

---

## Hard Rules (apply to all contributors, human and agent)

1. **No bash without authorization** — ask before running any shell command that starts/stops a process, modifies the database, or is destructive.
2. **No mock code, no fallbacks** — catch real errors; never simulate API responses or hide failures.
3. **No hardcoded secrets** — all credentials via environment variables. Add new ones to `.env.example`.
4. **Alembic for every schema change** — never run `CREATE TABLE` manually.
5. **RLS on every new Postgres table** — keyed on `workspace_id`, no exceptions.
6. **Backup before editing** — timestamped copy to `zscripts/backup/` before touching any existing file.
7. **Small files** — new code files under 400 lines; split if larger.
8. **Tests before code** — write failing tests first; never modify tests to make them pass.
9. **OpenTelemetry on every new service boundary** — propagate `trace_id` on all log lines.
10. **Stripe webhook idempotency** — check `event.id` before processing; handlers must be side-effect-free on replay.
