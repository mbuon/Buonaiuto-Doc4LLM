from control.abuse import AbusePreventionService


def test_email_verification_gate_for_api_key_issuance() -> None:
    service = AbusePreventionService()
    assert service.can_issue_api_key(email_verified=False, workspace_id="ws-a", date_key="2026-03-18") is False


def test_ip_level_free_account_limit() -> None:
    service = AbusePreventionService()
    ip = "203.0.113.10"
    day = "2026-03-18"

    assert service.can_create_free_account(ip, day) is True
    service.record_free_account_creation(ip, day)
    assert service.can_create_free_account(ip, day) is True
    service.record_free_account_creation(ip, day)
    assert service.can_create_free_account(ip, day) is True
    service.record_free_account_creation(ip, day)
    assert service.can_create_free_account(ip, day) is False


def test_api_key_creation_rate_limit_per_workspace() -> None:
    service = AbusePreventionService()
    ws = "ws-a"
    day = "2026-03-18"
    for _ in range(5):
        assert service.can_issue_api_key(email_verified=True, workspace_id=ws, date_key=day) is True
        service.record_api_key_created(ws, day)
    assert service.can_issue_api_key(email_verified=True, workspace_id=ws, date_key=day) is False
