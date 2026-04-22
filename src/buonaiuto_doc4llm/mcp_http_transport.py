from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SessionState:
    session_id: str
    project_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def allocate(self, *, session_id: str, project_id: str | None) -> SessionState:
        state = SessionState(session_id=session_id, project_id=project_id)
        with self._lock:
            self._sessions[session_id] = state
        return state

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)

    def update_project(self, session_id: str, project_id: str) -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is not None and state.project_id is None:
                state.project_id = project_id
