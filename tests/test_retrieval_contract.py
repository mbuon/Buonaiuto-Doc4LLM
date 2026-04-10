from buonaiuto_doc4llm.service import DocsHubService
from retrieval.retriever import HybridRetriever, RetrievalDocument, RetrievalQuery


class StubQdrantClient:
    def query_hybrid(self, query) -> list[dict]:
        return [
            {
                "workspace_id": query.workspace_id,
                "library_id": query.library_id,
                "version": query.version,
                "rel_path": "docs/hooks.md",
                "title": "Hooks",
                "source_uri": "doc://react/docs/hooks.md",
                "score": 0.99,
                "snippet": "Use hooks for state and effects.",
            }
        ]


def test_hybrid_retriever_filters_workspace_library_and_version() -> None:
    retriever = HybridRetriever()
    documents = [
        RetrievalDocument(
            workspace_id="ws-a",
            library_id="react",
            version="19.0",
            rel_path="docs/hooks.md",
            title="Hooks",
            content="Use hooks for state and effects.",
            source_uri="doc://react/docs/hooks.md",
        ),
        RetrievalDocument(
            workspace_id="ws-a",
            library_id="react",
            version="18.0",
            rel_path="docs/legacy-hooks.md",
            title="Legacy Hooks",
            content="Old guidance for hooks.",
            source_uri="doc://react/docs/legacy-hooks.md",
        ),
        RetrievalDocument(
            workspace_id="ws-b",
            library_id="react",
            version="19.0",
            rel_path="docs/private.md",
            title="Private",
            content="Workspace B only.",
            source_uri="doc://react/docs/private.md",
        ),
    ]

    query = RetrievalQuery(
        text="hooks",
        workspace_id="ws-a",
        library_id="react",
        version="19.0",
        limit=5,
    )
    results = retriever.search(documents, query)

    assert results.retrieval_mode == "lexical_only"
    assert len(results.matches) == 1
    assert results.matches[0].library_id == "react"
    assert results.matches[0].version == "19.0"
    assert results.matches[0].workspace_id == "ws-a"


def test_hybrid_retriever_uses_qdrant_when_available() -> None:
    retriever = HybridRetriever(qdrant_client=StubQdrantClient())
    documents = [
        RetrievalDocument(
            workspace_id="ws-a",
            library_id="react",
            version="19.0",
            rel_path="docs/hooks.md",
            title="Hooks",
            content="Use hooks for state and effects.",
            source_uri="doc://react/docs/hooks.md",
        )
    ]

    query = RetrievalQuery(
        text="hooks",
        workspace_id="ws-a",
        library_id="react",
        version="19.0",
        limit=5,
    )
    results = retriever.search(documents, query)

    assert results.retrieval_mode == "hybrid"
    # Vector score (0.99) + title_coverage boost (0.3) + path_coverage boost (0.15) = 1.44
    assert results.matches[0].score == 1.44


def test_service_search_docs_includes_retrieval_mode(tmp_path) -> None:
    docs_root = tmp_path / "docs_center"
    (docs_root / "projects").mkdir(parents=True, exist_ok=True)
    (docs_root / "technologies/react/docs").mkdir(parents=True, exist_ok=True)
    (docs_root / "projects/app.json").write_text(
        '{"project_id":"app","name":"App","technologies":["react"]}',
        encoding="utf-8",
    )
    (docs_root / "technologies/react/manifest.json").write_text(
        '{"technology":"react","version":"19.0"}',
        encoding="utf-8",
    )
    (docs_root / "technologies/react/docs/hooks.md").write_text(
        "# Hooks\n\nHooks let you use state.",
        encoding="utf-8",
    )

    service = DocsHubService(tmp_path)
    service.scan()

    payload = service.search_docs("react", "hooks", limit=5)

    assert payload["results"]
    assert payload["retrieval_mode"] == "lexical_only"
    assert payload["results"][0]["retrieval_mode"] == "lexical_only"


def test_service_search_documentation_filters_requested_libraries(tmp_path) -> None:
    docs_root = tmp_path / "docs_center"
    (docs_root / "projects").mkdir(parents=True, exist_ok=True)
    (docs_root / "technologies/react/docs").mkdir(parents=True, exist_ok=True)
    (docs_root / "technologies/python/docs").mkdir(parents=True, exist_ok=True)
    (docs_root / "projects/app.json").write_text(
        '{"project_id":"app","name":"App","technologies":["react","python"]}',
        encoding="utf-8",
    )
    (docs_root / "technologies/react/manifest.json").write_text(
        '{"technology":"react","version":"19.0"}',
        encoding="utf-8",
    )
    (docs_root / "technologies/python/manifest.json").write_text(
        '{"technology":"python","version":"3.13"}',
        encoding="utf-8",
    )
    (docs_root / "technologies/react/docs/hooks.md").write_text(
        "# Hooks\n\nUse hooks in React.",
        encoding="utf-8",
    )
    (docs_root / "technologies/python/docs/pathlib.md").write_text(
        "# Pathlib\n\nUse pathlib in Python.",
        encoding="utf-8",
    )

    service = DocsHubService(tmp_path)
    service.scan()

    payload = service.search_documentation(
        query="use",
        libraries=[{"id": "react"}],
        workspace_id="local",
        limit=10,
    )

    assert payload["requested_libraries"] == ["react"]
    assert payload["results"]
    assert all(item["technology"] == "react" for item in payload["results"])


def test_service_search_documentation_applies_version_filter(tmp_path) -> None:
    docs_root = tmp_path / "docs_center"
    (docs_root / "projects").mkdir(parents=True, exist_ok=True)
    (docs_root / "technologies/react/docs").mkdir(parents=True, exist_ok=True)
    (docs_root / "projects/app.json").write_text(
        '{"project_id":"app","name":"App","technologies":["react"]}',
        encoding="utf-8",
    )
    (docs_root / "technologies/react/manifest.json").write_text(
        '{"technology":"react","version":"19.0"}',
        encoding="utf-8",
    )
    (docs_root / "technologies/react/docs/hooks.md").write_text(
        "# Hooks\n\nHooks in v19.",
        encoding="utf-8",
    )
    legacy_doc = docs_root / "technologies/react/docs/legacy-hooks.md"
    legacy_doc.write_text("# Hooks\n\nHooks in v18.", encoding="utf-8")

    service = DocsHubService(tmp_path)
    service.scan()
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO documents (
                technology, rel_path, title, version, checksum, source_path, last_scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(technology, rel_path) DO UPDATE SET
                title = excluded.title,
                version = excluded.version,
                checksum = excluded.checksum,
                source_path = excluded.source_path,
                last_scanned_at = excluded.last_scanned_at
            """,
            (
                "react",
                "docs/legacy-hooks.md",
                "Hooks",
                "18.0",
                "legacy",
                str(legacy_doc),
                "2026-03-31T00:00:00+00:00",
            ),
        )

    payload = service.search_documentation(
        query="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        workspace_id="local",
        limit=10,
    )

    assert payload["library_id"] == "react"
    assert payload["version"] == "19.0"
    assert payload["results"]
    assert all(item["version"] == "19.0" for item in payload["results"])
