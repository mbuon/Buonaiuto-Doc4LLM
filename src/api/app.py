from __future__ import annotations

from typing import Callable

from api.mcp_http import HostedMCPGateway
from api.webhooks import StripeWebhookHandler
from control.quotas import QuotaLimiter


class ApiService:
    def __init__(
        self,
        gateway: HostedMCPGateway,
        stripe_handler: StripeWebhookHandler,
        quotas: QuotaLimiter,
        authenticate: Callable[[str], str | None],
    ):
        self.gateway = gateway
        self.stripe_handler = stripe_handler
        self.quotas = quotas
        self.authenticate = authenticate

    def handle_query(
        self,
        api_key: str,
        query_text: str,
        libraries: list[dict] | None,
        date_key: str,
        minute_key: str,
        daily_limit: int,
        rpm_limit: int,
        stream: bool = False,
        trace_id: str | None = None,
    ) -> dict | list[dict]:
        workspace_id = self.authenticate(api_key)
        if workspace_id is None:
            raise PermissionError("Invalid API key")

        if not self.quotas.can_increment_daily(workspace_id, date_key, limit=daily_limit):
            raise PermissionError("Daily quota exceeded")
        if not self.quotas.can_increment_rate(workspace_id, minute_key, rpm_limit=rpm_limit):
            raise PermissionError("Rate limit exceeded")
        self.quotas.increment_daily(workspace_id, date_key)
        self.quotas.increment_rate(workspace_id, minute_key)

        return self.gateway.query(
            api_key=api_key,
            query_text=query_text,
            libraries=libraries,
            stream=stream,
            trace_id=trace_id,
        )

    def handle_stripe_webhook(self, payload: str, signature_header: str) -> dict:
        return self.stripe_handler.handle_request(payload=payload, signature_header=signature_header)
