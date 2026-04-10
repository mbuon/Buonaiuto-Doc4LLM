from control.billing import BillingService


def _event(event_id: str, event_type: str, workspace_id: str) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": {"metadata": {"workspace_id": workspace_id}}},
    }


def test_billing_service_processes_event_once_by_event_id() -> None:
    billing = BillingService()
    event = _event("evt_1", "checkout.session.completed", "ws-a")

    first = billing.process_webhook_event(event)
    second = billing.process_webhook_event(event)

    assert first["status"] == "processed"
    assert second["status"] == "duplicate"


def test_billing_service_updates_workspace_state_from_event_types() -> None:
    billing = BillingService()
    billing.process_webhook_event(_event("evt_2", "checkout.session.completed", "ws-a"))
    assert billing.workspace_state("ws-a")["plan"] == "active"

    billing.process_webhook_event(_event("evt_3", "invoice.payment_failed", "ws-a"))
    assert billing.workspace_state("ws-a")["plan"] == "grace_period"

    billing.process_webhook_event(_event("evt_4", "customer.subscription.deleted", "ws-a"))
    assert billing.workspace_state("ws-a")["plan"] == "free"
