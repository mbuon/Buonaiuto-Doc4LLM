from __future__ import annotations

import hashlib
import hmac
import json
from typing import Callable


class OutboundWebhookDispatcher:
    def __init__(self, secret: str, sender: Callable[[str, str, dict[str, str]], None]):
        self.secret = secret.encode("utf-8")
        self.sender = sender
        self._sent_event_ids: set[str] = set()

    def dispatch(self, url: str, payload: dict) -> dict[str, str]:
        event_id = str(payload.get("event_id", "")).strip()
        if not event_id:
            raise ValueError("payload.event_id is required")

        if event_id in self._sent_event_ids:
            return {"status": "duplicate", "event_id": event_id}

        payload_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = hmac.new(self.secret, payload_text.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Event-Id": event_id,
        }
        self.sender(url, payload_text, headers)
        self._sent_event_ids.add(event_id)
        return {"status": "sent", "event_id": event_id}

