from api.admin import TrustAdminService


def test_admin_service_lists_quarantined_items() -> None:
    admin = TrustAdminService()
    admin.submit_for_review("chunk-1", "ws-a", "Suspicious instruction")
    admin.submit_for_review("chunk-2", "ws-a", "Prompt injection attempt")

    items = admin.list_quarantined(workspace_id="ws-a")
    assert len(items) == 2
    assert items[0]["status"] == "quarantined"


def test_admin_service_marks_item_reviewed() -> None:
    admin = TrustAdminService()
    admin.submit_for_review("chunk-1", "ws-a", "Suspicious instruction")
    updated = admin.mark_reviewed("chunk-1", reviewer="ops-user")

    assert updated["status"] == "reviewed"
    assert updated["reviewed_by"] == "ops-user"
