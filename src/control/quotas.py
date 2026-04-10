from __future__ import annotations


class QuotaLimiter:
    def __init__(self) -> None:
        self._daily_counts: dict[tuple[str, str], int] = {}
        self._minute_counts: dict[tuple[str, str], int] = {}

    def can_increment_daily(self, workspace_id: str, date_key: str, limit: int) -> bool:
        key = self._daily_key(workspace_id, date_key)
        current = self._daily_counts.get(key, 0)
        return current < limit

    def increment_daily(self, workspace_id: str, date_key: str) -> int:
        key = self._daily_key(workspace_id, date_key)
        updated = self._daily_counts.get(key, 0) + 1
        self._daily_counts[key] = updated
        return updated

    def check_and_increment_daily(self, workspace_id: str, date_key: str, limit: int) -> bool:
        if not self.can_increment_daily(workspace_id, date_key, limit):
            return False
        self.increment_daily(workspace_id, date_key)
        return True

    def can_increment_rate(self, workspace_id: str, minute_key: str, rpm_limit: int) -> bool:
        key = self._rate_key(workspace_id, minute_key)
        current = self._minute_counts.get(key, 0)
        return current < rpm_limit

    def increment_rate(self, workspace_id: str, minute_key: str) -> int:
        key = self._rate_key(workspace_id, minute_key)
        updated = self._minute_counts.get(key, 0) + 1
        self._minute_counts[key] = updated
        return updated

    def check_and_increment_rate(self, workspace_id: str, minute_key: str, rpm_limit: int) -> bool:
        if not self.can_increment_rate(workspace_id, minute_key, rpm_limit):
            return False
        self.increment_rate(workspace_id, minute_key)
        return True

    @staticmethod
    def _daily_key(workspace_id: str, date_key: str) -> tuple[str, str]:
        workspace = workspace_id.strip()
        day = date_key.strip()
        if not workspace:
            raise ValueError("workspace_id is required")
        if not day:
            raise ValueError("date_key is required")
        return workspace, day

    @staticmethod
    def _rate_key(workspace_id: str, minute_key: str) -> tuple[str, str]:
        workspace = workspace_id.strip()
        minute = minute_key.strip()
        if not workspace:
            raise ValueError("workspace_id is required")
        if not minute:
            raise ValueError("minute_key is required")
        return workspace, minute
