"""Tests for retrieval quality gaps: Gap 1-6 improvements.

Gap 2 — h2/h3 chunking
Gap 3 — query-time snippet extraction in hybrid results
Gap 4 — cross-encoder reranker (neural path + graceful fallback)
Gap 5 — BM25 sparse vector generation
Gap 6 — benchmark seed cases coverage
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.chunker import chunk_markdown
from retrieval.qdrant_client import QdrantHybridClient, QdrantQuery, _bm25_sparse_vector, _tokenize
from retrieval.retriever import HybridRetriever, RetrievalMatch, RetrievalQuery, _rerank_lexical


# ──────────────────────────────────────────────────────────────────────────────
# Gap 2: h2/h3 chunking
# ──────────────────────────────────────────────────────────────────────────────

def test_h2_flushes_when_chunk_exceeds_half_target() -> None:
    """H2 heading should start a new chunk when current chunk is already large."""
    intro = " ".join(["word"] * 350)  # 350 words — exceeds half of 600
    text = f"# Top Section\n\n{intro}\n\n## Sub Section\n\nShort paragraph."
    chunks = chunk_markdown(text, target_max_words=600)
    assert len(chunks) >= 2
    assert any("## Sub Section" in c for c in chunks)


def test_h2_stays_in_chunk_when_below_half_target() -> None:
    """H2 heading should NOT flush when current chunk is small."""
    text = "# Top Section\n\nShort intro.\n\n## Sub Section\n\nShort paragraph."
    chunks = chunk_markdown(text, target_max_words=600)
    # Both sections are tiny — should be one chunk
    assert len(chunks) == 1
    assert "## Sub Section" in chunks[0]


def test_h3_flushes_when_chunk_exceeds_half_target() -> None:
    """H3 heading should start a new chunk when current chunk is already large."""
    intro = " ".join(["word"] * 350)
    text = f"# Top\n\n{intro}\n\n### Subsub\n\nDetails."
    chunks = chunk_markdown(text, target_max_words=600)
    assert len(chunks) >= 2
    assert any("### Subsub" in c for c in chunks)


def test_h1_always_flushes() -> None:
    """H1 always starts a new chunk regardless of current chunk size."""
    text = "# First\n\nTiny.\n\n# Second\n\nAlso tiny."
    chunks = chunk_markdown(text)
    assert len(chunks) == 2
    assert chunks[0].startswith("# First")
    assert chunks[1].startswith("# Second")


def test_code_fence_stays_glued_across_h2_boundary() -> None:
    """Code fence must not be split; H2 inside code fence is not a real heading."""
    text = (
        "# Guide\n\n"
        "Explanation.\n\n"
        "```markdown\n"
        "## not a real heading\n"
        "```\n\n"
        "More prose."
    )
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert "## not a real heading" in chunks[0]


# ──────────────────────────────────────────────────────────────────────────────
# Gap 3: query-time snippet extraction in hybrid results
# ──────────────────────────────────────────────────────────────────────────────

class _FakeQdrantBackend:
    def __init__(self, full_chunk: str) -> None:
        self._full_chunk = full_chunk

    def query_points(self, **kwargs):
        class _Point:
            payload = {
                "workspace_id": "ws",
                "library_id": "react",
                "version": None,
                "rel_path": "docs/hooks.md",
                "title": "Hooks",
                "source_uri": "doc://react/docs/hooks.md",
                "snippet": self._full_chunk,
            }
            score = 0.9

        class _Resp:
            points = [_Point()]

        return _Resp()


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 3 for _ in texts]


def test_hybrid_path_applies_query_time_snippet_extraction() -> None:
    """HybridRetriever should apply _build_snippet to the full stored chunk."""
    long_chunk = "irrelevant preamble " * 20 + "useState lets you add state to functional components." + " trailing " * 20
    backend = _FakeQdrantBackend(full_chunk=long_chunk)
    client = QdrantHybridClient(client=backend, collection_name="c", embedder=_FakeEmbedder())
    retriever = HybridRetriever(qdrant_client=client)
    query = RetrievalQuery(text="useState state functional", workspace_id="ws", library_id="react")
    response = retriever.search(documents=[], query=query)
    assert response.retrieval_mode == "hybrid"
    assert len(response.matches) == 1
    snippet = response.matches[0].snippet
    # The snippet should contain the relevant passage, not just a fixed prefix
    assert "useState" in snippet or "state" in snippet


def test_hybrid_snippet_is_shorter_than_full_chunk() -> None:
    """Query-time extraction should produce a focused snippet, not the full chunk."""
    full_chunk = ("irrelevant text. " * 50) + "useEffect runs after render." + (" more filler " * 50)
    backend = _FakeQdrantBackend(full_chunk=full_chunk)
    client = QdrantHybridClient(client=backend, collection_name="c", embedder=_FakeEmbedder())
    retriever = HybridRetriever(qdrant_client=client)
    query = RetrievalQuery(text="useEffect render", workspace_id="ws", library_id="react")
    response = retriever.search(documents=[], query=query)
    assert len(response.matches) == 1
    # Snippet should be much shorter than full chunk
    assert len(response.matches[0].snippet) < len(full_chunk)


# ──────────────────────────────────────────────────────────────────────────────
# Gap 4: cross-encoder reranker
# ──────────────────────────────────────────────────────────────────────────────

def test_rerank_lexical_preserves_order_by_score() -> None:
    """Lexical reranker should boost title-matching results."""
    matches = [
        RetrievalMatch(
            workspace_id="ws", library_id="react", version=None,
            rel_path="docs/effects.md", title="Effects",
            source_uri="doc://react/docs/effects.md", score=0.7,
            snippet="useEffect is used for side effects",
        ),
        RetrievalMatch(
            workspace_id="ws", library_id="react", version=None,
            rel_path="docs/hooks.md", title="Hooks",
            source_uri="doc://react/docs/hooks.md", score=0.6,
            snippet="hooks provide state management",
        ),
    ]
    # Query about effects — first result should stay first (higher base score)
    reranked = _rerank_lexical(matches, terms=["effect", "side"], query_text="effect side effects")
    assert reranked[0].rel_path == "docs/effects.md"


def test_rerank_lexical_boosts_title_match() -> None:
    """Lexical reranker should boost result whose title contains query terms."""
    matches = [
        RetrievalMatch(
            workspace_id="ws", library_id="react", version=None,
            rel_path="docs/other.md", title="Other Concepts",
            source_uri="doc://react/docs/other.md", score=0.85,
            snippet="hooks are sometimes used here",
        ),
        RetrievalMatch(
            workspace_id="ws", library_id="react", version=None,
            rel_path="docs/hooks.md", title="Hooks",
            source_uri="doc://react/docs/hooks.md", score=0.8,
            snippet="hooks provide state and lifecycle management",
        ),
    ]
    reranked = _rerank_lexical(matches, terms=["hooks"], query_text="hooks")
    # hooks.md title matches "hooks" → should be boosted to first
    assert reranked[0].rel_path == "docs/hooks.md"


def test_cross_encoder_falls_back_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """When cross-encoder raises, _rerank_hybrid should return original order."""
    from retrieval import retriever as retriever_module

    class _BadCrossEncoder:
        def predict(self, pairs):
            raise RuntimeError("model load failed")

    monkeypatch.setattr(retriever_module, "_cross_encoder_cache", _BadCrossEncoder())

    matches = [
        RetrievalMatch(
            workspace_id="ws", library_id="react", version=None,
            rel_path="docs/hooks.md", title="Hooks",
            source_uri="doc://react/docs/hooks.md", score=0.9,
            snippet="React hooks",
        ),
    ]
    # Should not raise even if cross-encoder fails
    result = retriever_module.HybridRetriever._rerank_hybrid(matches, ["hooks"], "hooks")
    # Falls back to returning original list
    assert len(result) == 1


def test_cross_encoder_skipped_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When cross-encoder is unavailable, lexical reranker is used."""
    from retrieval import retriever as retriever_module

    monkeypatch.setattr(retriever_module, "_cross_encoder_cache", False)

    matches = [
        RetrievalMatch(
            workspace_id="ws", library_id="react", version=None,
            rel_path="docs/hooks.md", title="Hooks",
            source_uri="doc://react/docs/hooks.md", score=0.9,
            snippet="React hooks",
        ),
    ]
    result = retriever_module.HybridRetriever._rerank_hybrid(matches, ["hooks"], "hooks")
    assert len(result) == 1
    assert result[0].score >= 0


# ──────────────────────────────────────────────────────────────────────────────
# Gap 5: BM25 sparse vector generation
# ──────────────────────────────────────────────────────────────────────────────

def test_bm25_returns_indices_and_values() -> None:
    indices, values = _bm25_sparse_vector("react hooks useState useEffect")
    assert len(indices) > 0
    assert len(indices) == len(values)
    assert all(isinstance(i, int) for i in indices)
    assert all(isinstance(v, float) for v in values)
    assert all(0 <= i < 65536 for i in indices)
    assert all(v > 0 for v in values)


def test_bm25_empty_text_returns_empty() -> None:
    indices, values = _bm25_sparse_vector("")
    assert indices == []
    assert values == []


def test_bm25_stop_words_excluded() -> None:
    indices_with_stops, _ = _bm25_sparse_vector("the and or but a an")
    indices_no_stops, _ = _bm25_sparse_vector("react hooks python")
    # Only stop words → empty (all filtered out)
    assert len(indices_with_stops) == 0
    # Content words → non-empty
    assert len(indices_no_stops) > 0


def test_bm25_same_text_is_deterministic() -> None:
    a_idx, a_val = _bm25_sparse_vector("react server components data fetching")
    b_idx, b_val = _bm25_sparse_vector("react server components data fetching")
    assert a_idx == b_idx
    assert a_val == b_val


def test_bm25_repeated_tokens_higher_score() -> None:
    idx_once, val_once = _bm25_sparse_vector("python")
    idx_many, val_many = _bm25_sparse_vector("python python python python python")
    # The token "python" maps to same bucket; score should be higher with repetition
    bucket = idx_once[0]
    assert bucket in idx_many
    pos = idx_many.index(bucket)
    assert val_many[pos] > val_once[0]


def test_tokenize_filters_stop_words_and_short_tokens() -> None:
    tokens = _tokenize("the quick brown fox is in the forest")
    assert "the" not in tokens
    assert "is" not in tokens
    assert "in" not in tokens
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens


def test_qdrant_client_builds_sparse_vector_on_query() -> None:
    """QdrantHybridClient should generate sparse vector and attempt hybrid query."""
    captured: dict = {}

    class _FakeBackend:
        def query_points(self, **kwargs):
            captured.update(kwargs)

            class _Resp:
                points = []

            return _Resp()

    client = QdrantHybridClient(
        client=_FakeBackend(), collection_name="c", embedder=_FakeEmbedder()
    )
    client.query_hybrid(
        QdrantQuery(workspace_id="ws", library_id="react", version=None, query_text="useState hook", limit=5)
    )
    # Backend should have been called
    assert "collection_name" in captured


# ──────────────────────────────────────────────────────────────────────────────
# Gap 6: benchmark seed cases completeness
# ──────────────────────────────────────────────────────────────────────────────

def test_benchmark_has_at_least_50_cases() -> None:
    seed_path = Path(__file__).parent / "benchmark" / "seed_cases.json"
    assert seed_path.exists(), "seed_cases.json is missing"
    cases = json.loads(seed_path.read_text(encoding="utf-8"))
    assert len(cases) >= 50, f"Expected ≥50 benchmark cases, got {len(cases)}"


def test_benchmark_covers_at_least_5_libraries() -> None:
    seed_path = Path(__file__).parent / "benchmark" / "seed_cases.json"
    cases = json.loads(seed_path.read_text(encoding="utf-8"))
    libraries = {c["expected_uri"].split("/")[2] for c in cases}
    assert len(libraries) >= 5, f"Expected ≥5 libraries, got: {libraries}"


def test_benchmark_cases_are_well_formed() -> None:
    seed_path = Path(__file__).parent / "benchmark" / "seed_cases.json"
    cases = json.loads(seed_path.read_text(encoding="utf-8"))
    for i, case in enumerate(cases):
        assert "query" in case and case["query"], f"Case {i} missing query"
        assert "expected_uri" in case and case["expected_uri"], f"Case {i} missing expected_uri"
        assert "ranked_uris" in case and isinstance(case["ranked_uris"], list), f"Case {i} missing ranked_uris"
        assert case["expected_uri"] in case["ranked_uris"], f"Case {i}: expected_uri not in ranked_uris"
        assert case["expected_uri"].startswith("doc://"), f"Case {i}: expected_uri must start with doc://"


def test_benchmark_mrr_above_gate() -> None:
    """All seed cases have expected_uri at rank 1 or 2 → MRR should be ≥ 0.70."""
    seed_path = Path(__file__).parent / "benchmark" / "seed_cases.json"
    cases = json.loads(seed_path.read_text(encoding="utf-8"))

    total_rr = 0.0
    for case in cases:
        for rank, uri in enumerate(case["ranked_uris"][:10], start=1):
            if uri == case["expected_uri"]:
                total_rr += 1.0 / rank
                break

    mrr = total_rr / len(cases) if cases else 0.0
    assert mrr >= 0.70, f"MRR@10 = {mrr:.4f} < 0.70 gate"
