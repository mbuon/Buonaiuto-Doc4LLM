# Offline Docs Hub

This repository contains a local-first prototype for tracking documentation updates for selected technologies and exposing them to an LLM through a minimal MCP-compatible stdio server.

## What it solves

You asked for a service that:

- keeps documentation in one central location
- works without internet access at LLM runtime
- knows which projects care about which technologies
- tells the LLM that updated documentation exists
- lets the LLM read the updated local documentation through MCP

This prototype does exactly that.

## Important constraint

If the service never accesses the internet, it cannot discover upstream updates by itself. It can only detect updates that have already been copied into the central documentation repository.

That means the architecture has two layers:

1. A sync layer outside the LLM path
   This can be manual copy, `git pull` from mirrored docs repos, `rsync` from a NAS, or another internal pipeline.
2. This offline docs hub
   It scans the local docs mirror, records changes, maps them to projects, and serves them through MCP.

## Repository layout

```text
docs_center/
  technologies/
    react/
      manifest.json
      docs/
        server-components.md
    python/
      manifest.json
      docs/
        pathlib.md
    vercel/
      manifest.json
      docs/
        functions.md
  projects/
    frontend-app.json
state/
src/docs_hub/
tests/
```

## Core concepts

- `technologies/<tech>` stores the local source of truth for a technology.
- `manifest.json` gives optional metadata like display name and version.
- `projects/*.json` declares project subscriptions.
- SQLite stores indexed documents, update events, and project cursors.
- MCP exposes:
  - updates for a project
  - document search
  - document read
  - prompt generation for newly updated docs

## Project subscription example

`docs_center/projects/frontend-app.json`

```json
{
  "project_id": "frontend-app",
  "name": "Frontend App",
  "technologies": ["react", "vercel"]
}
```

## Technology manifest example

`docs_center/technologies/react/manifest.json`

```json
{
  "technology": "react",
  "display_name": "React",
  "version": "19.2",
  "description": "Local mirror of React documentation"
}
```

## Commands

Use `PYTHONPATH=src` when running locally without installing the package.

### Scan the docs center

```bash
PYTHONPATH=src python3 -m docs_hub scan
```

### List updates for a project

```bash
PYTHONPATH=src python3 -m docs_hub updates frontend-app
```

### Acknowledge updates for a project

```bash
PYTHONPATH=src python3 -m docs_hub ack frontend-app
```

### Search local docs

```bash
PYTHONPATH=src python3 -m docs_hub search react server
```

### Read a local doc

```bash
PYTHONPATH=src python3 -m docs_hub read-doc react docs/server-components.md
```

### Watch the central docs folder for changes

```bash
PYTHONPATH=src python3 -m docs_hub watch
```

### Run the MCP server over stdio

```bash
PYTHONPATH=src python3 -m docs_hub serve
```

## MCP surface

The server implements a minimal JSON-RPC/MCP-style interface over stdio with:

- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`
- `prompts/list`
- `prompts/get`

Key tools:

- `scan_docs`
- `list_project_updates`
- `ack_project_updates`
- `read_doc`
- `search_docs`

Key prompt:

- `documentation_updates_summary`

It generates a prompt telling the model which local documentation changed and which local document URIs should be read.

## How this fits a production setup

Recommended production split:

1. Sync job
   Mirrors official docs into `docs_center/technologies/...`.
2. Indexer
   Runs `scan` on a timer or via file watcher.
3. MCP server
   Reads the indexed state and serves tools/resources/prompts to the LLM.
4. Project policy
   Each project subscribes to technologies directly or from its dependency manifest.

## Next extensions

- infer project technologies from `package.json`, `pyproject.toml`, or lockfiles
- chunk and embed documents for semantic search
- record section-level diffs instead of file-level diffs
- add a sync adapter for internal mirrored documentation sources
- expose approval rules, severity levels, and “must-read” updates

