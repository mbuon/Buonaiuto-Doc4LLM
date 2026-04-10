# Changelog

## 2026-04-08 — Major Feature + Bug Fix Release

### New Features

#### GitHub Docs Fetcher
- Added `github://owner/repo/branch/path` source type for libraries without `llms.txt`
- Fetches markdown/rst files directly from GitHub repos via the API
- Enabled documentation for TypeScript, Tailwind CSS, FastAPI, pytest, Python (all lacked llms.txt)
- Result: **2,169 documents across 20 libraries** (up from 4 stub docs)

#### Source Fallback in Fetcher
- `HttpDocFetcher.fetch()` now tries all source URLs in priority order instead of failing on first 404
- Added `ordered_sources()` to `CanonicalSourceMapper` (llms-full.txt > llms.txt > github://)

#### Semantic Vector Search (Qdrant + sentence-transformers)
- Integrated Qdrant local storage (no server needed) at `state/qdrant/`
- sentence-transformers (all-MiniLM-L6-v2) as primary embedding provider
- Ollama (nomic-embed-text) as fallback embedder
- **19,011 vectors indexed** across all libraries
- Hybrid search: vector similarity + lexical re-ranking for title/path relevance
- Created `vector_setup.py` — auto-wires Qdrant, embedders, and indexer on dashboard startup

#### Query Page (`/query`)
- New dashboard page for searching documentation (like MCP `search_docs` tool)
- HTMX-powered inline results with library filter and limit controls
- "Read full document" with **term highlighting** (amber marks) and auto-scroll to first match
- "Build Index" button to trigger vector indexing from the UI
- Shows retrieval mode (hybrid/lexical), vector count, active provider

#### Improved Lexical Search
- Multi-term scoring with stop word filtering (was exact phrase only)
- Coverage bonus for matching more distinct query terms
- Adjacent term pair bonus
- Smart snippet extraction showing best co-occurrence window

### Registry Updates
- Updated Anthropic URLs (added `/en/docs/` path)
- Updated Vercel AI SDK URLs (added `ai-sdk.dev` domain)
- Added GitHub fallback sources for TypeScript, Tailwind, FastAPI, pytest, Python
- Added Python as a new registry entry

---

### Bug Fixes (38 found, 35 fixed)

#### Critical (5)
- **C-1**: `search_documentation` no longer loads all document content into memory — tries hybrid search first, lexical fallback limited to 2,000 docs with OSError handling
- **C-2**: SQLite WAL mode + 10s timeout on all connections (service.py + http_fetcher.py) — prevents `database is locked` errors
- **C-3**: Path traversal fix — `_url_to_rel_path` now URL-decodes before `..` check, blocks `%2e%2e` attacks
- **C-4**: Replaced bare `except Exception` in hybrid search with explicit `NotImplementedError` catch + logged warning
- **C-5**: `OllamaEmbeddingProvider.is_available()` now probes the Ollama server instead of just checking if `requests` is installed

#### High (8)
- **H-1**: `api/scan` flash message used wrong key `documents_scanned` (always showed 0) — fixed to `documents_indexed`
- **H-2**: Thread-safe debounce in `RescanHandler` with `threading.Lock`
- **H-4**: LIKE wildcard injection — escaped `%` and `_` in search queries, added 200-char length limit
- **H-5**: `_sync_tree` now uses atomic copy (temp dir + rename) to prevent data loss on copy failure
- **H-7**: `read_doc` validates `source_path.is_relative_to(base_dir)` before reading files
- **H-8**: Linked page fetching limits: max 500 pages, 5MB per page, 100MB total
- **H-10**: Fixed version_filters logic — `None` (match all) can no longer be narrowed back to a specific version
- **M-4**: Fetch button on technologies page sent technology as query param but route expected Form — fixed route to use `Query`

#### Medium (10)
- **M-1**: Chunker now handles `~~~` code fences (not just `` ``` ``)
- **M-2**: Absolute max chunk size (1,500 words) enforced even inside code fences
- **M-3**: All route DB helpers use `with service._connect() as db:` context manager
- **M-7**: XML-escape all paths in launchd plist generation
- **M-9**: Removed stale `@lru_cache` on `_registry_package_to_technology_map` (was never invalidated after registry updates)
- **M-12**: `_parse_github_source` now handles `api.github.com/repos/.../git/trees/...` URL format
- **M-13**: Doc discovery `_probe_llms_txt` falls back to GET when HEAD returns 404/405/403

#### Low (7)
- **L-1**: Documented `_extract_title` duplication between `service.py` and `indexer.py`
- **L-4**: Cron line uses `shlex.quote()` for paths with spaces
- **L-7**: Documented dead `chunk_hashes` field in `SourceSnapshot`
- **L-10**: Pre-built set instead of per-iteration set comprehension in `scan()` missing-doc detection

### Files Modified
- `src/docs_hub/service.py` — WAL mode, hybrid-first search, path validation, version filter fix, set optimization
- `src/docs_hub/dashboard/__init__.py` — Vector setup integration
- `src/docs_hub/dashboard/routes.py` — Scan key fix, DB context managers, LIKE escaping, fetch param fix, index API, query API
- `src/docs_hub/dashboard/templates/base.html` — Query nav link
- `src/docs_hub/dashboard/templates/query.html` — New query page
- `src/docs_hub/dashboard/templates/partials/query_results.html` — Search results with highlighting
- `src/docs_hub/dashboard/templates/partials/doc_viewer.html` — Term highlighting + auto-scroll
- `src/docs_hub/dashboard/static/style.css` — Query page styles, highlight marks
- `src/docs_hub/vector_setup.py` — New: Qdrant + embedder wiring
- `src/docs_hub/indexer.py` — PointStruct conversion, UUID-style IDs, title dedup note
- `src/docs_hub/__main__.py` — Thread-safe debounce
- `src/docs_hub/auto_setup.py` — Atomic sync_tree, removed stale lru_cache
- `src/docs_hub/scheduler.py` — XML escaping, shlex quoting
- `src/ingestion/http_fetcher.py` — Source fallback, GitHub fetcher, path traversal fix, page limits
- `src/ingestion/source_mapper.py` — `ordered_sources()` with github:// support
- `src/ingestion/registry.json` — Updated URLs, GitHub fallbacks, Python entry
- `src/ingestion/chunker.py` — ~~~ fences, absolute max size
- `src/ingestion/doc_discovery.py` — HEAD→GET fallback
- `src/ingestion/fetcher.py` — Documented dead field
- `src/retrieval/retriever.py` — Multi-term scoring, hybrid re-ranking, snippet extraction, error logging
- `src/retrieval/qdrant_client.py` — Embedder-aware query, proper Qdrant filter models, optional library filter
- `src/retrieval/model_provider.py` — Ollama server probe
