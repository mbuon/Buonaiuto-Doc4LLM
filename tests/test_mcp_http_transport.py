from __future__ import annotations
import threading
from buonaiuto_doc4llm.mcp_http_transport import SessionRegistry, SessionState


def test_allocate_returns_session_state():
    reg = SessionRegistry()
    state = reg.allocate(session_id="abc-123", project_id=None)
    assert isinstance(state, SessionState)
    assert state.session_id == "abc-123"
    assert state.project_id is None


def test_get_returns_allocated_session():
    reg = SessionRegistry()
    reg.allocate(session_id="abc-123", project_id="proj-1")
    state = reg.get("abc-123")
    assert state is not None
    assert state.project_id == "proj-1"


def test_get_unknown_session_returns_none():
    reg = SessionRegistry()
    assert reg.get("does-not-exist") is None


def test_thread_safe_allocation():
    reg = SessionRegistry()
    ids = [f"sid-{i}" for i in range(50)]
    results: list[SessionState] = []
    lock = threading.Lock()

    def allocate(sid: str) -> None:
        state = reg.allocate(session_id=sid, project_id=None)
        with lock:
            results.append(state)

    threads = [threading.Thread(target=allocate, args=(sid,)) for sid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 50
    assert len({s.session_id for s in results}) == 50
