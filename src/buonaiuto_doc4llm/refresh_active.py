from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from buonaiuto_doc4llm.project_bootstrap import FRESHNESS_SECONDS

if TYPE_CHECKING:
    from buonaiuto_doc4llm.service import DocsHubService


def list_active_projects(service: "DocsHubService", *, days: int = 30) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with service._connect() as conn:
        active_ids = [
            r["project_id"] for r in conn.execute(
                """
                SELECT DISTINCT project_id FROM mcp_interactions
                 WHERE project_id IS NOT NULL AND created_at >= ?
                """,
                (since,),
            ).fetchall()
        ]
    if not active_ids:
        return []

    out: list[dict[str, Any]] = []
    for pid in active_ids:
        path = service.projects_root / f"{pid}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "project_id": pid,
            "name": data.get("name", pid),
            "technologies": list(data.get("technologies") or []),
            "workspace_path": data.get("workspace_path"),
            "project_file_mtime": path.stat().st_mtime,
        })
    return out


def _fetch_technology(service: "DocsHubService", technology: str) -> dict[str, Any]:
    """Thin seam so tests can monkeypatch out HTTP."""
    from buonaiuto_doc4llm.__main__ import _build_fetcher  # reuse registry wiring
    fetcher = _build_fetcher(service.base_dir)
    return fetcher.fetch(technology)


def refresh_active_projects(
    service: "DocsHubService", *, days: int = 30, dry_run: bool = False,
) -> dict[str, Any]:
    # Make sure newly-created project JSON files are synced into the DB
    # before we query which projects are active.
    try:
        service.sync_projects()
    except Exception as exc:  # noqa: BLE001
        print(f"[refresh_active] sync_projects failed: {exc}", file=sys.stderr)
    projects = list_active_projects(service, days=days)
    techs_union: set[str] = set()
    per_project: list[dict[str, Any]] = []

    for p in projects:
        needs_reinstall = (
            p["workspace_path"]
            and (time.time() - p["project_file_mtime"] >= FRESHNESS_SECONDS)
        )
        entry: dict[str, Any] = {
            "project_id": p["project_id"],
            "technologies": p["technologies"],
            "needs_reinstall": bool(needs_reinstall),
            "install_error": None,
        }
        if needs_reinstall and not dry_run:
            try:
                service.install_project(
                    project_root=Path(p["workspace_path"]),
                    project_id=p["project_id"],
                )
            except Exception as exc:  # noqa: BLE001
                entry["install_error"] = f"{type(exc).__name__}: {exc}"
                print(
                    f"[refresh_active] install_project({p['project_id']}) failed: {exc}",
                    file=sys.stderr,
                )
        techs_union.update(p["technologies"])
        per_project.append(entry)

    fetches: list[dict[str, Any]] = []
    fetched_any = False
    if not dry_run:
        for tech in sorted(techs_union):
            try:
                result = _fetch_technology(service, tech)
                fetches.append(result)
                fetched_any = True
            except Exception as exc:  # noqa: BLE001
                fetches.append({"technology": tech, "status": "error",
                                "error": f"{type(exc).__name__}: {exc}"})
                print(f"[refresh_active] fetch({tech}) failed: {exc}", file=sys.stderr)
        # Only scan when at least one fetch ran successfully; otherwise the
        # scan is just extra I/O that won't surface any new content.
        if fetched_any:
            service.scan()

    return {
        "dry_run": dry_run,
        "days": days,
        "projects": per_project,
        "technologies_to_fetch": sorted(techs_union),
        "fetches": fetches,
    }
