from __future__ import annotations

from dataclasses import dataclass, replace

from telemetry import ensure_trace_id


@dataclass(frozen=True)
class IngestionTrigger:
    source_type: str
    library_id: str
    version: str | None
    event_key: str
    trace_id: str | None = None


class IngestionScheduler:
    def __init__(self) -> None:
        self._seen_event_keys: set[str] = set()
        self._pending: list[IngestionTrigger] = []

    def enqueue(self, trigger: IngestionTrigger) -> bool:
        event_key = trigger.event_key.strip()
        if not event_key:
            raise ValueError("event_key is required")
        if event_key in self._seen_event_keys:
            return False
        self._seen_event_keys.add(event_key)
        self._pending.append(
            replace(
                trigger,
                event_key=event_key,
                trace_id=ensure_trace_id(trigger.trace_id),
            )
        )
        return True

    def pending(self) -> list[IngestionTrigger]:
        return list(self._pending)
