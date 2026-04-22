from __future__ import annotations
import threading
from pathlib import Path
from buonaiuto_doc4llm.mcp_http_transport import SessionRegistry, SessionState
from buonaiuto_doc4llm.mcp_server import MCPServer


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


def test_update_project_sets_project_id():
    reg = SessionRegistry()
    reg.allocate(session_id="s1", project_id=None)
    reg.update_project("s1", "proj-x")
    state = reg.get("s1")
    assert state is not None
    assert state.project_id == "proj-x"


def test_update_project_does_not_overwrite_existing():
    reg = SessionRegistry()
    reg.allocate(session_id="s2", project_id="existing")
    reg.update_project("s2", "new-value")
    state = reg.get("s2")
    assert state is not None
    assert state.project_id == "existing"


def test_handle_request_uses_session_state_for_tool_logging(tmp_path: Path):
    server = MCPServer(tmp_path)
    server.service.scan()
    state = SessionState(session_id="http-session-1", project_id=None)

    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        session_state=state,
    )
    assert "result" in response
    assert "tools" in response["result"]


def test_handle_request_without_session_state_still_works(tmp_path: Path):
    server = MCPServer(tmp_path)
    server.service.scan()

    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert "result" in response
