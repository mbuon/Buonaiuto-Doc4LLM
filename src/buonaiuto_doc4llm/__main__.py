from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .mcp_server import MCPServer
from .service import DocsHubService

# Lazy import so the CLI works without the 'requests' optional dependency
# installed — the fetch command will surface a clear error at runtime if needed.
try:
    from ingestion.http_fetcher import HttpDocFetcher
    from ingestion.registry_loader import default_registry_path, load_registry
except ImportError:  # pragma: no cover
    HttpDocFetcher = None  # type: ignore[assignment,misc]
    default_registry_path = None  # type: ignore[assignment]
    load_registry = None  # type: ignore[assignment]


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2))


class RescanHandler(FileSystemEventHandler):
    def __init__(self, service: DocsHubService, debounce_seconds: float = 0.75):
        self.service = service
        self.debounce_seconds = debounce_seconds
        self.last_scan_at = 0.0
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        now = time.monotonic()
        with self._lock:
            if now - self.last_scan_at < self.debounce_seconds:
                return
            self.last_scan_at = now
        summary = self.service.scan()
        print_json({"event": "rescanned", "summary": summary})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Buonaiuto Doc4LLM")
    parser.add_argument(
        "--base-dir",
        default=str(Path.cwd()),
        help="Repository root containing docs_center/ and state/",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan")

    updates = subparsers.add_parser("updates")
    updates.add_argument("project_id")
    updates.add_argument("--all", action="store_true", help="Include already acknowledged updates")
    updates.add_argument("--limit", type=int, default=20)

    ack = subparsers.add_parser("ack")
    ack.add_argument("project_id")
    ack.add_argument("--through-event-id", type=int)

    read_doc = subparsers.add_parser("read-doc")
    read_doc.add_argument("technology")
    read_doc.add_argument("rel_path")

    search = subparsers.add_parser("search")
    search.add_argument("technology")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)

    install = subparsers.add_parser("install-project")
    install.add_argument("project_path")
    install.add_argument("--project-id")

    subparsers.add_parser("projects")
    subparsers.add_parser("watch")

    fetch = subparsers.add_parser("fetch", help="Fetch documentation from the web and re-scan")
    fetch.add_argument("--technology", help="Fetch only this technology (e.g. react, nextjs)")
    fetch.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Repeat fetch every N seconds (runs once if omitted)",
    )

    watch_fetch = subparsers.add_parser(
        "watch-and-fetch",
        help="Watch local docs for changes AND periodically fetch from the web",
    )
    watch_fetch.add_argument(
        "--interval",
        type=int,
        default=86400,
        help="Fetch interval in seconds (default: 86400 = 24h)",
    )

    serve = subparsers.add_parser("serve")
    serve.add_argument("--project-path")
    serve.add_argument("--project-id")
    serve.add_argument(
        "--dashboard",
        action="store_true",
        help="Also start the web dashboard in a background thread (http://127.0.0.1:8420)",
    )
    serve.add_argument(
        "--dashboard-host", default="127.0.0.1", help="Dashboard bind address (default: 127.0.0.1)",
    )
    serve.add_argument(
        "--dashboard-port", type=int, default=8420, help="Dashboard port (default: 8420)",
    )

    schedule = subparsers.add_parser(
        "schedule",
        help="Install/uninstall/check the daily documentation fetch cron job",
    )
    schedule.add_argument(
        "action",
        choices=["install", "uninstall", "status"],
        help="install: set up daily fetch, uninstall: remove it, status: check if active",
    )
    schedule.add_argument(
        "--hour", type=int, default=4, help="Hour to run (0-23, default: 4)",
    )
    schedule.add_argument(
        "--minute", type=int, default=0, help="Minute to run (0-59, default: 0)",
    )

    dashboard = subparsers.add_parser(
        "dashboard",
        help="Start the web dashboard (FastAPI + HTMX)",
    )
    dashboard.add_argument(
        "--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)",
    )
    dashboard.add_argument(
        "--port", type=int, default=8420, help="Port (default: 8420)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    service = DocsHubService(args.base_dir)

    if args.command == "scan":
        print_json(service.scan())
        return
    if args.command == "projects":
        service.sync_projects()
        print_json(service.list_projects())
        return
    if args.command == "updates":
        service.sync_projects()
        payload = service.list_project_updates(
            args.project_id,
            unread_only=not args.all,
            limit=args.limit,
        )
        print_json(payload)
        return
    if args.command == "ack":
        service.sync_projects()
        print_json(
            {
                "project_id": args.project_id,
                "last_seen_event_id": service.ack_project_updates(
                    args.project_id, through_event_id=args.through_event_id
                ),
            }
        )
        return
    if args.command == "read-doc":
        print_json(service.read_doc(args.technology, args.rel_path))
        return
    if args.command == "search":
        print_json(service.search_docs(args.technology, args.query, limit=args.limit))
        return
    if args.command == "install-project":
        print_json(
            service.install_project(
                project_root=args.project_path,
                project_id=args.project_id,
            )
        )
        return
    if args.command == "watch":
        service.scan()
        observer = Observer()
        handler = RescanHandler(service)
        observer.schedule(handler, str(service.docs_root), recursive=True)
        observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
        return
    if args.command == "fetch":
        _run_fetch(args, service)
        return
    if args.command == "watch-and-fetch":
        _run_watch_and_fetch(args, service)
        return
    if args.command == "serve":
        server = MCPServer(args.base_dir)
        if args.project_path:
            server.service.install_project(
                project_root=args.project_path,
                project_id=args.project_id,
            )
        else:
            server.service.scan()
        if args.dashboard:
            _start_dashboard_thread(args.base_dir, args.dashboard_host, args.dashboard_port)
        server.serve()
        return
    if args.command == "schedule":
        _run_schedule(args)
        return
    if args.command == "dashboard":
        _run_dashboard(args)
        return

    parser.error(f"Unknown command: {args.command}")


def _build_fetcher(base_dir: Path) -> "HttpDocFetcher":  # type: ignore[name-defined]
    if HttpDocFetcher is None or load_registry is None or default_registry_path is None:
        raise RuntimeError(
            "Web fetching requires the 'requests' library. "
            "Install it with: pip install 'buonaiuto-doc4llm[fetch]'"
        )
    registry_path = default_registry_path()
    mappings = load_registry(registry_path)
    db_path = base_dir / "state" / "buonaiuto_doc4llm.db"
    return HttpDocFetcher(base_dir=base_dir, db_path=db_path, registry=mappings)


def _run_fetch(args: argparse.Namespace, service: DocsHubService) -> None:
    """Execute the 'fetch' command (once or on an interval)."""
    base_dir = Path(args.base_dir)
    fetcher = _build_fetcher(base_dir)

    def _do_once() -> None:
        if args.technology:
            results = [fetcher.fetch(args.technology)]
        else:
            results = fetcher.fetch_all()
        scan_summary = service.scan()
        print_json({"fetch_results": results, "scan_summary": scan_summary})

    if args.interval is None:
        _do_once()
        return

    # Periodic loop
    _do_once()
    try:
        while True:
            time.sleep(args.interval)
            _do_once()
    except KeyboardInterrupt:
        pass


def _run_watch_and_fetch(args: argparse.Namespace, service: DocsHubService) -> None:
    """Run filesystem watcher + periodic HTTP fetch concurrently."""
    base_dir = Path(args.base_dir)
    fetcher = _build_fetcher(base_dir)

    # Initial fetch + scan
    fetch_results = fetcher.fetch_all()
    scan_summary = service.scan()
    print_json({"event": "initial_fetch", "fetch_results": fetch_results, "scan_summary": scan_summary})

    # Start filesystem watcher in background thread
    observer = Observer()
    handler = RescanHandler(service)
    observer.schedule(handler, str(service.docs_root), recursive=True)
    observer.start()

    next_fetch_at = time.monotonic() + args.interval
    try:
        while True:
            time.sleep(1)
            if time.monotonic() >= next_fetch_at:
                fetch_results = fetcher.fetch_all()
                scan_summary = service.scan()
                print_json({"event": "periodic_fetch", "fetch_results": fetch_results, "scan_summary": scan_summary})
                next_fetch_at = time.monotonic() + args.interval
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def _start_dashboard_thread(base_dir: str, host: str, port: int) -> None:
    """Start the web dashboard in a background daemon thread."""
    import uvicorn
    from buonaiuto_doc4llm.dashboard import create_app

    app = create_app(base_dir)

    def _run() -> None:
        uvicorn.run(app, host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True, name="dashboard")
    t.start()
    import sys
    print(f"Buonaiuto Doc4LLM dashboard: http://{host}:{port}", file=sys.stderr)


def _run_dashboard(args: argparse.Namespace) -> None:
    """Start the web dashboard."""
    import uvicorn
    from buonaiuto_doc4llm.dashboard import create_app

    app = create_app(args.base_dir)
    print(f"Buonaiuto Doc4LLM dashboard: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _run_schedule(args: argparse.Namespace) -> None:
    """Install, uninstall, or check the daily fetch schedule."""
    from buonaiuto_doc4llm.scheduler import install_schedule, schedule_status, uninstall_schedule

    base_dir = Path(args.base_dir)
    if args.action == "install":
        print_json(install_schedule(base_dir, hour=args.hour, minute=args.minute))
    elif args.action == "uninstall":
        print_json(uninstall_schedule())
    elif args.action == "status":
        print_json(schedule_status())


if __name__ == "__main__":
    main()
