from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

from retrieval.qdrant_client import QdrantQuery

# ---------------------------------------------------------------------------
# Optional cross-encoder reranker (sentence-transformers)
# ---------------------------------------------------------------------------

_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_cross_encoder_cache: Any = None


def _get_cross_encoder() -> Any:
    """Return a cached CrossEncoder instance, or None if unavailable."""
    global _cross_encoder_cache
    if _cross_encoder_cache is not None:
        return _cross_encoder_cache
    try:
        from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
        _cross_encoder_cache = CrossEncoder(_CROSS_ENCODER_MODEL)
    except Exception:
        _cross_encoder_cache = False  # Mark unavailable, avoid repeated attempts
    return _cross_encoder_cache if _cross_encoder_cache else None


@dataclass(frozen=True)
class RetrievalDocument:
    workspace_id: str | None
    library_id: str
    version: str | None
    rel_path: str
    title: str
    content: str
    source_uri: str


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    workspace_id: str | None = None
    library_id: str | None = None
    version: str | None = None
    limit: int = 5


@dataclass(frozen=True)
class RetrievalMatch:
    workspace_id: str | None
    library_id: str
    version: str | None
    rel_path: str
    title: str
    source_uri: str
    score: float
    snippet: str


@dataclass(frozen=True)
class RetrievalResponse:
    retrieval_mode: str
    matches: list[RetrievalMatch]


class HybridRetriever:
    """Hybrid retrieval contract with lexical-only cold-start behavior."""

    def __init__(self, qdrant_client: Any | None = None):
        self.qdrant_client = qdrant_client

    _STOP_WORDS = frozenset({
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "it", "as", "be", "was", "are",
        "this", "that", "how", "what", "when", "where", "who", "which",
        "do", "does", "did", "not", "no", "can", "will", "should", "would",
        "could", "may", "i", "you", "we", "they", "my", "your", "use",
        "using", "used", "about", "into", "up", "out", "if", "then",
    })

    def search(self, documents: Sequence[RetrievalDocument], query: RetrievalQuery) -> RetrievalResponse:
        query_text = query.text.strip().lower()
        if not query_text:
            return RetrievalResponse(retrieval_mode="lexical_only", matches=[])

        hybrid_matches = self._search_hybrid(query)
        if hybrid_matches:
            terms = [t for t in query_text.split() if t not in self._STOP_WORDS and len(t) > 1]
            if not terms:
                terms = query_text.split()
            reranked = self._rerank_hybrid(hybrid_matches, terms, query_text)
            return RetrievalResponse(retrieval_mode="hybrid", matches=reranked[: query.limit])

        terms = [t for t in query_text.split() if t not in self._STOP_WORDS and len(t) > 1]
        if not terms:
            terms = query_text.split()

        n_terms = len(terms)
        scored: list[tuple[float, RetrievalDocument]] = []

        for doc in documents:
            if query.workspace_id is not None and doc.workspace_id != query.workspace_id:
                continue
            if query.library_id is not None and doc.library_id != query.library_id:
                continue
            if query.version is not None and doc.version != query.version:
                continue

            score = self._score_document(doc, terms, n_terms, query_text)
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda item: item[0], reverse=True)
        limited = scored[: query.limit]

        matches = [
            RetrievalMatch(
                workspace_id=doc.workspace_id,
                library_id=doc.library_id,
                version=doc.version,
                rel_path=doc.rel_path,
                title=doc.title,
                source_uri=doc.source_uri,
                score=round(score, 1),
                snippet=self._build_snippet(doc.content, terms, query_text),
            )
            for score, doc in limited
        ]
        return RetrievalResponse(retrieval_mode="lexical_only", matches=matches)

    @staticmethod
    def _score_document(
        doc: RetrievalDocument,
        terms: list[str],
        n_terms: int,
        query_text: str,
    ) -> float:
        content_lc = doc.content.lower()
        title_lc = doc.title.lower()
        path_lc = doc.rel_path.lower()

        matched_terms = 0
        raw_score = 0.0

        for term in terms:
            c_hits = content_lc.count(term)
            t_hits = title_lc.count(term)
            p_hits = path_lc.count(term)
            if c_hits + t_hits + p_hits > 0:
                matched_terms += 1
                raw_score += min(c_hits, 50) + (t_hits * 10) + (p_hits * 5)

        if matched_terms == 0:
            return 0.0

        coverage = matched_terms / n_terms

        # Require at least half the terms to match — eliminates noise
        if n_terms >= 3 and coverage < 0.5:
            return 0.0

        # Coverage is the primary multiplier — matching all terms matters most
        score = raw_score * (coverage ** 2)

        # Bonus for exact multi-word phrase in content
        if n_terms >= 2:
            phrase = " ".join(terms)
            phrase_hits = content_lc.count(phrase)
            if phrase_hits > 0:
                score += min(phrase_hits, 20) * 50
            # Bonus for adjacent pairs (e.g. "edge functions" within query)
            for i in range(len(terms) - 1):
                pair = terms[i] + " " + terms[i + 1]
                pair_hits = content_lc.count(pair)
                if pair_hits > 0:
                    score += min(pair_hits, 20) * 20

        # Title match bonus
        if all(t in title_lc for t in terms):
            score *= 2.0

        # Deprioritize changelogs and release notes — high term frequency but low relevance
        path_lower = doc.rel_path.lower()
        if any(p in path_lower for p in ("changelog", "release-notes", "releases", "history", "migration")):
            score *= 0.15

        # Size normalization: penalize very large documents so they don't
        # dominate just by having more text.  Uses sqrt-based normalization
        # so that a 1MB doc can't beat a focused 10KB doc just by repetition.
        doc_len = len(doc.content)
        if doc_len > 10_000:
            # sqrt normalization: sqrt(10k) = 100; sqrt(1M) = 1000 → divisor 10x
            score /= math.sqrt(doc_len / 10_000)

        return score

    @staticmethod
    def _build_snippet(content: str, terms: list[str], query_text: str, radius: int = 250) -> str:
        """Extract the best snippet showing terms in context.

        Strategy: find the passage where the most query terms appear
        closest together.
        """
        lower = content.lower()

        # Try to find the multi-word phrase first
        if len(terms) >= 2:
            phrase = " ".join(terms)
            idx = lower.find(phrase)
            if idx >= 0:
                return _extract_around(content, idx, len(phrase), radius)

        # Try adjacent term pairs
        for i in range(len(terms) - 1):
            pair = terms[i] + " " + terms[i + 1]
            idx = lower.find(pair)
            if idx >= 0:
                return _extract_around(content, idx, len(pair), radius)

        # Find the window where the most terms co-occur within a small span
        if len(terms) >= 2:
            best_pos = -1
            best_count = 0
            window = 500
            for i in range(0, len(lower) - window, 200):
                chunk = lower[i:i + window]
                count = sum(1 for t in terms if t in chunk)
                if count > best_count:
                    best_count = count
                    best_pos = i
            if best_pos >= 0 and best_count >= 2:
                # Find first term occurrence within this window
                for t in terms:
                    idx = lower.find(t, best_pos, best_pos + window)
                    if idx >= 0:
                        return _extract_around(content, idx, len(t), radius)

        # Fallback: first occurrence of any term
        for t in terms:
            idx = lower.find(t)
            if idx >= 0:
                return _extract_around(content, idx, len(t), radius)

        return content[:radius * 2].strip()

    def _search_hybrid(self, query: RetrievalQuery) -> list[RetrievalMatch]:
        if self.qdrant_client is None:
            return []
        if query.workspace_id is None:
            return []

        try:
            rows = self.qdrant_client.query_hybrid(
                QdrantQuery(
                    workspace_id=query.workspace_id,
                    library_id=query.library_id or "",
                    version=query.version,
                    query_text=query.text,
                    limit=query.limit,
                )
            )
        except NotImplementedError:
            return []
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Hybrid search failed: %s", exc)
            return []

        terms = [t for t in query.text.strip().lower().split() if t not in self._STOP_WORDS and len(t) > 1]
        if not terms:
            terms = query.text.strip().lower().split()

        matches: list[RetrievalMatch] = []
        for row in rows:
            rel_path = row.get("rel_path")
            title = row.get("title")
            source_uri = row.get("source_uri")
            if not rel_path or not title or not source_uri:
                continue
            if query.workspace_id is not None and row.get("workspace_id") != query.workspace_id:
                continue
            if query.library_id is not None and row.get("library_id") != query.library_id:
                continue
            if query.version is not None and row.get("version") != query.version:
                continue
            # Apply query-time best-passage extraction when chunk text is stored
            raw_snippet = row.get("snippet", "")
            snippet = (
                self._build_snippet(raw_snippet, terms, query.text)
                if raw_snippet and terms
                else raw_snippet
            )
            matches.append(
                RetrievalMatch(
                    workspace_id=row.get("workspace_id"),
                    library_id=row.get("library_id", ""),
                    version=row.get("version"),
                    rel_path=rel_path,
                    title=title,
                    source_uri=source_uri,
                    score=round(float(row.get("score", 0.0)), 3),
                    snippet=snippet,
                )
            )
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches

    @staticmethod
    def _rerank_hybrid(
        matches: list[RetrievalMatch],
        terms: list[str],
        query_text: str,
    ) -> list[RetrievalMatch]:
        """Re-rank hybrid (vector) results.

        When sentence-transformers is installed the cross-encoder
        ``cross-encoder/ms-marco-MiniLM-L-6-v2`` is used for neural
        reranking.  Otherwise falls back to lexical signal boosts.
        """
        cross_encoder = _get_cross_encoder()
        if cross_encoder is not None:
            return _rerank_with_cross_encoder(matches, query_text, cross_encoder)
        return _rerank_lexical(matches, terms, query_text)


def _rerank_with_cross_encoder(
    matches: list[RetrievalMatch],
    query_text: str,
    cross_encoder: Any,
) -> list[RetrievalMatch]:
    """Re-rank using a neural cross-encoder (sentence-transformers)."""
    if not matches:
        return matches
    pairs = [(query_text, m.snippet or m.title) for m in matches]
    try:
        scores: list[float] = cross_encoder.predict(pairs).tolist()
    except Exception:
        return matches  # Neural rerank failed — return in original order

    reranked = sorted(zip(scores, matches), key=lambda x: x[0], reverse=True)
    return [
        RetrievalMatch(
            workspace_id=m.workspace_id,
            library_id=m.library_id,
            version=m.version,
            rel_path=m.rel_path,
            title=m.title,
            source_uri=m.source_uri,
            score=round(float(s), 3),
            snippet=m.snippet,
        )
        for s, m in reranked
    ]


def _rerank_lexical(
    matches: list[RetrievalMatch],
    terms: list[str],
    query_text: str,
) -> list[RetrievalMatch]:
    """Re-rank hybrid results using lexical signals when cross-encoder is unavailable."""
    reranked: list[tuple[float, RetrievalMatch]] = []

    for m in matches:
        score = m.score
        title_lc = m.title.lower()
        path_lc = m.rel_path.lower()
        snippet_lc = m.snippet.lower()

        title_coverage = sum(1 for t in terms if t in title_lc) / len(terms) if terms else 0
        path_coverage = sum(1 for t in terms if t in path_lc) / len(terms) if terms else 0

        pair_bonus = 0.0
        for i in range(len(terms) - 1):
            pair = terms[i] + " " + terms[i + 1]
            if pair in snippet_lc:
                pair_bonus += 0.1

        boost = (title_coverage * 0.3) + (path_coverage * 0.15) + pair_bonus
        final_score = score + boost

        reranked.append((
            final_score,
            RetrievalMatch(
                workspace_id=m.workspace_id,
                library_id=m.library_id,
                version=m.version,
                rel_path=m.rel_path,
                title=m.title,
                source_uri=m.source_uri,
                score=round(final_score, 3),
                snippet=m.snippet,
            ),
        ))

    reranked.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in reranked]


def _extract_around(content: str, idx: int, match_len: int, radius: int) -> str:
    """Extract text around a match position, expanding to line boundaries."""
    start = max(0, idx - radius)
    end = min(len(content), idx + match_len + radius)
    # Expand to nearest newline boundaries for cleaner snippets
    nl_before = content.rfind("\n", start, idx)
    if nl_before >= start:
        start = nl_before + 1
    nl_after = content.find("\n", idx + match_len, end + 50)
    if nl_after > 0:
        end = nl_after
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet
