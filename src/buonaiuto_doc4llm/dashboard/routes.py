"""Dashboard route handlers."""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse


def register_routes(app: FastAPI) -> None:
    """Register all page and API routes on the app."""

    def _render(request: Request, template: str, ctx: dict[str, Any]) -> HTMLResponse:
        """Render a Jinja2 template (compatible with Starlette 1.x)."""
        return request.app.state.templates.TemplateResponse(
            request, template, ctx,
        )

    def _ctx(request: Request, active_page: str, **extra: Any) -> dict[str, Any]:
        """Build the shared template context (sidebar data)."""
        service = request.app.state.service
        with service._connect() as db:
            doc_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            tech_count = len(
                db.execute("SELECT DISTINCT technology FROM documents").fetchall()
            )
            last_scan_row = db.execute(
                "SELECT MAX(last_scanned_at) FROM documents"
            ).fetchone()
            last_scan = last_scan_row[0][:19] if last_scan_row and last_scan_row[0] else None

        return {
            "active_page": active_page,
            "doc_count": doc_count,
            "tech_count": tech_count,
            "last_scan": last_scan,
            **extra,
        }

    def _all_technologies(service: Any) -> list[str]:
        with service._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT technology FROM documents ORDER BY technology"
            ).fetchall()
            return [r[0] for r in rows]

    def _get_fetch_states(service: Any) -> list[dict[str, Any]]:
        with service._connect() as db:
            rows = db.execute(
                "SELECT * FROM fetch_state ORDER BY technology"
            ).fetchall()
            return [dict(r) for r in rows]

    def _get_libraries_with_fetch(service: Any) -> list[dict[str, Any]]:
        libraries = service.list_supported_libraries()
        fetch_states = {fs["technology"]: fs for fs in _get_fetch_states(service)}
        for lib in libraries:
            lib["fetch_state"] = fetch_states.get(lib["library_id"])
        return libraries

    def _get_all_events(
        service: Any,
        technology: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with service._connect() as db:
            query = "SELECT * FROM update_events WHERE 1=1"
            params: list[Any] = []
            if technology:
                query += " AND technology = ?"
                params.append(technology)
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = db.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def _get_all_documents(
        service: Any,
        technology: str | None = None,
        query: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with service._connect() as db:
            sql = "SELECT * FROM documents WHERE 1=1"
            params: list[Any] = []
            if technology:
                sql += " AND technology = ?"
                params.append(technology)
            if query:
                # Escape LIKE wildcards and limit query length
                safe_q = query[:200].replace("%", r"\%").replace("_", r"\_")
                sql += r" AND (title LIKE ? ESCAPE '\' OR rel_path LIKE ? ESCAPE '\')"
                params.extend([f"%{safe_q}%", f"%{safe_q}%"])
            sql += " ORDER BY technology, rel_path LIMIT ?"
            params.append(limit)
            rows = db.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def _get_projects_with_unread(service: Any) -> list[dict[str, Any]]:
        service.sync_projects()
        projects = service.list_projects()
        for p in projects:
            updates_payload = service.list_project_updates(p["project_id"], unread_only=True)
            p["unread_count"] = updates_payload["unseen_count"]
        return projects

    def _load_registry() -> list[dict[str, Any]]:
        from ingestion.registry_loader import default_registry_path, load_registry
        try:
            mappings = load_registry(default_registry_path())
            return [
                {
                    "library_id": m.library_id,
                    "package_names": m.package_names,
                    "sources": m.sources,
                }
                for m in mappings
            ]
        except Exception:
            return []

    # ── Page routes ──

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        service = request.app.state.service
        libraries = service.list_supported_libraries()
        events = _get_all_events(service, limit=10)

        with service._connect() as db:
            project_count = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            event_count = db.execute("SELECT COUNT(*) FROM update_events").fetchone()[0]

        try:
            from buonaiuto_doc4llm.scheduler import schedule_status
            scheduler = schedule_status()
        except Exception:
            scheduler = {"installed": False}

        ctx = _ctx(
            request,
            "overview",
            libraries=libraries,
            recent_events=events,
            project_count=project_count,
            event_count=event_count,
            scheduler=scheduler,
        )
        return _render(request, "overview.html", ctx)

    @app.get("/technologies", response_class=HTMLResponse)
    async def technologies(
        request: Request,
        flash_msg: str | None = None,
        flash_type: str | None = None,
    ) -> HTMLResponse:
        service = request.app.state.service
        libraries = _get_libraries_with_fetch(service)
        registry = _load_registry()
        ctx = _ctx(
            request,
            "technologies",
            libraries=libraries,
            registry=registry,
            registry_count=len(registry),
            flash_msg=flash_msg,
            flash_type=flash_type,
        )
        return _render(request, "technologies.html", ctx)

    @app.get("/query", response_class=HTMLResponse)
    async def query_page(request: Request) -> HTMLResponse:
        service = request.app.state.service
        vector_info = getattr(request.app.state, "vector_info", {})
        has_indexer = service.indexer is not None
        # Check if collection has any points
        indexed_count = 0
        if has_indexer:
            try:
                info = service.indexer.qdrant_client.client.get_collection(
                    service.indexer.qdrant_client.collection_name,
                )
                indexed_count = info.points_count or 0
            except Exception:
                pass
        ctx = _ctx(
            request,
            "query",
            all_technologies=_all_technologies(service),
            vector_provider=vector_info.get("provider_name"),
            has_indexer=has_indexer,
            indexed_count=indexed_count,
        )
        return _render(request, "query.html", ctx)

    @app.get("/documents", response_class=HTMLResponse)
    async def documents(
        request: Request,
        technology: str | None = None,
        q: str | None = None,
    ) -> HTMLResponse:
        service = request.app.state.service
        docs = _get_all_documents(service, technology=technology, query=q)
        ctx = _ctx(
            request,
            "documents",
            documents=docs,
            all_technologies=_all_technologies(service),
            current_tech=technology,
            query=q,
        )
        return _render(request, "documents.html", ctx)

    @app.get("/documents/{technology}/{rel_path:path}", response_class=HTMLResponse)
    async def document_page(
        request: Request,
        technology: str,
        rel_path: str,
    ) -> HTMLResponse:
        service = request.app.state.service
        # Strip URL fragment (#anchor) — fragments are client-side only and not part of the doc path
        rel_path = rel_path.split("#")[0]
        try:
            result = service.read_doc(technology, rel_path)
        except Exception as exc:
            return _flash_html(request, f"Could not read document: {exc}", "error")

        content = result.get("content", "")
        rendered = _render_markdown(content, technology=technology)

        with service._connect() as db:
            row = db.execute(
                "SELECT version, last_scanned_at FROM documents WHERE technology=? AND rel_path=?",
                (technology, rel_path),
            ).fetchone()
        meta = dict(row) if row else {}

        ctx = _ctx(
            request,
            "documents",
            technology=technology,
            rel_path=rel_path,
            content=content,
            rendered=rendered,
            version=meta.get("version"),
            last_scanned_at=meta.get("last_scanned_at"),
            char_count=len(content),
        )
        return _render(request, "doc_page.html", ctx)

    @app.get("/projects", response_class=HTMLResponse)
    async def projects(
        request: Request,
        flash_msg: str | None = None,
        flash_type: str | None = None,
    ) -> HTMLResponse:
        service = request.app.state.service
        project_list = _get_projects_with_unread(service)
        ctx = _ctx(
            request,
            "projects",
            projects=project_list,
            flash_msg=flash_msg,
            flash_type=flash_type,
        )
        return _render(request, "projects.html", ctx)

    @app.get("/activity", response_class=HTMLResponse)
    async def activity(
        request: Request,
        technology: str | None = None,
        event_type: str | None = None,
    ) -> HTMLResponse:
        service = request.app.state.service
        events = _get_all_events(service, technology=technology, event_type=event_type)
        ctx = _ctx(
            request,
            "activity",
            events=events,
            all_technologies=_all_technologies(service),
            current_tech=technology,
            current_type=event_type,
        )
        return _render(request, "activity.html", ctx)

    @app.get("/schedule", response_class=HTMLResponse)
    async def schedule(
        request: Request,
        flash_msg: str | None = None,
        flash_type: str | None = None,
    ) -> HTMLResponse:
        service = request.app.state.service

        try:
            from buonaiuto_doc4llm.scheduler import schedule_status
            scheduler = schedule_status()
        except Exception:
            scheduler = {"installed": False}

        ctx = _ctx(
            request,
            "schedule",
            scheduler=scheduler,
            fetch_states=_get_fetch_states(service),
            all_technologies=_all_technologies(service),
            flash_msg=flash_msg,
            flash_type=flash_type,
        )
        return _render(request, "schedule.html", ctx)

    # ── API routes (HTMX actions) ──

    @app.post("/api/scan", response_class=HTMLResponse)
    async def api_scan(request: Request) -> HTMLResponse:
        service = request.app.state.service
        try:
            scan_result = await asyncio.to_thread(service.scan)
            summaries = scan_result.get("technologies", [])
            total_docs = scan_result.get("total_documents", 0)
            total_events = scan_result.get("total_events", 0)
            msg = f"Scan complete: {total_docs} docs scanned, {total_events} new events across {len(summaries)} technologies"
            return _flash_html(request, msg, "success")
        except Exception as exc:
            return _flash_html(request, f"Scan failed: {exc}", "error")

    @app.post("/api/fetch-all", response_class=HTMLResponse)
    async def api_fetch_all(request: Request) -> HTMLResponse:
        service = request.app.state.service
        try:
            result = await asyncio.to_thread(service.fetch_docs)
            fetched = sum(1 for r in result["fetch_results"] if r.get("fetched"))
            total = len(result["fetch_results"])
            msg = f"Fetched {fetched}/{total} technologies"
            return _flash_html(request, msg, "success")
        except Exception as exc:
            return _flash_html(request, f"Fetch failed: {exc}", "error")

    @app.post("/api/fetch", response_class=HTMLResponse)
    async def api_fetch(
        request: Request,
        technology: str = Query(""),
    ) -> HTMLResponse:
        service = request.app.state.service
        if not technology:
            return _flash_html(request, "No technology selected", "error")
        try:
            result = await asyncio.to_thread(partial(service.fetch_docs, technology=technology))
            fr = result["fetch_results"][0]
            if fr.get("fetched"):
                pages = fr.get("pages_fetched", 0)
                msg = f"Fetched {technology}: {fr.get('bytes', 0)} bytes"
                if pages:
                    msg += f", {pages} linked pages"
            else:
                msg = f"{technology}: {fr.get('reason', 'not modified')}"
            return _flash_html(request, msg, "success")
        except Exception as exc:
            return _flash_html(request, f"Fetch {technology} failed: {exc}", "error")

    # ── SSE streaming endpoints for status ticker ──

    @app.get("/api/scan-stream")
    async def api_scan_stream(request: Request) -> StreamingResponse:
        """SSE endpoint that streams per-technology scan progress."""
        import json as _json

        service = request.app.state.service

        async def _generate():
            try:
                service.sync_projects()
                if not service.technologies_root.exists():
                    yield _sse_event("done", {"message": "No technologies directory found"})
                    return

                tech_dirs = sorted(
                    d for d in service.technologies_root.iterdir() if d.is_dir()
                )
                total = len(tech_dirs)
                total_docs = 0
                total_events = 0

                for i, tech_dir in enumerate(tech_dirs, 1):
                    technology = tech_dir.name
                    yield _sse_event("progress", {
                        "technology": technology,
                        "index": i,
                        "total": total,
                        "status": "scanning",
                    })

                    # Delegate to service.scan_technology() — avoids duplicating
                    # the read-compare-write logic here and prevents duplicate
                    # update events when concurrent scans run simultaneously.
                    summary = await asyncio.to_thread(
                        service.scan_technology, technology,
                    )
                    docs = summary.get("documents_indexed", 0)
                    events = summary.get("events_created", 0)
                    total_docs += docs
                    total_events += events

                    yield _sse_event("progress", {
                        "technology": technology,
                        "index": i,
                        "total": total,
                        "status": "done",
                        "documents_indexed": docs,
                        "events_created": events,
                    })

                yield _sse_event("done", {
                    "message": f"Scan complete: {total_docs} docs, {total_events} new events across {total} technologies",
                })
            except Exception as exc:
                yield _sse_event("error", {"message": f"Scan failed: {exc}"})

        return StreamingResponse(_generate(), media_type="text/event-stream")

    @app.get("/api/fetch-all-stream")
    async def api_fetch_all_stream(request: Request) -> StreamingResponse:
        """SSE endpoint that streams per-technology fetch progress."""
        import json as _json

        service = request.app.state.service

        async def _generate():
            try:
                from ingestion.http_fetcher import HttpDocFetcher
                from ingestion.registry_loader import default_registry_path, load_registry

                mappings = load_registry(default_registry_path())
                fetcher = HttpDocFetcher(
                    base_dir=service.base_dir,
                    db_path=service.db_path,
                    registry=mappings,
                )
                total = len(mappings)
                fetched_count = 0
                error_count = 0

                for i, mapping in enumerate(mappings, 1):
                    tech = mapping.library_id
                    yield _sse_event("progress", {
                        "technology": tech,
                        "index": i,
                        "total": total,
                        "status": "fetching",
                    })

                    try:
                        result = await asyncio.to_thread(fetcher.fetch, tech)
                        did_fetch = result.get("fetched", False)
                        byt = result.get("bytes", result.get("bytes_received", 0))
                        reason = result.get("reason", "")
                        if did_fetch:
                            fetched_count += 1
                        yield _sse_event("progress", {
                            "technology": tech,
                            "index": i,
                            "total": total,
                            "status": "done" if did_fetch else "skipped",
                            "bytes": byt,
                            "reason": reason,
                        })
                    except Exception as exc:
                        error_count += 1
                        yield _sse_event("progress", {
                            "technology": tech,
                            "index": i,
                            "total": total,
                            "status": "error",
                            "message": str(exc)[:200],
                        })

                # Run a scan after fetching to index new content
                yield _sse_event("progress", {
                    "technology": "(re-scan)",
                    "index": total,
                    "total": total,
                    "status": "scanning",
                })
                await asyncio.to_thread(service.scan)

                msg = f"Fetched {fetched_count}/{total} technologies"
                if error_count:
                    msg += f" ({error_count} errors)"
                yield _sse_event("done", {"message": msg})
            except Exception as exc:
                yield _sse_event("error", {"message": f"Fetch failed: {exc}"})

        return StreamingResponse(_generate(), media_type="text/event-stream")

    def _sse_event(event_type: str, data: dict[str, Any]) -> str:
        """Format a single SSE event."""
        import json as _json
        return f"event: {event_type}\ndata: {_json.dumps(data)}\n\n"

    @app.post("/api/index", response_class=HTMLResponse)
    async def api_index(
        request: Request,
        technology: str = Form(""),
    ) -> HTMLResponse:
        service = request.app.state.service
        if service.indexer is None:
            return _flash_html(request, "Vector indexing not available — no embedding provider configured", "error")
        try:
            if technology:
                result = await asyncio.to_thread(partial(service.indexer.index_technology, technology))
                msg = f"Indexed {technology}: {result['chunks_indexed']} chunks, {result['points_upserted']} vectors"
            else:
                techs = _all_technologies(service)

                async def _index_all() -> tuple[int, int]:
                    tc, tp = 0, 0
                    for t in techs:
                        r = await asyncio.to_thread(partial(service.indexer.index_technology, t))
                        tc += r["chunks_indexed"]
                        tp += r["points_upserted"]
                    return tc, tp

                total_chunks, total_points = await _index_all()
                msg = f"Indexed {len(techs)} libraries: {total_chunks} chunks, {total_points} vectors"
            return _flash_html(request, msg, "success")
        except Exception as exc:
            return _flash_html(request, f"Indexing failed: {exc}", "error")

    @app.get("/api/query", response_class=HTMLResponse)
    async def api_query(
        request: Request,
        q: str = Query(""),
        technology: str = Query(""),
        limit: int = Query(10),
    ) -> HTMLResponse:
        service = request.app.state.service
        if not q.strip():
            return _render(request, "partials/query_results.html", {
                "error": "Please enter a search query",
            })
        try:
            libraries = [{"id": technology}] if technology else None
            payload = service.search_documentation(
                query=q.strip(),
                libraries=libraries,
                limit=min(limit, 50),
            )
            return _render(request, "partials/query_results.html", {
                "results": payload["results"],
                "retrieval_mode": payload["retrieval_mode"],
                "library_id": payload.get("library_id"),
                "query": q.strip(),
            })
        except Exception as exc:
            return _render(request, "partials/query_results.html", {
                "error": f"Search failed: {exc}",
            })

    @app.get("/api/read-doc", response_class=HTMLResponse)
    async def api_read_doc(
        request: Request,
        technology: str = Query(""),
        rel_path: str = Query(""),
        q: str = Query(""),
    ) -> HTMLResponse:
        service = request.app.state.service
        try:
            result = service.read_doc(technology, rel_path)
            content = result.get("content", "")
            return _render(request, "partials/doc_viewer.html", {
                "technology": technology,
                "rel_path": rel_path,
                "content": content,
                "highlight_query": q,
            })
        except Exception as exc:
            return _flash_html(request, f"Read failed: {exc}", "error")

    @app.post("/api/install-project", response_class=HTMLResponse)
    async def api_install_project(
        request: Request,
        project_path: str = Form(""),
        project_id: str = Form(""),
    ) -> HTMLResponse:
        service = request.app.state.service
        if not project_path:
            return _flash_html(request, "Project path is required", "error")
        try:
            result = await asyncio.to_thread(
                partial(service.install_project, project_root=project_path, project_id=project_id or None),
            )
            techs = result.get("technologies_detected", [])
            fetched = len(result.get("fetch_results", []))
            errors = len(result.get("fetch_errors", []))
            msg = f"Installed {result['project_id']}: {len(techs)} technologies detected, {fetched} fetched"
            if errors:
                msg += f", {errors} fetch errors"
            return _flash_html(request, msg, "success")
        except Exception as exc:
            return _flash_html(request, f"Install failed: {exc}", "error")

    @app.post("/api/ack", response_class=HTMLResponse)
    async def api_ack(
        request: Request,
        project_id: str = Query(""),
    ) -> HTMLResponse:
        service = request.app.state.service
        try:
            event_id = service.ack_project_updates(project_id)
            msg = f"Acknowledged updates for {project_id} through event #{event_id}"
            return _flash_html(request, msg, "success")
        except Exception as exc:
            return _flash_html(request, f"Ack failed: {exc}", "error")

    @app.post("/api/schedule-install", response_class=HTMLResponse)
    async def api_schedule_install(
        request: Request,
        hour: int = Form(4),
        minute: int = Form(0),
    ) -> HTMLResponse:
        try:
            from buonaiuto_doc4llm.scheduler import install_schedule
            base_dir = request.app.state.base_dir
            result = install_schedule(base_dir, hour=hour, minute=minute)
            msg = f"Schedule installed: {result.get('schedule', 'daily')}"
            return _flash_html(request, msg, "success")
        except Exception as exc:
            return _flash_html(request, f"Schedule install failed: {exc}", "error")

    @app.post("/api/schedule-uninstall", response_class=HTMLResponse)
    async def api_schedule_uninstall(request: Request) -> HTMLResponse:
        try:
            from buonaiuto_doc4llm.scheduler import uninstall_schedule
            result = uninstall_schedule()
            if result.get("uninstalled"):
                return _flash_html(request, "Schedule removed", "success")
            return _flash_html(request, "Schedule was not installed", "info")
        except Exception as exc:
            return _flash_html(request, f"Uninstall failed: {exc}", "error")

    def _render_markdown(text: str, technology: str | None = None) -> str:
        """Convert Markdown text to safe HTML.

        Uses the ``markdown`` package when available; falls back to a plain
        <pre> block so the page is always readable.

        When ``technology`` is given, rewrites relative doc links (e.g.
        ``/docs/transformers/v5.5.0/en/model_doc/vit``) to dashboard URLs
        (``/documents/{technology}/docs/transformers/...``) so in-doc navigation
        stays within the dashboard and fragments are preserved.
        """
        import re

        try:
            import markdown
            html_out = markdown.markdown(
                text,
                extensions=["fenced_code", "tables", "nl2br", "toc"],
                output_format="html",
            )
        except ImportError:
            import html as _html
            return f"<pre style='white-space:pre-wrap;word-break:break-word;'>{_html.escape(text)}</pre>"

        # Sanitize HTML to prevent stored XSS from documentation content.
        # Prefer nh3 (Rust-based, fast), fall back to bleach, then to a
        # conservative tag-stripping regex as a last resort.
        try:
            import nh3
            _ALLOWED_ATTRS: dict[str, set[str]] = {
                "a": {"href", "title", "target", "rel"},
                "img": {"src", "alt", "title", "width", "height"},
                "code": {"class"},
                "pre": {"class"},
                "div": {"class", "id"},
                "span": {"class"},
                "th": {"align"}, "td": {"align"},
            }
            html_out = nh3.clean(html_out, attributes=_ALLOWED_ATTRS)
        except ImportError:
            try:
                import bleach
                from bleach.sanitizer import ALLOWED_TAGS as _BL_TAGS
                _EXTRA_TAGS = {
                    "p", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6",
                    "table", "thead", "tbody", "tr", "th", "td",
                    "img", "br", "hr", "div", "span",
                }
                _SAFE_ATTRS = {
                    "a": ["href", "title"],
                    "img": ["src", "alt", "title", "width", "height"],
                    "code": ["class"], "pre": ["class"],
                    "div": ["class", "id"], "span": ["class"],
                    "th": ["align"], "td": ["align"],
                }
                html_out = bleach.clean(
                    html_out,
                    tags=set(_BL_TAGS) | _EXTRA_TAGS,
                    attributes=_SAFE_ATTRS,
                    strip=True,
                )
            except ImportError:
                # Last resort: strip <script>, <style>, and on* event handlers
                import re as _re
                html_out = _re.sub(r'<script[^>]*>.*?</script>', '', html_out, flags=_re.DOTALL | _re.IGNORECASE)
                html_out = _re.sub(r'<style[^>]*>.*?</style>', '', html_out, flags=_re.DOTALL | _re.IGNORECASE)
                html_out = _re.sub(r'\s+on\w+="[^"]*"', '', html_out, flags=_re.IGNORECASE)
                html_out = _re.sub(r'\s+on\w+=\'[^\']*\'', '', html_out, flags=_re.IGNORECASE)

        if technology:
            # Rewrite href values that look like absolute doc paths (start with /)
            # but are not already dashboard URLs and not external http(s) links.
            def _rewrite(m: re.Match) -> str:
                href = m.group(1)
                # Leave external links, mailto, anchors-only, and already-rewritten links alone
                if href.startswith(("http://", "https://", "mailto:", "#", "/documents/")):
                    return m.group(0)
                # Strip leading slash for the rel_path portion
                path_part = href.lstrip("/")
                return f'href="/documents/{technology}/{path_part}"'

            html_out = re.sub(r'href="([^"]*)"', _rewrite, html_out)

        return html_out

    def _flash_html(request: Request, msg: str, flash_type: str) -> HTMLResponse:
        return _render(request, "partials/flash.html", {
            "flash_msg": msg,
            "flash_type": flash_type,
        })
