from retrieval.retriever import HybridRetriever, RetrievalDocument, RetrievalQuery


class _UnsafeBackend:
    def query_hybrid(self, query):
        return [
            {
                "workspace_id": "ws-b",
                "library_id": "react",
                "version": "19.0",
                "rel_path": "docs/leak.md",
                "title": "Leak",
                "source_uri": "doc://react/docs/leak.md",
                "score": 0.95,
                "snippet": "wrong workspace",
            },
            {
                "workspace_id": "ws-a",
                "library_id": "other-lib",
                "version": "19.0",
                "rel_path": "docs/wrong-lib.md",
                "title": "Wrong lib",
                "source_uri": "doc://other-lib/docs/wrong-lib.md",
                "score": 0.94,
                "snippet": "wrong library",
            },
            {
                "workspace_id": "ws-a",
                "library_id": "react",
                "version": "19.0",
                "rel_path": "docs/hooks.md",
                "title": "Hooks",
                "source_uri": "doc://react/docs/hooks.md",
                "score": 0.93,
                "snippet": "correct",
            },
        ]


def test_hybrid_retriever_enforces_workspace_and_library_filters_on_backend_rows() -> None:
    retriever = HybridRetriever(qdrant_client=_UnsafeBackend())
    docs = [
        RetrievalDocument(
            workspace_id="ws-a",
            library_id="react",
            version="19.0",
            rel_path="docs/hooks.md",
            title="Hooks",
            content="Hooks content.",
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

    result = retriever.search(docs, query)

    assert result.retrieval_mode == "hybrid"
    assert len(result.matches) == 1
    assert result.matches[0].workspace_id == "ws-a"
    assert result.matches[0].library_id == "react"
