from __future__ import annotations

from typing import Callable

from control.billing import BillingService


class StripeWebhookHandler:
    def __init__(self, verifier: Callable[[str, str], dict], billing_service: BillingService):
        self.verifier = verifier
        self.billing_service = billing_service

    def handle_request(self, payload: str, signature_header: str) -> dict:
        try:
            event = self.verifier(payload, signature_header)
        except Exception as exc:
            raise ValueError("Invalid Stripe signature") from exc
        return self.billing_service.process_webhook_event(event)

