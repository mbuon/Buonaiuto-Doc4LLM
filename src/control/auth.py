from __future__ import annotations

from control.workspaces import Membership, WorkspaceAccessService


class AuthService:
    def __init__(self, token_to_user: dict[str, str]):
        self.token_to_user = token_to_user
        self.access_service = WorkspaceAccessService()

    def authenticate_token(self, token: str) -> str:
        user_id = self.token_to_user.get(token)
        if user_id is None:
            raise PermissionError("Invalid token")
        return user_id

    def authorize_workspace(
        self, user_id: str, workspace_id: str, memberships: list[Membership]
    ) -> bool:
        if not self.access_service.can_access_workspace(user_id, workspace_id, memberships):
            raise PermissionError("Workspace access denied")
        return True

