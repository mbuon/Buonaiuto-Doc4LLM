# CLAUDE.agents.md

Agent execution guide for Claude Code (claude.ai/code) working in this repository.
This file covers: mandatory behaviours, skill usage, parallel dispatch, worktree isolation, model selection, and phase-by-phase agent assignments.

Read `CLAUDE.md` first for project architecture and hard rules.

---

## Mandatory Behaviours

### 1. Skill Check Before Any Action

Before responding to any request — including clarifying questions — check whether a skill applies.
If there is even a 1% chance a skill is relevant, invoke it with the `Skill` tool before proceeding.

Key skills for this project:

| Situation | Skill to invoke |
|-----------|----------------|
| Starting any new feature or task | `superpowers:brainstorming` |
| About to write implementation code | `superpowers:writing-plans` |
| Executing a written plan | `superpowers:executing-plans` |
| 2+ independent tasks available | `superpowers:dispatching-parallel-agents` |
| Subagent executing its assigned task | `superpowers:subagent-driven-development` |
| About to claim work is complete | `superpowers:verification-before-completion` |
| Encountering a bug or test failure | `superpowers:systematic-debugging` |
| Implementation complete, ready to merge | `superpowers:finishing-a-development-branch` |
| Starting feature work needing isolation | `superpowers:using-git-worktrees` |
| Receiving code review feedback | `superpowers:receiving-code-review` |

### 2. Extended Thinking for High-Stakes Decisions

Use extended thinking (budget ≥ 8000 tokens) before making decisions that are hard to reverse:

- Resolver priority order changes
- Schema migrations
- Security control design (RLS policies, quota enforcement)
- Chunking strategy adjustments
- Breaking changes to MCP tool contracts

State your reasoning explicitly before acting. Do not silently pick an option.

### 3. Activity Logging

After completing each activity, immediately append a record to the per-prompt log file:

- **Location**: `docs/claude.ai/tasks/[promptname].txt`
- Extract prompt name from `promptname="<value>"` or `pn="<value>"`. If absent, use the first 20 characters of the prompt.
- If the file already exists: read it first, then append the new prompt and continue from where work stopped.
- If the file does not exist: create it with the prompt name as the first line.
- On any error: write the error to the log file immediately.
- On full completion: append exactly `RESPONSE DONE SUCCESSFULLY!`
- Never move to the next activity without logging the last completed one.

### 4. Prompt Continuity Check

At session start, determine whether this is:
- A new task
- A continuation of previous incomplete work
- A consequence of prior output

Check `docs/claude.ai/tasks/` for an existing log matching the prompt name. If found, resume from the last logged step.

### 5. ALERT! Discrepancy Reporting

When any documentation, plan, or expectation is inconsistent with observed reality in the codebase, output:

```
ALERT! [description of discrepancy]
```

Never silently ignore mismatches between spec and implementation.

### 6. Meta-Reasoning Memory

Two memory layers are available:

**a) Project-local memory** — `docs/claude.ai/claude_memory.md`
For project-specific learnings: mistakes made, corrective lessons, architectural discoveries, negative feedback received. Update after any session that produced a meaningful learning. Write honestly — this file is private agent memory.

**b) Native Claude memory** — `~/.claude/projects/-Users-massimo-Projects-Massimo-Documentation-LLMs/memory/`
For persistent cross-session facts. Use typed entries (user, feedback, project, reference). Save here when something should survive across many future sessions (user preferences, stable architectural decisions, confirmed conventions).

Read relevant sections at session start when the task may benefit from prior learnings.

### 7. File Backup Before Editing

Before modifying any existing file:
1. Create a timestamped backup: `zscripts/backup/[same hierarchy]/[filename]_[YYYYMMDD]_[HH]-[MM]-[SS].[ext]`
2. If the backup fails, stop and output: `**** Backup copy failed: [path] ****`

### 8. Verification Before Completion

Before claiming any task is done, invoke `superpowers:verification-before-completion`.
Do not say "done", "complete", or "passing" without running this check first.

### 9. Bug Hunt at Task Completion

After any task that creates or modifies files:
1. Output `CHECK FOR BUGS`
2. Review all created/modified files for: logic errors, missing error handling, security gaps, contract violations with the architecture plan
3. Output findings
4. Output `CHECK FOR BUGS ENDED`

### 10. Implementation Log

At the end of every response that produces code changes:

```
[fullpath/filename]: [Action taken, max 200 characters]
```

### 11. Prompt History

At the end of every response, append the original prompt verbatim to `docs/claude.ai/List_Prompts_Executed.md`:

```
[Date and Time] - [prompt name]:
PROMPT: [original prompt text]
-----
```

### 12. Documentation Update

At end of each task: identify which docs need updating, update them (after backup). Docs describe current implementation state — not work summaries. No new doc files unless explicitly authorized. No source code duplication in docs.

---

## Using context7 for Third-Party Libraries

**Always** resolve a library's canonical ID before reading its docs. Never guess IDs.

```
# Pattern for any library
mcp__plugin_context7_context7__resolve-library-id: { "libraryName": "<name>" }
mcp__plugin_context7_context7__query-docs: { "context7CompatibleLibraryID": "<id>", "topic": "<specific topic>" }
```

Common lookups for this project:

| Library | Topic examples |
|---------|---------------|
| `qdrant-client` | `hybrid search sparse dense upsert`, `payload filter collection` |
| `cohere` | `embed-english-v3.0 batch embedding`, `rerank` |
| `celery` | `task routing Redis broker`, `beat scheduler` |
| `alembic` | `autogenerate migrations env.py`, `upgrade downgrade` |
| `supabase` | `row level security auth.uid policy`, `realtime` |
| `stripe` | `checkout session webhook construct_event`, `trial_period_days` |
| `fastapi` | `middleware dependency injection`, `background tasks SSE` |
| `opentelemetry-sdk` | `trace span context propagation`, `OTLP exporter` |

---

## Worktree Isolation

Every parallel agent **must** work in its own git worktree. Invoke `superpowers:using-git-worktrees` before starting any feature branch work.

Branch naming:
- Phase 1 agents: `feature/phase1-<agent-id>` (e.g., `feature/phase1-retrieval`)
- Phase 2 agents: `feature/phase2-<agent-id>`
- Phase 3 agents: `feature/phase3-<agent-id>`

When using the `Agent` tool to dispatch subagents, set `isolation: "worktree"` so each agent gets its own isolated copy of the repository. This prevents file conflicts between parallel agents working on the same phase.

```
Agent(
  subagent_type="...",
  isolation="worktree",
  prompt="..."
)
```

---

## Model Selection for Subagents

Specify the right model per agent to control cost and quality:

| Agent task | Model | Reason |
|-----------|-------|--------|
| Architecture decisions, schema design, security design | `opus` | High reasoning required |
| Core implementation (retrieval, embedder, chunker) | `sonnet` (default) | Balanced |
| Boilerplate, migrations, config files, test fixtures | `haiku` | Fast and cheap |
| Benchmark harness, eval scripts | `sonnet` | Logic-heavy but not architectural |
| Frontend scaffolding, UI components | `sonnet` | Design judgement needed |

Pass `model: "opus"` / `model: "haiku"` in the Agent tool call to override the default.

---

## Phase-by-Phase Agent Dispatch

### Phase 1 — Local / MVP Core

**Goal:** Replace naive `search_docs()` with hybrid Qdrant retrieval. Exit gate: MRR@10 ≥ 0.70.

**Dispatcher flow:**
1. Invoke `superpowers:brainstorming` to confirm scope.
2. Invoke `superpowers:writing-plans` to produce a written plan before any code.
3. Invoke `superpowers:dispatching-parallel-agents` to assign the agents below.
4. After all agents return, run `python scripts/eval.py` and verify MRR@10 ≥ 0.70.
5. Invoke `superpowers:verification-before-completion` before declaring Phase 1 done.

**Agent assignments:**

| Agent | Model | Task | File scope |
|-------|-------|------|------------|
| A1 — Retrieval | `sonnet` | Qdrant client wrapper, hybrid query (dense + sparse), payload filtering by `library_id`/`version`/`workspace_id`. Expose `HybridRetriever`. | `src/retrieval/qdrant_client.py`, `src/retrieval/retriever.py` |
| A2 — Embeddings | `sonnet` | `ModelProvider` protocol, Cohere `embed-english-v3.0` + BGE-M3 backends, batch upsert, cold-start sparse-first strategy, `embedding_status` flag. | `src/retrieval/embedder.py`, `src/retrieval/model_provider.py` |
| A3 — Chunker | `sonnet` | Markdown/AST-first chunking, heading+prose as one chunk, code blocks never severed, 300–800 token target. Wire into `scan()`. | `src/ingestion/chunker.py` |
| A4 — Ingestion triggers | `sonnet` | `llms.txt` ETag poller, PyPI RSS watcher, npm change feed. Two-stage dedup (ETag → SHA-256). Feed canonical source mapper. | `src/ingestion/fetcher.py`, `src/ingestion/scheduler.py` |
| A5 — Alembic + schema | `haiku` | Init Alembic, migration 0001: `libraries`, `library_versions`, `source_mappings`, `workspaces`. | `alembic/`, `alembic/versions/0001_initial.py` |
| A6 — Benchmark harness | `sonnet` | Eval script loading query/expected-doc pairs from `tests/benchmark/`, computing MRR@10 + nDCG@10, failing CI if MRR@10 < 0.70. | `tests/benchmark/`, `scripts/eval.py` |

**Dependencies:** A1 and A2 must complete before the integration step. A3, A4, A5, A6 are fully parallel.

**Integration agent** (runs after A1+A2): wires `DocsHubService.search_docs()` to `HybridRetriever`, removes substring fallback.

---

### Phase 2 — SaaS Cloud Control Plane

**Goal:** Multi-tenant hosted product with Stripe billing, API keys, quotas, hosted MCP over HTTP.

**Prerequisite:** Phase 1 benchmark gate passed.

**Dispatcher flow:**
1. Dispatch B1 first.
2. When B1 completes, dispatch B2–B6 in parallel (`isolation: "worktree"`).
3. Smoke-test Stripe: `stripe listen --forward-to localhost:8000/webhooks/stripe`.
4. Verify RLS: attempt cross-workspace query, confirm rejection.
5. Invoke `superpowers:verification-before-completion`.

**Agent assignments:**

| Agent | Model | Task | File scope |
|-------|-------|------|------------|
| B1 — Auth + workspaces | `opus` | Supabase auth, workspace CRUD, memberships, RLS policies on all control-plane tables. | `src/control/auth.py`, `src/control/workspaces.py`, `alembic/versions/0002_workspaces.py` |
| B2 — API keys + quotas | `sonnet` | API key issuance (hashed), daily quota counter (`quota:{workspace_id}:{YYYY-MM-DD}`, TTL midnight UTC), per-minute token bucket, HTTP 429. | `src/control/api_keys.py`, `src/control/quotas.py` |
| B3 — Stripe billing | `opus` | Checkout Sessions, `POST /webhooks/stripe`, `construct_event` validation, idempotent handlers keyed on `event.id`, grace-period on `invoice.payment_failed`. | `src/control/billing.py`, `src/api/webhooks.py` |
| B4 — HTTP MCP transport | `sonnet` | Upgrade `MCPServer` to also support Streamable HTTP. `POST /mcp` with SSE. Auth via API key header. | `src/api/mcp_http.py` |
| B5 — Next.js frontend | `sonnet` | Dashboard (usage, workspaces), onboarding, billing page (Checkout redirect), API key management. | `frontend/` |
| B6 — Abuse prevention | `haiku` | Email verification gate, IP-level rate limit (3 Free/IP/24h), key creation rate monitoring. | `src/control/abuse.py` |

---

### Phase 3 — Enterprise and Private RAG

**Goal:** Private repo connectors, workspace-scoped indexing, signed outbound webhooks, self-hosted models.

**All C agents are independent — dispatch all in parallel.**

| Agent | Model | Task | File scope |
|-------|-------|------|------------|
| C1 — Private connectors | `sonnet` | GitHub/GitLab webhook receiver, workspace-scoped ingestion, `workspace_id` on every Qdrant point and Postgres row. | `src/ingestion/private_connector.py` |
| C2 — Outbound webhooks | `sonnet` | HMAC-SHA256 signing, exponential backoff, unique `event_id`, dead-letter queue. | `src/control/webhooks_outbound.py` |
| C3 — Trust scoring | `opus` | Per-source trust score in registry, chunk-level pattern scoring, quarantine flow, operator review API. | `src/ingestion/trust.py`, `src/api/admin.py` |
| C4 — Self-hosted models | `sonnet` | Ollama backend for `ModelProvider`, BGE-reranker-v2-m3, Docker Compose for full offline stack. | `src/retrieval/model_provider.py` (extend), `docker-compose.enterprise.yml` |

---

## Per-Agent Execution Rules

Every subagent must follow these rules inside its assigned scope:

1. **Invoke `feature-dev:code-explorer` before writing.** Understand the existing structure before touching any file.
2. **Invoke `feature-dev:code-architect` for new modules.** Design the module before implementing it.
3. **One plane per agent.** Never cross plane boundaries. Retrieval agents do not touch `src/control/`. Ingestion agents do not touch `mcp_server.py`.
4. **Fetch context7 docs before any library call.** See the context7 table above. Do not rely on training data for library APIs.
5. **Tests first.** Write a failing test, then write code that passes it. `pytest` must pass before the agent returns.
6. **Invoke `superpowers:finishing-a-development-branch` before returning.** Ensures the branch is clean, tested, and ready for integration.
7. **Use `feature-dev:code-reviewer` on your own output** before signalling completion.

---

## Debugging Protocol

When any test fails or unexpected behaviour occurs:
1. Invoke `superpowers:systematic-debugging` immediately.
2. Do not retry the same failing approach more than twice.
3. If blocked after two attempts, surface to the orchestrating agent with full context.

---

## Completion Checklist (orchestrating agent)

Before declaring any phase complete:

- [ ] All assigned agents have returned without errors
- [ ] `pytest` passes with no failures
- [ ] Benchmark gate met (Phase 1: MRR@10 ≥ 0.70)
- [ ] No hardcoded secrets in any new file
- [ ] Every new Postgres table has an RLS policy
- [ ] Every new service boundary has OTEL instrumentation
- [ ] `superpowers:verification-before-completion` invoked and passed
- [ ] Implementation log written
- [ ] Prompt appended to `docs/claude.ai/List_Prompts_Executed.md`
- [ ] `RESPONSE DONE SUCCESSFULLY!` written to task log
