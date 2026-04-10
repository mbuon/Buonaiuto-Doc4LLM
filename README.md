# Buonaiuto Doc4LLM

A local-first documentation retrieval server for AI coding assistants. It fetches official documentation from the web, indexes it locally, and serves it to LLMs through the **Model Context Protocol (MCP)** — giving your AI assistant accurate, version-aware, citation-ready answers instead of hallucinated APIs.

---

## Why this exists

LLMs are trained on a snapshot of the internet. By the time you use them, the documentation for the libraries you depend on has moved on — new APIs, deprecated patterns, breaking changes. The model does not know.

Buonaiuto Doc4LLM solves this by:

- **Auto-detecting your project's technologies** from `package.json`, `pyproject.toml`, `requirements.txt`, and other manifests — no manual configuration needed
- **Fetching the missing documentation automatically** the first time it sees a library, then keeping it fresh on a daily schedule
- **Auto-discovering unknown libraries** — if a library isn't in the built-in registry, the server finds its official docs site, downloads them, and remembers the source for next time
- Detecting exactly what changed since the last fetch (added, updated, deleted files)
- Serving documents to any MCP-compatible AI tool with token-budget enforcement and section-level access
- Tracking which projects care about which technologies, so the AI only surfaces relevant updates
- Recording quality feedback per document to surface low-quality or stale content

The result is an AI assistant that reads the actual current documentation, not what it was trained on — with zero manual setup.

---

## How it works

```
Your project (package.json, pyproject.toml, …)
        │
        │  install_project / MCP initialize
        │  → detect technologies
        │  → fetch missing docs automatically
        │  → auto-discover unknown libraries
        ▼
Official docs websites
        │
        │  HTTP fetch (ETag / If-Modified-Since)
        │  + linked page downloading
        ▼
docs_center/technologies/<tech>/    ← local mirror
        │
        │  scan (SHA-256 diff)
        ▼
state/buonaiuto_doc4llm.db          ← SQLite: documents, events, projects, feedback
        │
        │  MCP / JSON-RPC over stdio
        ▼
AI coding assistant (Claude Code, Cursor, Windsurf, …)
```

There are two layers:

1. **Fetch layer** — on first use, auto-detects your project's libraries and downloads their docs. Subsequently keeps them fresh on a daily schedule or on demand. Works for any library — unknown ones are discovered automatically.
2. **Serve layer** — scans the local mirror, indexes changes, answers MCP tool calls. Works fully offline after the initial fetch.

---

## Technology stack

| Layer | Technology |
|---|---|
| MCP server | Python 3.11+, JSON-RPC 2.0 over stdio (protocol `2025-03-26`) |
| Storage | SQLite (WAL mode) via `sqlite3` stdlib |
| Vector search | [Qdrant](https://qdrant.tech/) (optional, local) + sentence-transformers or Ollama embeddings |
| Lexical search | BM25 with TF-IDF scoring and sqrt length normalization |
| Web dashboard | [FastAPI](https://fastapi.tiangolo.com/) + [Jinja2](https://jinja.palletsprojects.com/) + [HTMX](https://htmx.org/) |
| HTTP fetching | `requests` with conditional HTTP (ETag / If-Modified-Since) |
| Scheduling | macOS `launchd` or Linux `crontab` |
| Control plane (planned) | PostgreSQL / Supabase, Alembic migrations |
| Frontend (planned) | Next.js, TypeScript |
| Observability (planned) | OpenTelemetry |

---

## Supported libraries

Documentation can be fetched and indexed for these libraries out of the box:

| ID | Library |
|---|---|
| `nextjs` | Next.js |
| `react` | React |
| `vercel-ai-sdk` | Vercel AI SDK |
| `typescript` | TypeScript |
| `tailwindcss` | Tailwind CSS |
| `vite` | Vite |
| `shadcn-ui` | shadcn/ui |
| `fastapi` | FastAPI |
| `pydantic` | Pydantic |
| `sqlalchemy` | SQLAlchemy |
| `pytest` | pytest |
| `langchain` | LangChain |
| `openai` | OpenAI SDK |
| `anthropic` | Anthropic SDK |
| `supabase` | Supabase |
| `huggingface-transformers` | Hugging Face Transformers |
| `docker` | Docker |
| `stripe` | Stripe |
| `python` | Python standard library |

Any local documentation directory can also be indexed manually — just drop files into `docs_center/technologies/<tech>/`.

---

## MCP tools

| Tool | Description |
|---|---|
| `list_supported_libraries` | List all locally indexed libraries with version and freshness info |
| `search_documentation` | Cross-library search with version-aware filtering and per-library result counts |
| `search_docs` | Search within a single technology |
| `read_doc` | Read a document by path, with token budget, query-ranked sections, and TOC |
| `read_full_page` | Same as `read_doc` with library/version validation |
| `list_docs` | Browse all indexed documents for a technology |
| `fetch_docs` | Pull the latest docs from the web and re-index |
| `install_project` | Auto-detect technologies from a project path and bootstrap the local cache |
| `scan_docs` | Rescan the local mirror and record change events |
| `list_project_updates` | List unread documentation changes for a subscribed project |
| `ack_project_updates` | Mark updates as read (advance the project cursor) |
| `diff_since` | Show all changes since a given timestamp, with pagination |
| `submit_feedback` | Record whether a document answered your question (required after reads) |
| `list_feedback` | Browse quality feedback entries with time and technology filters |
| `feedback_stats` | Aggregate satisfaction rates and low-quality document detection |

---

## Installation

### Requirements

- Python 3.11 or later
- `/opt/anaconda3/bin/python` or any Python 3.11+ interpreter

### 1. Clone and install dependencies

```bash
git clone https://github.com/mbuon/Buonaiuto-Doc4LLM.git
cd Buonaiuto-Doc4LLM

# Minimal install (MCP server + CLI only)
pip install -e .

# With web fetching
pip install -e ".[fetch]"

# With web dashboard
pip install -e ".[dashboard]"

# With local vector search (sentence-transformers)
pip install -e ".[embeddings-st,qdrant]"

# Everything
pip install -e ".[fetch,dashboard,embeddings-st,qdrant]"
```

### 2. Fetch documentation

```bash
# Fetch all supported libraries
PYTHONPATH=src python -m buonaiuto_doc4llm fetch

# Fetch a specific library
PYTHONPATH=src python -m buonaiuto_doc4llm fetch --technology react
```

This downloads docs into `docs_center/technologies/` and indexes them into `state/buonaiuto_doc4llm.db`.

### 3. Connect your AI tool

---

#### Claude Code

The repository ships a `.mcp.json` file. Open the folder in Claude Code and the server starts automatically.

To add it manually from the terminal:

```bash
# Project scope (current project only)
claude mcp add --scope project buonaiuto-doc4llm \
  /opt/anaconda3/bin/python \
  -- -m buonaiuto_doc4llm \
     --base-dir /path/to/Buonaiuto-Doc4LLM \
     serve

# Global scope (all your projects)
claude mcp add --scope user buonaiuto-doc4llm \
  /opt/anaconda3/bin/python \
  -- -m buonaiuto_doc4llm \
     --base-dir /path/to/Buonaiuto-Doc4LLM \
     serve
```

---

#### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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

---

#### Cursor

Open **Settings → MCP** and add a new server entry:

```json
{
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
```

---

#### Windsurf

Edit `~/.codeium/windsurf/mcp_config.json`:

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

---

#### Zed

In your Zed `settings.json`:

```json
{
  "context_servers": {
    "buonaiuto-doc4llm": {
      "command": {
        "path": "/opt/anaconda3/bin/python",
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
}
```

---

#### OpenAI Codex CLI

Point Codex at the `.mcp.json` in the repo root, or add to your Codex config:

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

---

#### Cline (VS Code extension)

In VS Code settings, under Cline → MCP Servers, add:

```json
{
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
```

---

#### Continue (VS Code / JetBrains extension)

In your `~/.continue/config.json`:

```json
{
  "mcpServers": [
    {
      "name": "buonaiuto-doc4llm",
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
  ]
}
```

---

#### Any MCP-compatible client

The server speaks JSON-RPC 2.0 over `stdin`/`stdout`. Launch it with:

```bash
PYTHONPATH=/path/to/Buonaiuto-Doc4LLM/src \
  /opt/anaconda3/bin/python -m buonaiuto_doc4llm \
  --base-dir /path/to/Buonaiuto-Doc4LLM \
  serve
```

**Key rule:** `--base-dir` must always come *before* the `serve` subcommand.

---

## CLI reference

```bash
# Fetch documentation from the web
PYTHONPATH=src python -m buonaiuto_doc4llm fetch
PYTHONPATH=src python -m buonaiuto_doc4llm fetch --technology react
PYTHONPATH=src python -m buonaiuto_doc4llm fetch --interval 3600   # repeat every hour

# Scan the local mirror for changes
PYTHONPATH=src python -m buonaiuto_doc4llm scan

# Watch the local mirror and rescan on change (0.75 s debounce)
PYTHONPATH=src python -m buonaiuto_doc4llm watch

# Watch + periodic fetch combined
PYTHONPATH=src python -m buonaiuto_doc4llm watch-and-fetch --interval 86400

# Search indexed docs
PYTHONPATH=src python -m buonaiuto_doc4llm search react hooks
PYTHONPATH=src python -m buonaiuto_doc4llm search fastapi dependency injection

# Read a document
PYTHONPATH=src python -m buonaiuto_doc4llm read-doc react llms.txt

# List unread updates for a project
PYTHONPATH=src python -m buonaiuto_doc4llm updates my-project

# Acknowledge updates
PYTHONPATH=src python -m buonaiuto_doc4llm ack my-project

# Install a project (auto-detect technologies)
PYTHONPATH=src python -m buonaiuto_doc4llm install-project /path/to/my-project

# Schedule daily automatic fetch (macOS launchd / Linux crontab)
PYTHONPATH=src python -m buonaiuto_doc4llm schedule install           # 04:00 default
PYTHONPATH=src python -m buonaiuto_doc4llm schedule install --hour 2 --minute 30
PYTHONPATH=src python -m buonaiuto_doc4llm schedule status
PYTHONPATH=src python -m buonaiuto_doc4llm schedule uninstall

# Start the web dashboard standalone (http://127.0.0.1:8420)
PYTHONPATH=src python -m buonaiuto_doc4llm dashboard
PYTHONPATH=src python -m buonaiuto_doc4llm dashboard --port 9000
PYTHONPATH=src python -m buonaiuto_doc4llm dashboard --host 0.0.0.0

# Start the MCP server
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve

# Start the MCP server + dashboard together (dashboard at http://127.0.0.1:8420)
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve --dashboard
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve --dashboard --dashboard-port 9000
```

---

## Auto project setup — zero configuration required

The most powerful feature: point the server at your project and it figures out everything else automatically.

### How it works

When you run `install_project` (CLI or MCP tool), or when an MCP client opens a workspace, the server:

1. **Detects technologies** — reads `package.json`, `pyproject.toml`, `requirements.txt`, `Pipfile`, `go.mod`, and other manifests from the project path. Maps package names to library IDs via the built-in registry (e.g. `"ai"` or `"@ai-sdk/*"` → `vercel-ai-sdk`, `"fastapi"` → `fastapi`).

2. **Fetches missing documentation** — for each detected technology not yet in the local cache, downloads official docs from the authoritative source (e.g. `https://react.dev/llms-full.txt`). Uses conditional HTTP (ETag / If-Modified-Since) — sources that haven't changed are skipped.

3. **Indexes everything** — SHA-256 diffs the new files and writes `added` events to SQLite so the AI immediately knows what's new.

4. **Creates the project subscription** — writes `docs_center/projects/<id>.json` with the detected technology list. Future `list_project_updates` calls use this to surface only relevant changes.

### CLI

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm install-project /path/to/my-project
```

### MCP tool (called by the AI assistant)

```json
{"name": "install_project", "arguments": {"project_path": "/path/to/my-project"}}
```

### Auto-bootstrap on MCP initialize

If your MCP client sends workspace context in the `initialize` call (Claude Code does this automatically), the entire flow runs without any manual step — the server bootstraps the project on first connection.

---

## Auto-discovery of unknown libraries

If a project uses a library that is **not in the built-in registry**, the server does not give up. It automatically:

1. Searches for the library's official documentation site
2. Probes candidate domains for `llms-full.txt` / `llms.txt` endpoints
3. If found, **downloads the documentation** and indexes it
4. **Persists the new entry to `registry.json`** so future fetches work without searching again

This means the server handles any library — not just the 19 built-in ones. Discovery errors are reported in `fetch_errors` in the install result and do not block the rest of the installation.

---

## Linked page downloading

When the server fetches an `llms.txt` index file that contains markdown links to individual documentation pages, it automatically:

1. Parses all `[title](url)` links from the content
2. Filters to same-domain `.md` / `.mdx` / `.txt` / `.rst` URLs
3. Downloads each linked page into `docs_center/technologies/<tech>/docs/<path>`

This gives the AI access to the full content of every individual page — not just the index. Searching for "useState" finds the actual React hooks page, not just a mention in the `llms.txt` summary.

---

## Project subscriptions

Create `docs_center/projects/my-app.json` to manually define which technologies a project tracks:

```json
{
  "project_id": "my-app",
  "name": "My App",
  "technologies": ["react", "nextjs", "stripe"]
}
```

In practice you rarely need to write this by hand — `install_project` generates it automatically from your project's dependency manifests.

The `list_project_updates` and `ack_project_updates` tools use this to surface only relevant documentation changes to each project.

---

## Web dashboard

Browse indexed docs, run queries, inspect feedback stats, and manage the fetch schedule at `http://127.0.0.1:8420`.

**Standalone** (dashboard only):

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm dashboard
```

**Together with the MCP server** (single command, background thread):

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve --dashboard
```

The `--dashboard` flag starts uvicorn in a daemon thread. The MCP server continues to run on stdio unaffected. The dashboard URL is printed to stderr on startup.

Custom address: `serve --dashboard --dashboard-host 0.0.0.0 --dashboard-port 9000`

Requires the `dashboard` extra: `pip install -e ".[dashboard]"`.

---

## Running the tests

```bash
pytest                        # all 269 tests
pytest tests/test_service.py  # single file
pytest -k test_search         # filter by name
pytest --tb=short -q          # compact output
```

No database mocking — tests use real SQLite instances via `tmp_path`.

---

## Roadmap

| Current | Planned |
|---|---|
| SQLite | PostgreSQL / Supabase |
| Lexical BM25 search | Hybrid BM25 + dense Qdrant retrieval + cross-encoder reranking |
| stdio MCP | Streamable HTTP MCP transport |
| Local fetch schedule | Ingestion worker with source trust scoring |
| Project JSON files | Workspace subscriptions with API key auth |
| Python CLI | Full SaaS control plane |

**Phase 1 target:** MRR@10 ≥ 0.70 on the seed library benchmark set.
