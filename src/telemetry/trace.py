from __future__ import annotations

from uuid import uuid4


def ensure_trace_id(trace_id: str | None = None) -> str:
    candidate = (trace_id or "").strip()
    if candidate:
        return candidate
    return uuid4().hex
