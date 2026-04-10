import json
from pathlib import Path

from buonaiuto_doc4llm.mcp_server import MCPServer


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_tools_list_exposes_search_documentation(tmp_path: Path) -> None:
    server = MCPServer(tmp_path)
    response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = response["result"]["tools"]

    assert any(tool["name"] == "search_documentation" for tool in tools)
    assert any(tool["name"] == "list_supported_libraries" for tool in tools)
    assert any(tool["name"] == "read_full_page" for tool in tools)


def test_search_documentation_tool_returns_results(tmp_path: Path) -> None:
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    write(
        tmp_path / "docs_center/technologies/react/docs/hooks.md",
        "# Hooks\n\nHooks let you use state.",
    )

    server = MCPServer(tmp_path)
    server.service.scan()

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "search_documentation",
                "arguments": {
                    "query": "hooks",
                    "libraries": [{"id": "react", "version": "19.0"}],
                    "limit": 5,
                },
            },
        }
    )

    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["retrieval_mode"] == "lexical_only"
    assert payload["results"]
    assert payload["results"][0]["technology"] == "react"


def test_list_supported_libraries_returns_library_and_version(tmp_path: Path) -> None:
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    write(
        tmp_path / "docs_center/technologies/react/docs/hooks.md",
        "# Hooks\n\nHooks let you use state.",
    )

    server = MCPServer(tmp_path)
    server.service.scan()
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_supported_libraries", "arguments": {}},
        }
    )

    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload
    assert payload[0]["library_id"] == "react"
    assert payload[0]["version"] == "19.0"


def test_read_full_page_returns_full_document_content(tmp_path: Path) -> None:
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    write(
        tmp_path / "docs_center/technologies/react/docs/hooks.md",
        "# Hooks\n\nHooks let you use state.",
    )

    server = MCPServer(tmp_path)
    server.service.scan()
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "read_full_page",
                "arguments": {
                    "library_id": "react",
                    "version": "19.0",
                    "rel_path": "docs/hooks.md",
                },
            },
        }
    )

    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["technology"] == "react"
    assert payload["version"] == "19.0"
    assert "Hooks let you use state." in payload["content"]
