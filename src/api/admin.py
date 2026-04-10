from __future__ import annotations

from datetime import UTC, datetime


class TrustAdminService:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, str]] = {}

    def submit_for_review(self, chunk_id: str, workspace_id: str, reason: str) -> dict[str, str]:
        normalized_chunk_id = chunk_id.strip()
        normalized_workspace_id = workspace_id.strip()
        normalized_reason = reason.strip()
        if not normalized_chunk_id:
            raise ValueError("chunk_id is required")
        if not normalized_workspace_id:
            raise ValueError("workspace_id is required")
        if not normalized_reason:
            raise ValueError("reason is required")

        item = {
            "chunk_id": normalized_chunk_id,
            "workspace_id": normalized_workspace_id,
            "reason": normalized_reason,
            "status": "quarantined",
            "submitted_at": datetime.now(UTC).isoformat(),
            "reviewed_by": "",
        }
        self._items[normalized_chunk_id] = item
        return item

    def list_quarantined(self, workspace_id: str) -> list[dict[str, str]]:
        normalized_workspace_id = workspace_id.strip()
        return [
            item
            for item in self._items.values()
            if item["workspace_id"] == normalized_workspace_id and item["status"] == "quarantined"
        ]

    def mark_reviewed(self, chunk_id: str, reviewer: str) -> dict[str, str]:
        normalized_chunk_id = chunk_id.strip()
        normalized_reviewer = reviewer.strip()
        if not normalized_reviewer:
            raise ValueError("reviewer is required")
        if normalized_chunk_id not in self._items:
            raise ValueError("Unknown chunk_id")
        item = self._items[normalized_chunk_id]
        item["status"] = "reviewed"
        item["reviewed_by"] = normalized_reviewer
        return item
