from pathlib import Path

import pytest

from api.app import ApiService
from api.mcp_http import HostedMCPGateway
from api.webhooks import StripeWebhookHandler
from control.billing import BillingService
from control.quotas import QuotaLimiter
from buonaiuto_doc4llm.service import DocsHubService


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _service(tmp_path: Path) -> DocsHubService:
    _write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    _write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    _write(
        tmp_path / "docs_center/technologies/react/docs/hooks.md",
        "# Hooks\n\nHooks let you use state.",
    )
    service = DocsHubService(tmp_path)
    service.scan()
    return service


def test_api_service_rejects_when_daily_quota_exceeded(tmp_path: Path) -> None:
    docs_service = _service(tmp_path)
    auth = lambda api_key: "ws-a" if api_key == "good" else None
    gateway = HostedMCPGateway(service=docs_service, authenticate=auth)
    billing = BillingService()
    stripe = StripeWebhookHandler(verifier=lambda payload, sig: {}, billing_service=billing)
    quotas = QuotaLimiter()
    api = ApiService(gateway=gateway, stripe_handler=stripe, quotas=quotas, authenticate=auth)

    api.handle_query(
        api_key="good",
        query_text="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        date_key="2026-03-18",
        minute_key="2026-03-18T16:10",
        daily_limit=1,
        rpm_limit=10,
        stream=False,
    )
    with pytest.raises(PermissionError, match="Daily quota exceeded"):
        api.handle_query(
            api_key="good",
            query_text="hooks",
            libraries=[{"id": "react", "version": "19.0"}],
            date_key="2026-03-18",
            minute_key="2026-03-18T16:11",
            daily_limit=1,
            rpm_limit=10,
            stream=False,
        )


def test_api_service_streams_query_and_processes_webhook(tmp_path: Path) -> None:
    docs_service = _service(tmp_path)
    auth = lambda api_key: "ws-a" if api_key == "good" else None
    gateway = HostedMCPGateway(service=docs_service, authenticate=auth)
    billing = BillingService()
    stripe = StripeWebhookHandler(
        verifier=lambda payload, sig: {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"workspace_id": "ws-a"}}},
        },
        billing_service=billing,
    )
    quotas = QuotaLimiter()
    api = ApiService(gateway=gateway, stripe_handler=stripe, quotas=quotas, authenticate=auth)

    events = api.handle_query(
        api_key="good",
        query_text="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        date_key="2026-03-18",
        minute_key="2026-03-18T16:20",
        daily_limit=10,
        rpm_limit=10,
        stream=True,
        trace_id="trace-query-123",
    )
    assert events[0]["event"] == "library_resolved"
    assert all(event["data"]["trace_id"] == "trace-query-123" for event in events)

    webhook_result = api.handle_stripe_webhook(payload='{"id":"evt_1"}', signature_header="sig")
    assert webhook_result["status"] == "processed"


def test_api_service_generates_trace_id_for_non_streaming_query(tmp_path: Path) -> None:
    docs_service = _service(tmp_path)
    auth = lambda api_key: "ws-a" if api_key == "good" else None
    gateway = HostedMCPGateway(service=docs_service, authenticate=auth)
    billing = BillingService()
    stripe = StripeWebhookHandler(verifier=lambda payload, sig: {}, billing_service=billing)
    quotas = QuotaLimiter()
    api = ApiService(gateway=gateway, stripe_handler=stripe, quotas=quotas, authenticate=auth)

    payload = api.handle_query(
        api_key="good",
        query_text="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        date_key="2026-03-18",
        minute_key="2026-03-18T16:21",
        daily_limit=10,
        rpm_limit=10,
        stream=False,
    )

    assert isinstance(payload["trace_id"], str)
    assert payload["trace_id"]
    assert payload["results"][0]["trace_id"] == payload["trace_id"]
