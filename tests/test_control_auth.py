import pytest

from control.auth import AuthService
from control.workspaces import Membership


def test_auth_service_authenticates_known_token() -> None:
    auth = AuthService(token_to_user={"tok_1": "user-1"})
    assert auth.authenticate_token("tok_1") == "user-1"


def test_auth_service_rejects_unknown_token() -> None:
    auth = AuthService(token_to_user={"tok_1": "user-1"})
    with pytest.raises(PermissionError, match="Invalid token"):
        auth.authenticate_token("invalid")


def test_auth_service_enforces_workspace_membership() -> None:
    auth = AuthService(token_to_user={"tok_1": "user-1"})
    memberships = [Membership(workspace_id="ws-a", user_id="user-1", role="owner")]

    assert auth.authorize_workspace("user-1", "ws-a", memberships) is True
    with pytest.raises(PermissionError, match="Workspace access denied"):
        auth.authorize_workspace("user-1", "ws-b", memberships)
