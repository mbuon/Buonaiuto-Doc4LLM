# Buonaiuto Doc4LLM

A local-first documentation retrieval server for AI coding assistants. It fetches official documentation from the web, indexes it locally, and serves it to LLMs through the **Model Context Protocol (MCP)** — giving your AI assistant accurate, version-aware, citation-ready answers instead of hallucinated APIs.

---

## Why this exists

LLMs are trained on a snapshot of the internet. By the time you use them, the documentation for the libraries you depend on has moved on — new APIs, deprecated patterns, breaking changes. The model does not know.

Buonaiuto Doc4LLM solves this by:

- **Auto-detecting your project's technologies** from `package.json`, `pyproject.toml`, `requirements.txt`, and other manifests — no manual configuration needed
- **Ingesting local documentation instantly** — if your project already contains `llms.txt` or `llms-full.txt` files anywhere in the directory tree, they are copied directly into the index without any HTTP requests
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
Your project (package.json, pyproject.toml, llms.txt, …)
        │
        │  install_project / MCP initialize
        │  1. detect technologies from manifests
        │  2. copy local llms.txt files instantly (no HTTP)
        │  3. fetch remaining docs from the web
        │  4. auto-discover unknown libraries
        ▼
Official docs websites          Local llms.txt files
        │                               │
        │  HTTP fetch                   │  direct copy
        │  (ETag / If-Modified-Since)   │  (no network needed)
        │  + linked page downloading    │
        └───────────────┬───────────────┘
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

1. **Fetch layer** — on first use, auto-detects your project's libraries. Any `llms.txt` / `llms-full.txt` files already present in the project are copied immediately without HTTP requests. Remaining libraries are fetched from the web and kept fresh on a daily schedule. Unknown libraries are discovered automatically.
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

There are four ways to get documentation into the server — from a built-in registry, from any site that publishes `llms.txt`, from GitHub, or from your own local files.

### Built-in registry (19 libraries, zero configuration)

These are fetched automatically when your project uses them:

| ID | Library | Source |
|---|---|---|
| `nextjs` | Next.js | nextjs.org/llms-full.txt |
| `react` | React | react.dev/llms-full.txt |
| `vercel-ai-sdk` | Vercel AI SDK | ai-sdk.dev/llms-full.txt |
| `typescript` | TypeScript | typescriptlang.org/llms-full.txt |
| `tailwindcss` | Tailwind CSS | tailwindcss.com/llms-full.txt |
| `vite` | Vite | vite.dev/llms-full.txt |
| `shadcn-ui` | shadcn/ui | ui.shadcn.com/llms-full.txt |
| `fastapi` | FastAPI | fastapi.tiangolo.com/llms-full.txt |
| `pydantic` | Pydantic | docs.pydantic.dev/llms-full.txt |
| `sqlalchemy` | SQLAlchemy | docs.sqlalchemy.org/llms-full.txt |
| `pytest` | pytest | docs.pytest.org/llms-full.txt |
| `langchain` | LangChain | python.langchain.com/llms-full.txt |
| `openai` | OpenAI SDK | platform.openai.com/docs/llms-full.txt |
| `anthropic` | Anthropic SDK | docs.anthropic.com/llms-full.txt |
| `supabase` | Supabase | supabase.com/llms-full.txt |
| `huggingface-transformers` | Hugging Face Transformers | huggingface.co/docs/transformers/llms-full.txt |
| `docker` | Docker | docs.docker.com/llms-full.txt |
| `stripe` | Stripe | docs.stripe.com/llms-full.txt |
| `python` | Python stdlib | docs.python.org/3/llms-full.txt |

Each entry tries `llms-full.txt` first (complete docs), falls back to `llms.txt` (index), then GitHub source as a last resort.

### Any library with an llms.txt endpoint

The `llms.txt` standard is being adopted rapidly across the developer ecosystem. If a library's documentation site publishes an `llms.txt` or `llms-full.txt` file, you can fetch it directly:

```bash
# Fetch by explicit URL — any library, anywhere on the web
python -m buonaiuto_doc4llm fetch --technology django
```

If `django` isn't in the registry, the server searches for its official docs site, probes for `llms-full.txt` / `llms.txt`, downloads the content, and saves the source to `registry.json` for next time. No manual configuration needed.

Libraries with known `llms.txt` support include (but are not limited to): Django, Flask, Astro, SvelteKit, Nuxt, Vue, Angular, Remix, Prisma, Drizzle, tRPC, Zod, Axios, Lodash, Express, NestJS, Hono, Starlette, Celery, Redis, Elasticsearch, and hundreds more as adoption grows.

### GitHub source

For libraries whose docs live in a GitHub repository rather than a website, the registry supports `github://` sources:

```
github://owner/repo/branch/path/to/docs
```

Examples already in the registry:
- `github://microsoft/TypeScript-Website/v2/packages/documentation/copy/en`
- `github://fastapi/fastapi/master/docs/en/docs`
- `github://pytest-dev/pytest/main/doc/en`
- `github://python/cpython/main/Doc`
- `github://tailwindlabs/tailwindcss.com/main/src/docs`

To add any GitHub-hosted documentation, add an entry to `src/ingestion/registry.json` following the same format.

### Local llms.txt files (automatic, no HTTP)

If your project already contains `llms.txt` or `llms-full.txt` files anywhere in the directory tree, they are detected and copied into the index automatically when you run `install_project` — no HTTP requests needed.

**Detection rules:**

- `llms.txt` or `llms-full.txt` at the **project root** → technology ID = project folder name
- Same files inside a **subdirectory** (any depth) → technology ID = immediate parent directory name

```
myproject/
├── llms.txt                         → technology: "myproject"
├── docs/
│   ├── django/
│   │   └── llms-full.txt            → technology: "django"
│   └── celery/
│       └── llms.txt                 → technology: "celery"
└── vendor/
    └── internal-api/
        └── llms-full.txt            → technology: "internal-api"
```

`llms-full.txt` takes priority over `llms.txt` when both exist in the same directory.

Technologies satisfied by local files are **skipped during web fetch** — the server never makes unnecessary HTTP requests for docs it already has.

### Any local documentation files

Drop `.md`, `.mdx`, `.txt`, `.rst`, or `.json` files directly into `docs_center/technologies/<tech>/` and run:

```bash
python -m buonaiuto_doc4llm scan
```

This works for internal documentation, private libraries, API specs, architecture docs, or any content you want your AI to have access to.

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
| `resolve_observed_packages` | Probe unresolved packages for llms.txt URLs and auto-fetch their docs |
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

# With local vector search (sentence-transformers + cross-encoder reranker)
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

# With semantic search (sentence-transformers + BM25 hybrid + cross-encoder reranking)
claude mcp add --scope project buonaiuto-doc4llm \
  /opt/anaconda3/bin/python \
  -- -m buonaiuto_doc4llm \
     --base-dir /path/to/Buonaiuto-Doc4LLM \
     serve --embeddings

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

The most powerful feature: point the server at any project and it figures out everything else automatically.

### How it works

When you run `install_project` (CLI or MCP tool), or when an MCP client opens a workspace, the server runs through six detection layers and then fetches docs:

1. **Parse all manifests** — reads every dependency file it finds in the project root (see [Supported project types](#supported-project-types) below). Maps package names to library IDs via the built-in registry.

2. **Scan config file hints** — presence of files like `vite.config.ts`, `tailwind.config.js`, or `supabase/config.toml` implies the matching technology even without a manifest entry.

3. **Ingest local llms.txt files** — any `llms.txt` or `llms-full.txt` found anywhere in the project tree is copied directly into `docs_center/technologies/<tech>/` with no HTTP requests.

4. **Fetch remaining docs from the web** — technologies not satisfied locally are downloaded from official sources (e.g. `https://react.dev/llms-full.txt`). Uses conditional HTTP (ETag / If-Modified-Since) — unchanged sources are skipped.

5. **Record every raw package name** — all package names from all manifests are persisted to the `observed_packages` table, including those that had no registry match. This is the input for the auto-discovery step.

6. **Index everything and create the project subscription** — SHA-256 diffs new files, writes change events to SQLite, and writes `docs_center/projects/<id>.json` with the detected technology list.

**File-extension fallback** — if a project has no manifest files at all (e.g. a bare Python or Go project without a lockfile), the server infers the language from the source file extensions present (`.py` → `python`, `.go` → `go`, `.rs` → `rust`, `.ts` → `typescript`, etc.).

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

## Supported project types

The server reads dependency manifests from all major language ecosystems. The top 90% of real-world projects are covered by the first five.

| Ecosystem | Manifest file(s) | Package registry |
|---|---|---|
| **JavaScript / TypeScript** | `package.json` | npm |
| **Python** | `requirements.txt`, `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile` | PyPI |
| **Go** | `go.mod` | pkg.go.dev |
| **Rust** | `Cargo.toml` | crates.io |
| **Java / Kotlin** | `pom.xml` (Maven), `build.gradle` / `build.gradle.kts` (Gradle) | Maven Central |
| **Ruby** | `Gemfile` | RubyGems |
| **PHP** | `composer.json` | Packagist |
| **Dart / Flutter** | `pubspec.yaml` | pub.dev |
| **C# / .NET** | `*.csproj`, `packages.config` | NuGet |

Projects may use any combination of these — a monorepo with both `package.json` and `pyproject.toml` is handled correctly.

### Projects with no manifest

Some projects have no package manager at all: a bare Python script directory, a Go service without modules, a Rust project mid-init. For these, the server falls back to scanning source file extensions:

| Extension | Inferred technology |
|---|---|
| `.py` | `python` |
| `.go` | `go` |
| `.rs` | `rust` |
| `.ts`, `.tsx` | `typescript` |
| `.java`, `.kt` | `java` |
| `.rb` | `ruby` |
| `.php` | `php` |
| `.cs` | `dotnet` |
| `.swift` | `swift` |
| `.dart` | `dart` |
| `.ex`, `.exs` | `elixir` |

Standard non-source directories (`node_modules`, `.git`, `__pycache__`, `vendor`, `target`, etc.) are skipped during this scan.

---

## Auto-discovery of unknown libraries

If a project uses a library that is **not in the built-in registry**, the server does not give up — it remembers it and tries to discover the documentation later.

### How it works

Every package name seen during `install_project` — whether matched to a known library or not — is written into the `observed_packages` table in SQLite. The table records the package name, its ecosystem (`npm`, `pypi`, `cargo`, etc.), which project first used it, and when it was first seen.

When `resolve_observed_packages` runs (automatically as a side effect of `scan_docs`, or explicitly via the MCP tool), the server:

1. Queries for unresolved packages not attempted in the last 24 hours
2. For each, probes a set of candidate `llms.txt` URL patterns — for example for an npm package named `framer-motion`:
   - `https://framer-motion.dev/llms-full.txt`
   - `https://framer-motion.io/llms-full.txt`
   - `https://docs.framer-motion.dev/llms-full.txt`
   - `https://framer-motion.js.org/llms-full.txt`
   - … and several more variants
3. If a URL returns plain-text content (HTML responses are rejected), the docs are downloaded, written to `docs_center/technologies/<id>/`, and indexed immediately
4. The package is marked `resolved` in the DB with the discovered technology ID and URL

This means the technology list grows automatically from real usage — no manual registry maintenance required. The 24-hour cooldown prevents hammering external domains on every scan.

### Trigger it manually

```json
{"name": "resolve_observed_packages", "arguments": {"limit": 50}}
```

Returns:
```json
{
  "resolved": [{"package_name": "framer-motion", "ecosystem": "npm", "technology": "framer-motion", "url": "https://..."}],
  "failed":   [{"package_name": "some-internal-lib", "ecosystem": "npm"}],
  "skipped":  3
}
```

Discovery errors never block the rest of the installation — `failed` entries are retried automatically on the next scan.

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

A full web interface for managing every aspect of the server. Built with FastAPI + Jinja2 + HTMX — real-time actions without page reloads.

**Start standalone:**

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm dashboard
# Opens at http://127.0.0.1:8420
```

**Start alongside the MCP server (single command):**

```bash
PYTHONPATH=src python -m buonaiuto_doc4llm --base-dir . serve --dashboard
```

Custom address: `serve --dashboard --dashboard-host 0.0.0.0 --dashboard-port 9000`

Requires: `pip install -e ".[dashboard]"`

### Pages

#### Overview `/`
- Total document count, technology count, project count, event count
- List of all indexed libraries with version and document count
- Last 10 activity events
- Scheduler status (active / inactive)

#### Technologies `/technologies`
- All indexed libraries with document count, last scan date, last fetch date, and status
- Full registry of supported libraries (all 19 built-in entries)
- **Scan** button — rescan the local mirror immediately
- **Fetch All** button — download fresh docs from the web for all libraries (streamed progress)
- **Fetch** button per library — fetch a single library on demand
- **Index** button per library — build vector embeddings for Qdrant search

#### Query `/query`
- Full-text search across all indexed documentation
- Filter by technology
- Inline document viewer — read the full content of any result without leaving the page
- Shows whether vector search (Qdrant) or lexical search (BM25) is active
- Displays indexed vector count when Qdrant is available

#### Documents `/documents`
- Browse every indexed document across all technologies
- Filter by technology or search by path/title
- **Preview** button — loads the document inline at the bottom of the page without leaving the list
- **Open** button — opens the document on its own full page (`/documents/<tech>/<path>`)
  - **Rendered** view — Markdown rendered to HTML: headings, code blocks, tables, links all styled
  - **Source** view — raw file content as plain text
  - Toggle between the two views with the buttons in the top-right corner
  - Shows technology, relative path, version, character count, and last-scanned timestamp in the header
- Shows file size for each document

#### Projects `/projects`
- All registered projects with their technology subscriptions
- Unread update count per project
- **Install Project** form — enter a project path to auto-detect technologies and bootstrap docs
- **Acknowledge** button — mark all updates as read for a project

#### Activity `/activity`
- Full timeline of all documentation change events (added, updated, deleted)
- Filter by technology or event type
- Shows timestamp, technology, relative path, and event type for each entry

#### Fetch & Schedule `/schedule`
- **Install schedule** — set up a daily automatic fetch via macOS launchd or Linux crontab
- Configure hour and minute for the daily run (default: 04:00)
- **Uninstall schedule** — remove the cron job
- Current schedule status
- Fetch state table — ETag, Last-Modified, and last fetch timestamp per technology
- Manual **Scan** and **Fetch All** buttons

---

## Running the tests

```bash
pytest                        # all 352 tests
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
| Qdrant dense-only search | Qdrant dense + BM25 sparse RRF hybrid + cross-encoder reranking ✓ |
| stdio MCP | Streamable HTTP MCP transport |
| Local fetch schedule | Ingestion worker with source trust scoring |
| Project JSON files | Workspace subscriptions with API key auth |
| Python CLI | Full SaaS control plane |

**Retrieval quality implemented:**
- H1 + H2/H3 boundary chunking (finer-grained topic chunks)
- Query-time best-passage snippet extraction (not fixed 400-char prefix)
- Cross-encoder reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`) when sentence-transformers is installed
- BM25 sparse vectors + dense RRF hybrid search when qdrant-client ≥ 1.7
- `--embeddings` CLI flag for `serve` and `fetch` to activate semantic search
- 50+ benchmark cases across 6 libraries; eval harness with MRR@10 gate

**Phase 1 target:** MRR@10 ≥ 0.70 on the seed library benchmark set.
