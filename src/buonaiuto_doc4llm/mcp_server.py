from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from .project_bootstrap import ensure_project_installed, extract_workspace_path
from .service import DocsHubService

# Largest JSON-RPC input we accept from stdin. A malicious client can otherwise
# park the server on an unbounded read and exhaust memory.
MAX_JSONRPC_LINE_BYTES = 16 * 1024 * 1024  # 16 MB

# Per-client-field clamps for data taken from untrusted initialize params.
MAX_CLIENT_INFO_LEN = 256


class MCPServer:
    def __init__(self, base_dir: Path | str):
        base = Path(base_dir)
        retriever = None
        indexer = None
        try:
            from buonaiuto_doc4llm.vector_setup import create_qdrant_retriever_and_indexer
            vector_info = create_qdrant_retriever_and_indexer(base)
            retriever = vector_info.get("retriever")
            indexer = vector_info.get("indexer")
        except Exception:
            pass  # Fall back to lexical search
        self.service = DocsHubService(base, retriever=retriever, indexer=indexer)
        self._session_id: str | None = None
        self._session_project_id: str | None = None
        # Serialises mutations of _session_id / _session_project_id against
        # concurrent transports (future HTTP streamable MCP).
        self._session_lock = threading.Lock()

    def serve(self) -> None:
        for raw_line in sys.stdin:
            # Bound individual requests so a malicious client can't exhaust
            # memory by streaming an unbounded JSON-RPC line.
            if len(raw_line.encode("utf-8", errors="ignore")) > MAX_JSONRPC_LINE_BYTES:
                print(
                    f"[mcp_server] dropping oversized request "
                    f"({len(raw_line)} bytes)",
                    file=sys.stderr,
                )
                continue
            line = raw_line.strip()
            if not line:
                continue
            request_id = None
            try:
                request = json.loads(line)
                # Extract id before dispatch so error responses can include it
                # even when handle_request raises (JSON-RPC 2.0 §5).
                request_id = request.get("id")
                response = self.handle_request(request)
            except Exception as exc:  # pragma: no cover
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32000,
                        "message": str(exc),
                        "data": traceback.format_exc(),
                    },
                }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        result: Any
        if method == "initialize":
            bootstrap_summary = self._bootstrap_from_initialize_params(params)
            result = {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "Buonaiuto Doc4LLM", "version": "0.1.0"},
                "capabilities": {
                    "tools": {},
                    "resources": {},
                    "prompts": {},
                },
                "bootstrap": bootstrap_summary,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self._list_tools()}
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
        elif method == "resources/list":
            result = {"resources": self.service.list_resources()}
        elif method == "resources/read":
            resource = self.service.read_resource(params["uri"])
            result = {
                "contents": [
                    {
                        "uri": resource["uri"],
                        "mimeType": resource["mimeType"],
                        "text": resource["text"],
                    }
                ]
            }
        elif method == "prompts/list":
            result = {
                "prompts": [
                    {
                        "name": "documentation_updates_summary",
                        "description": "Tell the model which local documentation updates should be read for a project.",
                        "arguments": [
                            {
                                "name": "project_id",
                                "required": True,
                            },
                            {
                                "name": "limit",
                                "required": False,
                            },
                        ],
                    }
                ]
            }
        elif method == "prompts/get":
            if params["name"] != "documentation_updates_summary":
                raise ValueError(f"Unknown prompt: {params['name']}")
            args = params.get("arguments", {})
            prompt = self.service.build_update_prompt(
                project_id=args["project_id"],
                limit=int(args.get("limit", 10)),
            )
            result = {
                "description": "Prompt for local documentation updates",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": prompt,
                        },
                    }
                ],
            }
        else:
            raise ValueError(f"Unsupported method: {method}")

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "scan_docs",
                "description": (
                    "Scan the local documentation center and record new update events. "
                    "Returns {scanned_at, technologies, total_documents, total_events}."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "list_project_updates",
                "description": (
                    "List documentation updates for a project based on its technology subscriptions. "
                    "Returns {project_id, unseen_count, latest_event_id, last_seen_event_id, events}. "
                    "unseen_count is the total number of unread events (may exceed limit). "
                    "latest_event_id is the highest unread event ID — pass to ack_project_updates to mark all as read."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "unread_only": {"type": "boolean"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["project_id"],
                },
            },
            {
                "name": "ack_project_updates",
                "description": (
                    "Mark updates as seen for a project. "
                    "If through_event_id is omitted, ALL current updates are marked as read. "
                    "Pass a specific event ID to acknowledge only up to that point."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "through_event_id": {
                            "type": "integer",
                            "description": "Mark updates up to this event ID as read. If omitted, marks ALL updates as read.",
                        },
                    },
                    "required": ["project_id"],
                },
            },
            {
                "name": "read_doc",
                "description": (
                    "Read a local documentation document by technology and relative path. "
                    "Large documents are automatically truncated to fit the token budget. "
                    "Pass a query to prioritize the most relevant sections, or use section "
                    "to read a specific heading. When truncated, the response includes a "
                    "table_of_contents with all section names for targeted follow-up reads. "
                    "Response includes char_count, locale, last_scanned_at, and last_fetched_at metadata."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "technology": {"type": "string"},
                        "rel_path": {"type": "string"},
                        "max_tokens": {
                            "type": "integer",
                            "description": "Maximum tokens to return (default 20000). Sections are prioritized by query relevance when truncating.",
                            "default": 20000,
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional query to prioritize relevant sections when the document exceeds max_tokens.",
                        },
                        "section": {
                            "type": "string",
                            "description": "Read only the section matching this heading text (case-insensitive substring match). Use table_of_contents from a previous read to discover section names.",
                        },
                    },
                    "required": ["technology", "rel_path"],
                },
            },
            {
                "name": "read_full_page",
                "description": (
                    "Read a canonical page for a given library/version/path. "
                    "Large pages are automatically truncated to fit the token budget. "
                    "Pass a query to prioritize the most relevant sections, or use section "
                    "to read a specific heading. Response includes char_count, locale, and freshness metadata."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "library_id": {
                            "type": "string",
                            "description": "Library ID (same as 'technology' in read_doc). Alias: technology.",
                        },
                        "technology": {
                            "type": "string",
                            "description": "Alias for library_id (for consistency with read_doc).",
                        },
                        "version": {
                            "type": "string",
                            "description": "If provided, validates that stored version matches. Raises error on mismatch.",
                        },
                        "rel_path": {"type": "string"},
                        "max_tokens": {
                            "type": "integer",
                            "description": "Maximum tokens to return (default 20000). Sections are prioritized by query relevance when truncating.",
                            "default": 20000,
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional query to prioritize relevant sections when the document exceeds max_tokens.",
                        },
                        "section": {
                            "type": "string",
                            "description": "Read only the section matching this heading text.",
                        },
                    },
                    "required": ["rel_path"],
                },
            },
            {
                "name": "search_docs",
                "description": "Search local documentation text for a technology.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "technology": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["technology", "query"],
                },
            },
            {
                "name": "search_documentation",
                "description": (
                    "Search across one or more documentation libraries with version-aware filtering. "
                    "Supports cross-technology search — pass multiple libraries to find related patterns "
                    "across different technologies (e.g., Stripe webhooks + Supabase Edge Functions). "
                    "Results include char_count and last_scanned_at for size-aware reading. "
                    "Response includes result_count_by_library breakdown per requested library."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "libraries": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "version": {"type": "string"},
                                },
                                "required": ["id"],
                            },
                        },
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "list_supported_libraries",
                "description": (
                    "List libraries and versions currently available in the local index. "
                    "Each entry includes: monolith (bool), status ('ok'/'broken'), "
                    "last_scanned_at, last_fetched_at, and documents_count."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "diff_since",
                "description": (
                    "Show documentation changes (added/updated/deleted) since a given timestamp. "
                    "Use to detect stale docs or track what changed between sessions. "
                    "Supports pagination via offset and filtering by event_type."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "since": {
                            "type": "string",
                            "description": "ISO 8601 timestamp (e.g. '2026-04-08T00:00:00'). Returns changes after this time.",
                        },
                        "technology": {"type": "string", "description": "Filter by technology (optional)."},
                        "event_type": {
                            "type": "string",
                            "description": "Filter by event type: 'added', 'updated', or 'deleted'.",
                            "enum": ["added", "updated", "deleted"],
                        },
                        "limit": {"type": "integer", "description": "Max results per page (default 100)."},
                        "offset": {"type": "integer", "description": "Skip first N results for pagination (default 0)."},
                    },
                    "required": ["since"],
                },
            },
            {
                "name": "list_docs",
                "description": (
                    "List all documents indexed for a technology. Use to browse available "
                    "docs before reading, or to discover document paths for section-level "
                    "reads. Each result includes char_count for size-aware reading."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "technology": {"type": "string", "description": "Library/technology ID."},
                        "path_prefix": {
                            "type": "string",
                            "description": "Filter by path prefix (e.g. 'docs/guides/' to browse a subdirectory).",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 200)."},
                    },
                    "required": ["technology"],
                },
            },
            {
                "name": "install_project",
                "description": "Auto-detect technologies from a project path, bootstrap local docs cache, and index docs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string"},
                        "project_id": {"type": "string"},
                    },
                    "required": ["project_path"],
                },
            },
            {
                "name": "fetch_docs",
                "description": (
                    "Fetch the latest documentation from the web for a technology "
                    "(or all registered technologies) and re-index the local cache. "
                    "Uses conditional HTTP (ETag / If-Modified-Since) to skip unchanged sources."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "technology": {
                            "type": "string",
                            "description": "Optional. Library ID to fetch (e.g. 'react', 'nextjs'). Omit to fetch all.",
                        },
                    },
                },
            },
            {
                "name": "submit_feedback",
                "description": (
                    "Submit mandatory feedback on documentation quality. "
                    "After receiving documentation via read_doc, search_docs, or search_documentation, "
                    "the requester MUST call this tool to report whether the content was helpful. "
                    "Both 'satisfied' (yes/no) and 'reason' (why) are required."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "technology": {
                            "type": "string",
                            "description": "The technology/library the documentation belongs to.",
                        },
                        "rel_path": {
                            "type": "string",
                            "description": "Relative path of the document that was read.",
                        },
                        "query": {
                            "type": "string",
                            "description": "The original query or question the requester was trying to answer.",
                        },
                        "satisfied": {
                            "type": "boolean",
                            "description": "Was the documentation what you were looking for? true = yes, false = no.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Explain why the documentation did or did not meet your needs.",
                        },
                        "requester_id": {
                            "type": "string",
                            "description": "Identifier for the requester (e.g. agent name, session id).",
                        },
                    },
                    "required": ["technology", "rel_path", "query", "satisfied", "reason", "requester_id"],
                },
            },
            {
                "name": "list_feedback",
                "description": (
                    "List feedback entries on documentation quality, most recent first. "
                    "Supports filtering by technology and time range."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "technology": {
                            "type": "string",
                            "description": "Filter by technology. Omit for all.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max entries to return (default 100).",
                        },
                        "since": {
                            "type": "string",
                            "description": "ISO 8601 timestamp. Only include feedback created at or after this time.",
                        },
                        "until": {
                            "type": "string",
                            "description": "ISO 8601 timestamp. Only include feedback created at or before this time.",
                        },
                    },
                },
            },
            {
                "name": "feedback_stats",
                "description": (
                    "Get aggregate statistics on documentation quality feedback, with per-document breakdowns. "
                    "satisfaction_rate is rounded to 4 decimal places. low_quality_docs includes documents with "
                    "satisfaction < 50% and at least 2 feedback entries."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "technology": {
                            "type": "string",
                            "description": "Filter stats by technology. Omit for all.",
                        },
                        "since": {
                            "type": "string",
                            "description": "ISO 8601 timestamp. Only include feedback created at or after this time.",
                        },
                        "until": {
                            "type": "string",
                            "description": "ISO 8601 timestamp. Only include feedback created at or before this time.",
                        },
                    },
                },
            },
        ]  + [
            {
                "name": "resolve_observed_packages",
                "description": (
                    "Attempt to discover llms.txt documentation sources for packages that were "
                    "seen during project install but had no known documentation. "
                    "Probes candidate URLs and, if found, fetches and indexes docs automatically. "
                    "Safe to call repeatedly — packages attempted within the last 24 hours are skipped. "
                    "Returns {resolved: [...], failed: [...], skipped: int}."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of unresolved packages to probe in this call (default 50).",
                            "default": 50,
                        },
                    },
                },
            },
        ]

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        # Snapshot session identity at the start so a concurrent initialize
        # can't swap it mid-call and misattribute the log row.
        session_id = self._session_id
        session_project_id = self._session_project_id
        error_msg: str | None = None
        result_chars: int | None = None
        try:
            result = self._dispatch_tool(name, arguments)
            try:
                result_chars = len(json.dumps(result, default=str))
            except (TypeError, ValueError):
                result_chars = None
            return result
        except Exception as exc:
            # Keep the traceback tail so the persisted row is debuggable
            # without exposing internal paths.
            tb = traceback.format_exc().splitlines()
            tail = "\n".join(tb[-6:]) if len(tb) > 6 else "\n".join(tb)
            error_msg = f"{type(exc).__name__}: {exc}\n{tail}"
            raise
        finally:
            if session_id is None:
                # Tool called before initialize — generate a one-shot session.
                session_id = self.service.interaction_log.new_session_id()
                with self._session_lock:
                    if self._session_id is None:
                        self._session_id = session_id
                try:
                    self.service.record_mcp_session(
                        session_id=session_id,
                        project_id=None, workspace_path=None,
                        client_name=None, client_version=None,
                    )
                except Exception as exc:
                    print(f"[mcp_server] record_mcp_session failed: {exc}",
                          file=sys.stderr)
            try:
                self.service.record_mcp_interaction(
                    session_id=session_id,
                    project_id=session_project_id,
                    tool_name=name,
                    arguments=arguments,
                    result_chars=result_chars,
                    error=error_msg,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                )
            except Exception as exc:
                print(f"[mcp_server] record_mcp_interaction failed: {exc}",
                      file=sys.stderr)

    def _dispatch_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "scan_docs":
            payload = self.service.scan()
            # Side-effect: probe unresolved packages non-blocking
            try:
                self.service.resolve_observed_packages(limit=10)
            except Exception:
                pass
        elif name == "list_project_updates":
            payload = self.service.list_project_updates(
                project_id=arguments["project_id"],
                unread_only=bool(arguments.get("unread_only", True)),
                limit=int(arguments.get("limit", 20)),
            )
        elif name == "ack_project_updates":
            payload = {
                "project_id": arguments["project_id"],
                "last_seen_event_id": self.service.ack_project_updates(
                    project_id=arguments["project_id"],
                    through_event_id=arguments.get("through_event_id"),
                ),
            }
        elif name == "read_doc":
            payload = self.service.read_doc(
                technology=arguments["technology"],
                rel_path=arguments["rel_path"],
                max_tokens=int(arguments.get("max_tokens", 20000)),
                query=arguments.get("query"),
                section=arguments.get("section"),
            )
        elif name == "read_full_page":
            lib_id = arguments.get("library_id") or arguments.get("technology")
            if not lib_id:
                raise ValueError("Either library_id or technology is required")
            payload = self.service.read_full_page(
                library_id=lib_id,
                version=arguments.get("version"),
                rel_path=arguments["rel_path"],
                max_tokens=int(arguments.get("max_tokens", 20000)),
                query=arguments.get("query"),
                section=arguments.get("section"),
            )
        elif name == "search_docs":
            payload = self.service.search_docs(
                technology=arguments["technology"],
                query=arguments["query"],
                limit=int(arguments.get("limit", 5)),
            )
        elif name == "search_documentation":
            payload = self.service.search_documentation(
                query=arguments["query"],
                libraries=arguments.get("libraries"),
                limit=int(arguments.get("limit", 5)),
                workspace_id=str(arguments.get("workspace_id", "local")),
            )
        elif name == "list_supported_libraries":
            payload = self.service.list_supported_libraries()
        elif name == "diff_since":
            payload = self.service.diff_since(
                since=arguments["since"],
                technology=arguments.get("technology"),
                event_type=arguments.get("event_type"),
                limit=int(arguments.get("limit", 100)),
                offset=int(arguments.get("offset", 0)),
            )
        elif name == "list_docs":
            payload = self.service.list_docs(
                technology=arguments["technology"],
                path_prefix=arguments.get("path_prefix"),
                limit=int(arguments.get("limit", 200)),
            )
        elif name == "install_project":
            payload = self.service.install_project(
                project_root=arguments["project_path"],
                project_id=arguments.get("project_id"),
            )
        elif name == "fetch_docs":
            payload = self.service.fetch_docs(
                technology=arguments.get("technology"),
            )
        elif name == "submit_feedback":
            # LLMs sometimes send "false"/"true" as strings instead of JSON booleans.
            # bool("false") == True, so we handle the string case explicitly.
            _raw_sat = arguments["satisfied"]
            if isinstance(_raw_sat, str):
                _satisfied = _raw_sat.strip().lower() not in ("false", "0", "no", "")
            else:
                _satisfied = bool(_raw_sat)
            payload = self.service.submit_feedback(
                technology=arguments["technology"],
                rel_path=arguments["rel_path"],
                query=arguments["query"],
                satisfied=_satisfied,
                reason=arguments["reason"],
                requester_id=arguments["requester_id"],
            )
        elif name == "list_feedback":
            payload = self.service.list_feedback(
                technology=arguments.get("technology"),
                limit=int(arguments.get("limit", 100)),
                since=arguments.get("since"),
                until=arguments.get("until"),
            )
        elif name == "feedback_stats":
            payload = self.service.feedback_stats(
                technology=arguments.get("technology"),
                since=arguments.get("since"),
                until=arguments.get("until"),
            )
        elif name == "resolve_observed_packages":
            payload = self.service.resolve_observed_packages(
                limit=int(arguments.get("limit", 50)),
            )
        else:
            raise ValueError(f"Unknown tool: {name}")

        text = json.dumps(payload, indent=2)

        # Hard cap: prevent MCP tool result overflow.  If the serialized
        # response exceeds 100K chars (~25K tokens), truncate the content
        # field inside the payload and re-serialize.
        max_response_chars = 100_000
        if len(text) > max_response_chars and isinstance(payload, dict) and "content" in payload:
            budget = max_response_chars - 2000  # room for metadata
            payload["content"] = payload["content"][:budget] + (
                "\n\n---\n[Response truncated to fit MCP tool result limit. "
                "Use section parameter or reduce max_tokens to read specific parts.]"
            )
            payload["response_truncated"] = True
            text = json.dumps(payload, indent=2)

        return {
            "content": [
                {
                    "type": "text",
                    "text": text,
                }
            ]
        }

    def _bootstrap_from_initialize_params(self, params: dict[str, Any]) -> dict[str, Any] | None:
        workspace_path = extract_workspace_path(params)
        client_info = params.get("clientInfo") or {}
        if not isinstance(client_info, dict):
            client_info = {}
        client_name = client_info.get("name")
        client_version = client_info.get("version")
        # Clamp before persistence so a malicious client can't flood the DB.
        if isinstance(client_name, str) and len(client_name) > MAX_CLIENT_INFO_LEN:
            client_name = client_name[:MAX_CLIENT_INFO_LEN]
        if isinstance(client_version, str) and len(client_version) > MAX_CLIENT_INFO_LEN:
            client_version = client_version[:MAX_CLIENT_INFO_LEN]
        if not isinstance(client_name, str):
            client_name = None
        if not isinstance(client_version, str):
            client_version = None

        # Allocate a fresh session id under the store's lock.
        new_sid = self.service.interaction_log.new_session_id()

        # Caller explicitly passed a project path/id → synchronous install,
        # return the full bootstrap summary. This is the classic opt-in path
        # used by CLI-driven integrations that want confirmation before the
        # first tool call.
        explicit = params.get("project_path") or params.get("projectPath")
        explicit_id = params.get("project_id") or params.get("projectId")
        bootstrap_summary: dict[str, Any] | None = None
        session_project_id: str | None = None
        if isinstance(explicit, str) and explicit.strip():
            try:
                bootstrap_summary = self.service.install_project(
                    project_root=Path(explicit.strip()),
                    project_id=explicit_id if isinstance(explicit_id, str) else None,
                )
                if isinstance(bootstrap_summary, dict):
                    session_project_id = bootstrap_summary.get("project_id")
            except Exception as exc:
                print(f"[mcp_server] explicit install_project failed: {exc}",
                      file=sys.stderr)
                session_project_id = None
        else:
            # Workspace-URI path: resolve + auto-install in the background
            # so the MCP handshake never blocks. Pass the session_id so
            # the background thread can backfill attribution once the real
            # project_id is known.
            session_project_id = ensure_project_installed(
                self.service, workspace_path=workspace_path, wait=False,
                session_id=new_sid,
            )

        # Commit the session row before handing control to the client so
        # any concurrent tool call has a row to upsert against.
        with self._session_lock:
            self._session_id = new_sid
            self._session_project_id = session_project_id
        try:
            self.service.record_mcp_session(
                session_id=new_sid,
                project_id=session_project_id,
                workspace_path=str(workspace_path) if workspace_path else None,
                client_name=client_name,
                client_version=client_version,
            )
        except Exception as exc:
            print(f"[mcp_server] record_mcp_session failed: {exc}", file=sys.stderr)
        return bootstrap_summary

