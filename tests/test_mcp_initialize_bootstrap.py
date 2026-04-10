import json
from pathlib import Path

from buonaiuto_doc4llm.mcp_server import MCPServer


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_initialize_bootstraps_from_project_path(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"
    _write(project_root / "package.json", json.dumps({"dependencies": {"react": "^19.0.0"}}))

    server = MCPServer(base_dir)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "project_path": str(project_root),
                "project_id": "myproject",
            },
        }
    )

    bootstrap = response["result"]["bootstrap"]
    assert bootstrap is not None
    assert bootstrap["project_id"] == "myproject"
    assert "react" in bootstrap["technologies_detected"]

    libs_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_supported_libraries", "arguments": {}},
        }
    )
    payload = json.loads(libs_response["result"]["content"][0]["text"])
    assert any(item["library_id"] == "react" for item in payload)


def test_initialize_bootstraps_from_workspace_folders_uri(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"
    _write(project_root / "package.json", json.dumps({"dependencies": {"react": "^19.0.0"}}))

    server = MCPServer(base_dir)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "initialize",
            "params": {
                "workspaceFolders": [{"uri": f"file://{project_root}", "name": "myproject"}],
            },
        }
    )

    bootstrap = response["result"]["bootstrap"]
    assert bootstrap is not None
    assert bootstrap["project_id"] == "myproject"


def test_initialize_without_workspace_context_keeps_bootstrap_none(tmp_path: Path) -> None:
    server = MCPServer(tmp_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {},
        }
    )

    assert response["result"]["bootstrap"] is None
