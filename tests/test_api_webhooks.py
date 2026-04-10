import pytest

from api.webhooks import StripeWebhookHandler
from control.billing import BillingService


def test_stripe_webhook_handler_rejects_invalid_signature() -> None:
    billing = BillingService()

    def _bad_verifier(payload: str, signature: str):
        raise ValueError("signature mismatch")

    handler = StripeWebhookHandler(verifier=_bad_verifier, billing_service=billing)
    with pytest.raises(ValueError, match="Invalid Stripe signature"):
        handler.handle_request(payload='{"id":"evt_1"}', signature_header="bad")


def test_stripe_webhook_handler_processes_verified_event() -> None:
    billing = BillingService()

    def _verifier(payload: str, signature: str):
        return {
            "id": "evt_10",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"workspace_id": "ws-a"}}},
        }

    handler = StripeWebhookHandler(verifier=_verifier, billing_service=billing)
    result = handler.handle_request(payload='{"id":"evt_10"}', signature_header="good")

    assert result["status"] == "processed"
    assert result["event_id"] == "evt_10"
