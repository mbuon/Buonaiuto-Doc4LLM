from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceBillingState:
    workspace_id: str
    plan: str


class BillingService:
    def __init__(self) -> None:
        self._processed_event_ids: set[str] = set()
        self._workspace_states: dict[str, WorkspaceBillingState] = {}

    def process_webhook_event(self, event: dict) -> dict:
        event_id = str(event.get("id", "")).strip()
        if not event_id:
            raise ValueError("Webhook event missing id.")

        if event_id in self._processed_event_ids:
            return {"status": "duplicate", "event_id": event_id}

        self._processed_event_ids.add(event_id)
        event_type = str(event.get("type", "")).strip()
        workspace_id = self._workspace_id_from_event(event)

        if workspace_id:
            if event_type == "checkout.session.completed":
                self._workspace_states[workspace_id] = WorkspaceBillingState(workspace_id, "active")
            elif event_type == "customer.subscription.updated":
                self._workspace_states[workspace_id] = WorkspaceBillingState(workspace_id, "active")
            elif event_type == "customer.subscription.deleted":
                self._workspace_states[workspace_id] = WorkspaceBillingState(workspace_id, "free")
            elif event_type == "invoice.payment_succeeded":
                self._workspace_states[workspace_id] = WorkspaceBillingState(workspace_id, "active")
            elif event_type == "invoice.payment_failed":
                self._workspace_states[workspace_id] = WorkspaceBillingState(workspace_id, "grace_period")

        return {"status": "processed", "event_id": event_id, "workspace_id": workspace_id}

    def workspace_state(self, workspace_id: str) -> dict[str, str]:
        state = self._workspace_states.get(workspace_id)
        if state is None:
            return {"workspace_id": workspace_id, "plan": "free"}
        return {"workspace_id": state.workspace_id, "plan": state.plan}

    @staticmethod
    def _workspace_id_from_event(event: dict) -> str | None:
        data = event.get("data", {})
        obj = data.get("object", {}) if isinstance(data, dict) else {}
        metadata = obj.get("metadata", {}) if isinstance(obj, dict) else {}
        workspace_id = metadata.get("workspace_id") if isinstance(metadata, dict) else None
        if workspace_id is None:
            return None
        workspace = str(workspace_id).strip()
        return workspace or None

