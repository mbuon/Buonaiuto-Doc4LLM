from control.webhooks_outbound import OutboundWebhookDispatcher


def test_outbound_webhook_dispatcher_signs_payload_and_deduplicates_event() -> None:
    sent = []

    def sender(url: str, payload: str, headers: dict[str, str]) -> None:
        sent.append({"url": url, "payload": payload, "headers": headers})

    dispatcher = OutboundWebhookDispatcher(secret="top-secret", sender=sender)
    payload = {"event_id": "evt-1", "workspace_id": "ws-a", "type": "index.completed"}

    first = dispatcher.dispatch(url="https://example.com/webhook", payload=payload)
    second = dispatcher.dispatch(url="https://example.com/webhook", payload=payload)

    assert first["status"] == "sent"
    assert second["status"] == "duplicate"
    assert len(sent) == 1
    assert "X-Webhook-Signature" in sent[0]["headers"]
