from pathlib import Path

import pytest

from buonaiuto_doc4llm.service import DocsHubService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_service(tmp_path: Path) -> DocsHubService:
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
        "# Hooks\n\nuseState and friends",
    )
    service = DocsHubService(tmp_path)
    service.scan()
    return service


def test_submit_feedback_stores_record(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    result = service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="how to use useState",
        satisfied=True,
        reason="exactly what I needed",
        requester_id="llm-agent-1",
    )
    assert result["id"] >= 1
    assert result["technology"] == "react"
    assert result["satisfied"] is True


def test_submit_feedback_requires_satisfied_and_reason(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    with pytest.raises(ValueError, match="satisfied"):
        service.submit_feedback(
            technology="react",
            rel_path="docs/hooks.md",
            query="hooks",
            satisfied=None,  # type: ignore[arg-type]
            reason="some reason",
            requester_id="agent-1",
        )
    with pytest.raises(ValueError, match="reason"):
        service.submit_feedback(
            technology="react",
            rel_path="docs/hooks.md",
            query="hooks",
            satisfied=False,
            reason="",
            requester_id="agent-1",
        )


def test_list_feedback_returns_all_entries(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="useState",
        satisfied=True,
        reason="great",
        requester_id="a1",
    )
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="useEffect",
        satisfied=False,
        reason="missing cleanup example",
        requester_id="a2",
    )

    entries = service.list_feedback()
    assert len(entries) == 2


def test_list_feedback_filters_by_technology(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="useState",
        satisfied=True,
        reason="good",
        requester_id="a1",
    )
    entries = service.list_feedback(technology="react")
    assert len(entries) == 1
    entries_empty = service.list_feedback(technology="nextjs")
    assert len(entries_empty) == 0


def test_feedback_stats_returns_aggregates(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="q1",
        satisfied=True,
        reason="good",
        requester_id="a1",
    )
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="q2",
        satisfied=True,
        reason="fine",
        requester_id="a2",
    )
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="q3",
        satisfied=False,
        reason="bad",
        requester_id="a3",
    )

    stats = service.feedback_stats()
    assert stats["total"] == 3
    assert stats["satisfied"] == 2
    assert stats["unsatisfied"] == 1
    assert stats["satisfaction_rate"] == pytest.approx(2 / 3, abs=0.01)


def test_feedback_stats_per_technology(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="q1",
        satisfied=True,
        reason="good",
        requester_id="a1",
    )
    service.submit_feedback(
        technology="react",
        rel_path="docs/hooks.md",
        query="q2",
        satisfied=False,
        reason="bad",
        requester_id="a2",
    )

    stats = service.feedback_stats(technology="react")
    assert stats["total"] == 2
    assert stats["satisfied"] == 1
    assert stats["satisfaction_rate"] == pytest.approx(0.5, abs=0.01)


def test_feedback_stats_empty(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    stats = service.feedback_stats()
    assert stats["total"] == 0
    assert stats["satisfied"] == 0
    assert stats["unsatisfied"] == 0
    assert stats["satisfaction_rate"] == 0.0


def test_feedback_stats_per_document(tmp_path: Path) -> None:
    service = _setup_service(tmp_path)
    for i in range(3):
        service.submit_feedback(
            technology="react",
            rel_path="docs/hooks.md",
            query=f"q{i}",
            satisfied=i < 2,
            reason="r",
            requester_id=f"a{i}",
        )

    stats = service.feedback_stats()
    assert "by_document" in stats
    doc_stats = stats["by_document"]
    assert len(doc_stats) >= 1
    hooks_stat = next(d for d in doc_stats if d["rel_path"] == "docs/hooks.md")
    assert hooks_stat["total"] == 3
    assert hooks_stat["satisfied"] == 2
