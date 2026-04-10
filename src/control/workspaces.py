from __future__ import annotations

from dataclasses import dataclass


VALID_ROLES = {"owner", "admin", "member", "viewer"}


@dataclass(frozen=True)
class Membership:
    workspace_id: str
    user_id: str
    role: str


class WorkspaceAccessService:
    def validate_role(self, role: str) -> str:
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role}")
        return role

    def can_access_workspace(
        self, user_id: str, workspace_id: str, memberships: list[Membership]
    ) -> bool:
        for membership in memberships:
            if membership.user_id == user_id and membership.workspace_id == workspace_id:
                self.validate_role(membership.role)
                return True
        return False

