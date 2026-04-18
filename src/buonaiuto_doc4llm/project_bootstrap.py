from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from buonaiuto_doc4llm.service import DocsHubService


FRESHNESS_SECONDS = 24 * 60 * 60  # 24h

# Module-level in-flight set of workspace paths currently being installed
_install_in_flight: set[str] = set()
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
        return None
    return Path(unquote(parsed.path))


def resolve_project_id_for_basename(projects_root: Path, basename: str) -> str | None:
    """Return the project_id whose project-file basename matches (case-insensitive)."""
    if not projects_root.exists():
        return None
    target = basename.lower()
    for path in projects_root.glob("*.json"):
        if path.stem.lower() == target:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            pid = data.get("project_id")
            return pid if isinstance(pid, str) and pid else path.stem
    return None


def _is_fresh(path: Path, max_age_seconds: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < max_age_seconds
    except OSError:
        return False


def ensure_project_installed(
    service: "DocsHubService",
    *,
    workspace_path: Path | None,
    wait: bool = False,
    freshness_seconds: int = FRESHNESS_SECONDS,
) -> str | None:
    """Resolve a workspace path to a project_id, auto-installing when needed.

    Returns the predicted project_id immediately (for session pinning).
    Install is dispatched to a background thread unless wait=True.
    Returns None if workspace_path is missing OR (when wait=True) if install raised.
    """
    if workspace_path is None:
        return None

    basename = workspace_path.name
    if not basename:
        return None

    project_file = service.projects_root / f"{basename}.json"

    # Fast path: file exists and is fresh → reuse as-is
    if project_file.exists() and _is_fresh(project_file, freshness_seconds):
        existing = resolve_project_id_for_basename(service.projects_root, basename)
        return existing or basename

    key = str(workspace_path)
    with _install_in_flight_lock:
        if key in _install_in_flight:
            return basename  # another thread is already on it
        _install_in_flight.add(key)

    error_holder: dict[str, Exception] = {}

    def _run() -> None:
        try:
            service.install_project(
                project_root=workspace_path,
                project_id=basename,
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

    if wait:
        _run()
        return None if "exc" in error_holder else basename

    threading.Thread(target=_run, daemon=True, name=f"install-{basename}").start()
    return basename
