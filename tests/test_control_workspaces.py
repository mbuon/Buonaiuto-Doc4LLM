import pytest

from control.workspaces import Membership, WorkspaceAccessService


def test_validate_role_rejects_invalid_role() -> None:
    service = WorkspaceAccessService()
    with pytest.raises(ValueError, match="Invalid role"):
        service.validate_role("superadmin")


def test_access_service_rejects_cross_workspace_access() -> None:
    service = WorkspaceAccessService()
    memberships = [
        Membership(workspace_id="ws-a", user_id="u-1", role="owner"),
    ]

    assert service.can_access_workspace("u-1", "ws-a", memberships) is True
    assert service.can_access_workspace("u-1", "ws-b", memberships) is False
