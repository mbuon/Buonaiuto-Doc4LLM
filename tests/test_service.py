from pathlib import Path

from buonaiuto_doc4llm.service import DocsHubService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_detects_added_and_updated_docs(tmp_path: Path) -> None:
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    doc_path = tmp_path / "docs_center/technologies/react/docs/guide.md"
    write(doc_path, "# Guide\n\nfirst version")

    service = DocsHubService(tmp_path)

    first_scan = service.scan()
    assert first_scan["technologies"][0]["events_created"] == 1

    unread = service.list_project_updates("app")
    assert unread["unseen_count"] == 1
    assert unread["events"][0]["event_type"] == "added"

    write(doc_path, "# Guide\n\nsecond version with update")
    second_scan = service.scan()
    assert second_scan["technologies"][0]["events_created"] == 1

    unread_again = service.list_project_updates("app")
    assert unread_again["unseen_count"] == 2
    assert unread_again["events"][0]["event_type"] == "updated"


def test_ack_hides_previous_updates(tmp_path: Path) -> None:
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["python"]}',
    )
    write(
        tmp_path / "docs_center/technologies/python/manifest.json",
        '{"technology":"python","version":"3.13"}',
    )
    write(
        tmp_path / "docs_center/technologies/python/docs/pathlib.md",
        "# pathlib\n\npaths",
    )

    service = DocsHubService(tmp_path)
    service.scan()
    unread = service.list_project_updates("app")
    assert unread["unseen_count"] == 1

    last_seen = service.ack_project_updates("app")
    assert last_seen >= unread["events"][0]["id"]
    assert service.list_project_updates("app")["unseen_count"] == 0


def test_build_update_prompt_includes_local_doc_uri(tmp_path: Path) -> None:
    write(
        tmp_path / "docs_center/projects/frontend.json",
        '{"project_id":"frontend","name":"Frontend","technologies":["vercel"]}',
    )
    write(
        tmp_path / "docs_center/technologies/vercel/manifest.json",
        '{"technology":"vercel","version":"2026-03"}',
    )
    write(
        tmp_path / "docs_center/technologies/vercel/docs/functions.md",
        "# Functions\n\nserver-side logic",
    )

    service = DocsHubService(tmp_path)
    service.scan()
    prompt = service.build_update_prompt("frontend")

    assert "doc://vercel/docs/functions.md" in prompt
    assert "frontend" in prompt
