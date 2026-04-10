import json
from pathlib import Path

from buonaiuto_doc4llm.mcp_server import MCPServer
from buonaiuto_doc4llm.service import DocsHubService
from buonaiuto_doc4llm.auto_setup import bootstrap_project, detect_project_technologies


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_detect_project_technologies_from_common_files(tmp_path: Path) -> None:
    project_root = tmp_path / "myproject"
    _write(
        project_root / "package.json",
        json.dumps(
            {
                "dependencies": {
                    "react": "^19.0.0",
                    "@supabase/supabase-js": "^2.0.0",
                },
                "devDependencies": {"typescript": "^5.0.0"},
            }
        ),
    )
    _write(project_root / "requirements.txt", "fastapi\nsupabase\n")

    technologies = detect_project_technologies(project_root)

    assert "react" in technologies
    assert "supabase" in technologies
    assert "fastapi" in technologies


def test_detect_project_technologies_uses_registry_package_aliases(tmp_path: Path) -> None:
    project_root = tmp_path / "myproject"
    _write(
        project_root / "package.json",
        json.dumps(
            {
                "dependencies": {
                    "ai": "^4.0.0",
                    "@ai-sdk/openai": "^1.0.0",
                    "@shadcn/ui": "^0.8.0",
                }
            }
        ),
    )

    technologies = detect_project_technologies(project_root)

    assert "vercel-ai-sdk" in technologies
    assert "shadcn-ui" in technologies


def test_bootstrap_project_writes_project_and_copies_seed_docs(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"
    seed_root = tmp_path / "seed" / "technologies"

    _write(project_root / "package.json", json.dumps({"dependencies": {"react": "^19.0.0"}}))
    _write(seed_root / "react" / "manifest.json", '{"technology":"react","version":"19.0"}')
    _write(seed_root / "react" / "docs" / "hooks.md", "# Hooks\n\nUse hooks.")

    result = bootstrap_project(
        base_dir=base_dir,
        project_root=project_root,
        project_id="myproject",
        seed_technologies_root=seed_root,
    )

    project_file = base_dir / "docs_center" / "projects" / "myproject.json"
    assert project_file.exists()
    payload = json.loads(project_file.read_text(encoding="utf-8"))
    assert payload["project_id"] == "myproject"
    assert payload["technologies"] == ["react"]

    copied_doc = base_dir / "docs_center" / "technologies" / "react" / "docs" / "hooks.md"
    assert copied_doc.exists()
    assert result["technologies_detected"] == ["react"]
    assert result["copied"] == ["react"]


def test_service_install_project_scans_and_returns_searchable_docs(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"
    seed_root = tmp_path / "seed" / "technologies"

    _write(project_root / "package.json", json.dumps({"dependencies": {"react": "^19.0.0"}}))
    _write(seed_root / "react" / "manifest.json", '{"technology":"react","version":"19.0"}')
    _write(seed_root / "react" / "docs" / "hooks.md", "# Hooks\n\nUse hooks for state.")

    service = DocsHubService(base_dir)
    summary = service.install_project(
        project_root=project_root,
        project_id="myproject",
        seed_technologies_root=seed_root,
    )

    assert summary["project_id"] == "myproject"
    assert summary["scan_summary"]
    assert any(item["library_id"] == "react" for item in service.list_supported_libraries())

    result = service.search_documentation(
        query="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        workspace_id="local",
    )
    assert result["results"]


def test_install_project_fetches_docs_for_detected_technologies(
    tmp_path: Path, monkeypatch,
) -> None:
    """install_project should auto-fetch docs from the web for detected techs."""
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"
    _write(project_root / "package.json", json.dumps({"dependencies": {"react": "^19.0.0"}}))

    # Stub HttpDocFetcher.fetch to avoid real HTTP calls
    fetched_techs: list[str] = []

    def fake_fetch(self, technology):
        fetched_techs.append(technology)
        # Write a doc so scan() finds it
        tech_dir = self.base_dir / "docs_center" / "technologies" / technology / "docs"
        tech_dir.mkdir(parents=True, exist_ok=True)
        (tech_dir / "intro.md").write_text(f"# {technology}\n\nHello.")
        return {"fetched": True, "technology": technology}

    monkeypatch.setattr(
        "ingestion.http_fetcher.HttpDocFetcher.fetch", fake_fetch,
    )

    service = DocsHubService(base_dir)
    summary = service.install_project(
        project_root=project_root,
        project_id="myproject",
    )

    assert "react" in fetched_techs
    assert len(summary["fetch_results"]) > 0
    assert summary["fetch_errors"] == []


def test_install_project_reports_fetch_errors_gracefully(
    tmp_path: Path, monkeypatch,
) -> None:
    """Techs not in the registry should appear in fetch_errors, not crash."""
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"
    # "angular" is in PACKAGE_TO_TECHNOLOGY but not in registry.json
    _write(
        project_root / "package.json",
        json.dumps({"dependencies": {"angular": "^17.0.0"}}),
    )

    service = DocsHubService(base_dir)
    summary = service.install_project(
        project_root=project_root,
        project_id="myproject",
    )

    assert any(e["technology"] == "angular" for e in summary["fetch_errors"])
    assert summary["scan_summary"] is not None


# ---------------------------------------------------------------------------
# Local llms.txt ingestion
# ---------------------------------------------------------------------------

def test_detect_from_local_llms_txt_root_level(tmp_path: Path) -> None:
    """llms.txt at project root is detected as a technology."""
    from buonaiuto_doc4llm.auto_setup import _detect_from_local_llms_txt
    project_root = tmp_path / "myproject"
    _write(project_root / "llms.txt", "# My Lib\n\nDocs here.")
    found = _detect_from_local_llms_txt(project_root)
    assert "myproject" in found


def test_detect_from_local_llms_txt_subdir(tmp_path: Path) -> None:
    """llms-full.txt inside a named subdirectory uses the dir name as tech ID."""
    from buonaiuto_doc4llm.auto_setup import _detect_from_local_llms_txt
    project_root = tmp_path / "myproject"
    _write(project_root / "docs" / "mylibrary" / "llms-full.txt", "# MyLibrary\n\nContent.")
    found = _detect_from_local_llms_txt(project_root)
    assert "mylibrary" in found


def test_detect_from_local_llms_txt_multiple(tmp_path: Path) -> None:
    """Multiple llms.txt files in different subdirs all detected."""
    from buonaiuto_doc4llm.auto_setup import _detect_from_local_llms_txt
    project_root = tmp_path / "myproject"
    _write(project_root / "vendor" / "alpha" / "llms.txt", "# Alpha")
    _write(project_root / "vendor" / "beta" / "llms-full.txt", "# Beta")
    found = _detect_from_local_llms_txt(project_root)
    assert "alpha" in found
    assert "beta" in found


def test_ingest_local_llms_files_copies_to_docs_center(tmp_path: Path) -> None:
    """ingest_local_llms_files copies llms.txt content into docs_center."""
    from buonaiuto_doc4llm.auto_setup import ingest_local_llms_files
    project_root = tmp_path / "myproject"
    base_dir = tmp_path / "base"
    content = "# MyLib\n\nSome documentation."
    _write(project_root / "docs" / "mylib" / "llms-full.txt", content)

    result = ingest_local_llms_files(project_root, base_dir)

    dest = base_dir / "docs_center" / "technologies" / "mylib" / "llms-full.txt"
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == content
    assert "mylib" in result["ingested"]


def test_install_project_uses_local_llms_txt_without_web_fetch(
    tmp_path: Path, monkeypatch,
) -> None:
    """install_project ingests local llms.txt and skips web fetch for that tech."""
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"

    # Project has a local llms.txt — no package.json reference needed
    _write(project_root / "docs" / "myinternal" / "llms.txt", "# Internal Docs\n\nContent.")

    fetched_techs: list[str] = []

    def fake_fetch(self, technology: str):
        fetched_techs.append(technology)
        return {"fetched": True, "technology": technology}

    monkeypatch.setattr("ingestion.http_fetcher.HttpDocFetcher.fetch", fake_fetch)

    service = DocsHubService(base_dir)
    summary = service.install_project(project_root=project_root, project_id="myproject")

    # Local tech is in scan results
    libs = [lib["library_id"] for lib in service.list_supported_libraries()]
    assert "myinternal" in libs

    # Web fetch was NOT called for the locally-provided tech
    assert "myinternal" not in fetched_techs

    # Reported in local_ingested, not fetch_results
    assert "myinternal" in summary.get("local_ingested", [])


def test_mcp_server_install_project_tool_bootstraps_project(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    project_root = tmp_path / "myproject"
    _write(project_root / "package.json", json.dumps({"dependencies": {"react": "^19.0.0"}}))

    server = MCPServer(base_dir)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "install_project",
                "arguments": {
                    "project_path": str(project_root),
                    "project_id": "myproject",
                },
            },
        }
    )

    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["project_id"] == "myproject"
    assert "technologies_detected" in payload
