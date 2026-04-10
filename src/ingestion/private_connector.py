from __future__ import annotations

from datetime import UTC, datetime

from telemetry import ensure_trace_id


class PrivateRepoConnector:
    def build_ingestion_record(
        self,
        repo: str,
        workspace_id: str,
        rel_path: str,
        trace_id: str | None = None,
    ) -> dict[str, str]:
        normalized_repo = repo.strip()
        workspace = workspace_id.strip()
        normalized_rel_path = rel_path.strip()
        if not normalized_repo:
            raise ValueError("repo is required")
        if not workspace:
            raise ValueError("workspace_id is required")
        if not normalized_rel_path:
            raise ValueError("rel_path is required")

        return {
            "workspace_id": workspace,
            "repo": normalized_repo,
            "rel_path": normalized_rel_path,
            "visibility": "private",
            "ingested_at": datetime.now(UTC).isoformat(),
            "trace_id": ensure_trace_id(trace_id),
        }
