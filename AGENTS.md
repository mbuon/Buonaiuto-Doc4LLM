# AGENTS.md

Codex multi-agent execution guide for this repository.

This file translates the repository guidance in [CLAUDE.md](/Users/massimo/Projects_Massimo/Documentation_LLMs/CLAUDE.md), [CLAUDE.agents.md](/Users/massimo/Projects_Massimo/Documentation_LLMs/CLAUDE.agents.md), and [docs/architecture/plan.md](/Users/massimo/Projects_Massimo/Documentation_LLMs/docs/architecture/plan.md) into Codex-native operating rules.

Use this file when executing the architecture plan with `spawn_agent`, `update_plan`, `multi_tool_use.parallel`, and the repository toolchain.

---

## Project Purpose

Build a Context7-like documentation retrieval platform for coding agents and developers:

- version-aware
- citation-friendly
- MCP-first
- Qdrant-first for MVP
- SaaS-ready control plane
- local and enterprise-compatible, including optional Ollama support

The current seed prototype lives in `src/docs_hub/`.
The target architecture is defined in `docs/architecture/plan.md` (currently v6).

---

## Source of Truth

When instructions conflict, apply this precedence:

1. direct user request
2. system and developer instructions in Codex
3. `AGENTS.md`
4. `CLAUDE.md`
5. `CLAUDE.agents.md`
6. inline comments and local conventions

When `AGENTS.md` and `CLAUDE*.md` differ, prefer Codex-native behavior while preserving repository invariants.

---

## Codex Operating Model

### Default Strategy

- Keep the critical path local unless delegation materially shortens the task.
- Delegate bounded, independent subtasks with disjoint write scopes.
- Use `update_plan` for any task that is not trivially one-step.
- Use `multi_tool_use.parallel` for parallel reads, searches, and independent checks.
- Use `spawn_agent` only when the user explicitly asks for delegation, parallel agent work, or when the current task is clearly multi-agent execution of the plan.

### Agent Types

- `explorer`: codebase questions, architecture inspection, locating integration points
- `worker`: implementation on a bounded write scope
- `default`: only when the task does not fit the above cleanly

### Model Selection

Use these defaults when spawning Codex subagents:

| Task Type | Agent Type | Model | Reasoning |
|-----------|------------|-------|-----------|
| architecture, schema, security, retrieval design | `worker` or `default` | `gpt-5.4` | `high` |
| core implementation | `worker` | `gpt-5.4-mini` | `medium` |
| boilerplate, fixtures, migrations, docs cleanup | `worker` | `gpt-5.4-mini` | `low` |
| codebase inspection | `explorer` | `gpt-5.4-mini` | `medium` |
| final review of risky changes | `worker` | `gpt-5.4` | `high` |

Escalate to `gpt-5.4` when changing:

- schema and migrations
- auth or RLS
- billing and quota logic
- retrieval contracts
- trust scoring or prompt-injection defenses

---

## Non-Negotiable Repository Rules

These apply to every agent, including subagents.

1. Do not revert user changes unless explicitly requested.
2. Use `apply_patch` for manual file edits.
3. Backup every existing file before modifying it:
   `zscripts/backup/<same hierarchy>/<name>_<YYYYMMDD>_<HH>-<MM>-<SS>.<ext>`
4. Prefer tests first. Write or update failing tests before implementation where practical.
5. Do not add mock behavior or fake fallbacks that hide failures.
6. No hardcoded secrets. New environment variables must be documented.
7. Use Alembic for every Postgres schema change.
8. Every new workspace-scoped Postgres table must have RLS or equivalent isolation.
9. Every retrieval path must preserve `workspace_id`, `library_id`, and `version` filtering.
10. Stripe webhook handlers must be idempotent and keyed on `event.id`.
11. Add OTEL-ready trace propagation at new service boundaries.
12. Keep new files under roughly 400 lines when possible.

---

## Required Pre-Work

Before substantial implementation:

1. Read [CLAUDE.md](/Users/massimo/Projects_Massimo/Documentation_LLMs/CLAUDE.md).
2. Read the relevant sections of [docs/architecture/plan.md](/Users/massimo/Projects_Massimo/Documentation_LLMs/docs/architecture/plan.md).
3. Inspect the target code paths with `rg`, `sed`, or `read_text_file`.
4. If the task is multi-step, create a plan with `update_plan`.
5. If work can be parallelized cleanly, define ownership boundaries before spawning any worker.

Before modifying any existing file:

1. Create the timestamped backup.
2. Read the current file contents.
3. Announce the intended edit in `commentary`.

---

## Documentation and Logging Conventions

This repository already uses the `docs/claude.ai/` area for task continuity. Codex should preserve that convention unless the user requests a replacement.

### Prompt History

Append the original prompt to:

- `docs/claude.ai/List_Prompts_Executed.md`

Format:

```text
[Date and Time] - [prompt name]:
PROMPT: [original prompt text]
-----
```

### Task Logs

Use:

- `docs/claude.ai/tasks/<promptname>.txt`

Rules:

- resume existing logs when continuing work
- log errors immediately
- append `RESPONSE DONE SUCCESSFULLY!` on full completion

### Project Memory

Use:

- `docs/claude.ai/claude_memory.md`

Store durable project learnings there when they would help future sessions.

---

## Multi-Agent Dispatch Rules

### When to Delegate

Delegate only when the tasks are:

- independent
- materially useful
- bounded
- assigned to disjoint file sets

Good delegation examples:

- one worker on retrieval
- one worker on ingestion
- one worker on schema and migrations
- one explorer mapping current code integration points

Bad delegation examples:

- two workers editing the same service file
- delegating the exact next blocking step
- parallelizing speculative work before architecture is stable

### Ownership Rule

Each worker must have explicit ownership:

- files
- module boundary
- plane responsibility

One plane per agent:

- retrieval agents do not edit control-plane modules
- control-plane agents do not edit ingestion internals unless explicitly assigned
- frontend agents do not edit retrieval logic

### Wait Policy

- Do not `wait_agent` by reflex.
- Spawn workers, continue with non-overlapping local work.
- Only wait when blocked on a delegated result.

### Agent Prompt Minimum

Every spawned worker should receive:

- exact task
- owned files
- files it must not touch
- reminder that other agents may be working in parallel
- verification requirement before return

---

## Phase-Oriented Execution

The target roadmap comes from `docs/architecture/plan.md`.

### Phase 1: Local / MVP Core

Goal:

- replace naive local search with Qdrant-first hybrid retrieval
- add chunking, embeddings, source mapping, and benchmarkable retrieval

Recommended workers:

| Worker | Scope | Owned Files |
|--------|-------|-------------|
| Retrieval | Qdrant client, hybrid retrieval, payload filters | `src/retrieval/` |
| Embeddings | model provider, hosted/self-hosted embedding backends | `src/retrieval/` |
| Chunking | semantic chunker, AST/markdown parsing | `src/ingestion/chunker.py` and related tests |
| Ingestion | fetcher, scheduler, source mapper, freshness triggers | `src/ingestion/` |
| Eval | benchmark fixtures and evaluation harness | `tests/benchmark/`, `scripts/eval.py` |

Critical checks:

- cold-start behavior returns sparse-only results
- retrieval always carries `workspace_id`, `library_id`, `version`
- benchmark gate is explicit before declaring phase success

### Phase 2: SaaS Cloud Control Plane

Goal:

- add hosted API, workspaces, auth, quotas, billing, hosted MCP transport

Recommended workers:

| Worker | Scope | Owned Files |
|--------|-------|-------------|
| Auth/Workspaces | workspace model, memberships, auth integration, RLS | `src/control/` |
| API Keys/Quotas | API key issuance, hashing, quotas, rate limiting | `src/control/`, `src/api/` |
| Billing | Stripe checkout, billing state, webhook handlers | `src/control/billing.py`, `src/api/webhooks.py` |
| MCP HTTP | hosted MCP transport and API auth | `src/api/` |
| Frontend | dashboard, billing, key management, analytics shell | `frontend/` |

Critical checks:

- cross-workspace access is rejected
- quotas work per workspace and per key
- Stripe webhook processing is replay-safe

### Phase 3: Enterprise and Private RAG

Goal:

- private repo ingestion
- stronger trust scoring
- self-hosted enterprise deployment
- Ollama support

Recommended workers:

| Worker | Scope | Owned Files |
|--------|-------|-------------|
| Private Connectors | GitHub/GitLab ingestion, workspace binding | `src/ingestion/private_connector.py` |
| Trust Scoring | trust model, quarantine flow, admin review APIs | `src/ingestion/trust.py`, `src/api/admin.py` |
| Outbound Webhooks | event fanout, HMAC signing, retries | `src/control/webhooks_outbound.py` |
| Self-Hosted Models | Ollama provider, offline model routing | `src/retrieval/model_provider.py`, deployment files |

Critical checks:

- no private chunk leakage across workspaces
- trust-scored or quarantined content does not silently bypass policy
- offline deployment works without hosted model dependencies

---

## Current Architecture Priorities

The current plan standardizes on these principles:

1. Qdrant is the MVP retrieval default.
2. Vespa is a future scale-out option, not a co-equal Day 1 dependency.
3. Package release feeds are triggers only; canonical source mapping remains mandatory.
4. Local context discovery improves resolver quality but never overrides explicit user intent.
5. High-level MCP tools are primary:
   - `search_documentation`
   - `read_full_page`
   - `list_supported_libraries`
6. Lower-level MCP tools remain for deterministic and debugging use:
   - `resolve_library_id`
   - `query_chunks`
7. SaaS control plane and local data plane stay decoupled.
8. Ollama is optional and primarily for enterprise or local deployments, not the default hosted hot path.

---

## Verification Checklist

Before claiming completion on any coding task:

- run the relevant tests
- inspect changed files for contract violations
- confirm no accidental plane-boundary violations
- confirm backups exist for modified pre-existing files
- confirm docs updated if implementation changed behavior
- confirm no secrets or unsafe defaults were added

For retrieval changes:

- verify query filtering by workspace, library, and version
- verify cold-start behavior
- verify benchmark or fixture coverage

For control-plane changes:

- verify RLS or equivalent isolation
- verify quota and billing idempotency

For enterprise changes:

- verify offline/self-hosted paths do not depend on hosted providers

Always include a short implementation log in the final response for changed files.

---

## Codex-Specific Tips

- Prefer `multi_tool_use.parallel` for read-only inspection bursts.
- Prefer `spawn_agent` with `worker` for bounded implementation tasks.
- Prefer `spawn_agent` with `explorer` for codebase mapping questions.
- Reuse existing agents with `send_input` when continuing related work.
- Close agents once their results are integrated.
- Use absolute file paths in final references.

---

## Minimal Worker Prompt Template

Use this template when spawning workers:

```text
You own: <files/modules>
Do not edit: <other files/modules>
Task: <bounded task>
Constraints:
- read the current implementation before editing
- do not revert others' work
- preserve workspace/version filtering contracts
- run relevant tests or checks before returning
Return:
- summary of what changed
- files touched
- verification performed
```

---

End of AGENTS.md
