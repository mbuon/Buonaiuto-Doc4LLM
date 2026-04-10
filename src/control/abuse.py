from __future__ import annotations


class AbusePreventionService:
    def __init__(self) -> None:
        self._free_accounts_by_ip_day: dict[tuple[str, str], int] = {}
        self._api_keys_by_workspace_day: dict[tuple[str, str], int] = {}

    def can_create_free_account(self, ip_address: str, date_key: str) -> bool:
        count = self._free_accounts_by_ip_day.get((ip_address, date_key), 0)
        return count < 3

    def record_free_account_creation(self, ip_address: str, date_key: str) -> None:
        key = (ip_address, date_key)
        self._free_accounts_by_ip_day[key] = self._free_accounts_by_ip_day.get(key, 0) + 1

    def can_issue_api_key(self, email_verified: bool, workspace_id: str, date_key: str) -> bool:
        if not email_verified:
            return False
        count = self._api_keys_by_workspace_day.get((workspace_id, date_key), 0)
        return count < 5

    def record_api_key_created(self, workspace_id: str, date_key: str) -> None:
        key = (workspace_id, date_key)
        self._api_keys_by_workspace_day[key] = self._api_keys_by_workspace_day.get(key, 0) + 1

