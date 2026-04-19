from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from buonaiuto_doc4llm.service import DocsHubService


FRESHNESS_SECONDS = 24 * 60 * 60  # 24h

# Character classes not allowed in a derived project_id. Everything outside
# this set is collapsed to a hyphen, matching `auto_setup._resolve_project_id`.
_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9._-]+")
# Pattern that would look like path traversal if it ever slipped into a
# filename built from the workspace basename.
_TRAVERSAL_RE = re.compile(r"(^|/)\.\.(/|$)|[\x00-\x1f]")

# Module-level dedup set of install jobs currently running. Keys are the
# *resolved* absolute path string so trailing slashes / symlink duplicates
# collapse to one entry. The paired lock guards both the set and the
# per-path Events used by wait=True callers.
_install_in_flight: set[str] = set()
_install_in_flight_events: dict[str, threading.Event] = {}
_install_in_flight_lock = threading.Lock()


def extract_workspace_path(params: dict[str, Any]) -> Path | None:
    direct = params.get("project_path") or params.get("projectPath")
    if isinstance(direct, str) and direct.strip():
        return Path(direct.strip())

    folders = params.get("workspaceFolders")
    if isinstance(folders, list):
        for folder in folders:
            if isinstance(folder, dict):
                uri = folder.get("uri")
                if isinstance(uri, str):
                    p = _path_from_uri(uri)
                    if p is not None:
                        return p

    root_uri = params.get("rootUri")
    if isinstance(root_uri, str):
        return _path_from_uri(root_uri)

    return None


def _path_from_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        # Non-file URIs (vscode-remote://, dataspell://, ...) can't be
        # auto-resolved. Log so the user sees why attribution failed.
        if parsed.scheme:
            print(f"[project_bootstrap] ignoring non-file URI scheme {parsed.scheme!r}",
                  file=sys.stderr)
        return None
    return Path(unquote(parsed.path))


def _normalise_basename(basename: str) -> str | None:
    """Turn a raw workspace basename into a safe project_id or return None.

    Rejects empty strings, path-traversal patterns, and control characters.
    Replaces unsafe characters with hyphens to match the canonical
    normalization in auto_setup._resolve_project_id.
    """
    if not basename:
        return None
    if _TRAVERSAL_RE.search(basename):
        return None
    cleaned = _ID_SANITIZE_RE.sub("-", basename).strip("-._") or None
    return cleaned


def resolve_project_id_for_basename(projects_root: Path, basename: str) -> str | None:
    """Return the project_id whose project-file basename matches (case-insensitive)."""
    if not projects_root.exists():
        return None
    target = basename.lower()
    matches: list[Path] = []
    for path in sorted(projects_root.glob("*.json")):
        if path.stem.lower() == target:
            matches.append(path)
    if not matches:
        return None
    if len(matches) > 1:
        # On case-sensitive filesystems two files can share a stem up to
        # case. Refuse to guess — operator must rename one.
        names = ", ".join(p.name for p in matches)
        print(f"[project_bootstrap] ambiguous basename {basename!r} matches: {names}",
              file=sys.stderr)
        return None
    path = matches[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    pid = data.get("project_id")
    return pid if isinstance(pid, str) and pid else path.stem


def _is_fresh(path: Path, max_age_seconds: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < max_age_seconds
    except OSError as exc:
        print(f"[project_bootstrap] stat({path}) failed: {exc}", file=sys.stderr)
        return False


def _resolve_workspace_key(workspace_path: Path) -> str:
    """Stable dedup key for a workspace path: absolute and normalised."""
    try:
        return str(workspace_path.resolve(strict=False))
    except (OSError, RuntimeError):
        return str(workspace_path)


def ensure_project_installed(
    service: "DocsHubService",
    *,
    workspace_path: Path | None,
    wait: bool = False,
    freshness_seconds: int = FRESHNESS_SECONDS,
    session_id: str | None = None,
) -> str | None:
    """Resolve a workspace path to a project_id, auto-installing when needed.

    Returns the predicted project_id immediately (for session pinning).
    Install is dispatched to a background thread unless wait=True.
    Returns None if workspace_path is missing OR (when wait=True) if install raised.

    If `session_id` is provided AND the install runs asynchronously AND it
    succeeds, the session's project_id is backfilled so later queries find
    the interaction rows under the correct project.
    """
    if workspace_path is None:
        return None

    raw_basename = workspace_path.name
    basename = _normalise_basename(raw_basename)
    if basename is None:
        print(f"[project_bootstrap] workspace basename rejected: {raw_basename!r}",
              file=sys.stderr)
        return None

    project_file = service.projects_root / f"{basename}.json"

    # Fast path: file exists and is fresh → reuse as-is
    if project_file.exists() and _is_fresh(project_file, freshness_seconds):
        existing = resolve_project_id_for_basename(service.projects_root, basename)
        return existing or basename

    key = _resolve_workspace_key(workspace_path)

    # Coordinate with any concurrent install for the same workspace.
    with _install_in_flight_lock:
        already_running = key in _install_in_flight
        if already_running:
            event = _install_in_flight_events.get(key)
        else:
            event = threading.Event()
            _install_in_flight.add(key)
            _install_in_flight_events[key] = event

    if already_running:
        if wait and event is not None:
            # Block until the in-flight install finishes instead of silently
            # returning without waiting.
            event.wait(timeout=300)
        return basename

    error_holder: dict[str, Exception] = {}

    def _run() -> None:
        try:
            result = service.install_project(
                project_root=workspace_path,
                project_id=basename,
            )
            # Backfill attribution now that the real project_id is known.
            if session_id:
                real_pid = (
                    (isinstance(result, dict) and result.get("project_id"))
                    or basename
                )
                try:
                    service.interaction_log.backfill_session_project(
                        session_id, real_pid,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[project_bootstrap] backfill for session={session_id} "
                        f"failed: {exc}",
                        file=sys.stderr,
                    )
        except Exception as exc:  # noqa: BLE001 — fail-safe
            error_holder["exc"] = exc
            print(
                f"[project_bootstrap] install_project failed for {workspace_path}: {exc}",
                file=sys.stderr,
            )
        finally:
            with _install_in_flight_lock:
                _install_in_flight.discard(key)
                ev = _install_in_flight_events.pop(key, None)
            if ev is not None:
                ev.set()

    if wait:
        _run()
        return None if "exc" in error_holder else basename

    threading.Thread(target=_run, daemon=True, name=f"install-{basename}").start()
    return basename
