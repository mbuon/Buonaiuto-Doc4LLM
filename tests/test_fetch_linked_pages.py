"""Tests for llms.txt link extraction and linked page fetching."""
from __future__ import annotations

from ingestion.http_fetcher import _extract_doc_links, _url_to_rel_path


SAMPLE_LLMS_TXT = """\
# React Documentation

> The library for web and native user interfaces.

## Learn React

### GET STARTED

#### Quick Start
- [Quick Start](https://react.dev/learn.md)
- [Tutorial: Tic-Tac-Toe](https://react.dev/learn/tutorial-tic-tac-toe.md)
- [Thinking in React](https://react.dev/learn/thinking-in-react.md)

#### Installation
- [Installation](https://react.dev/learn/installation.md)
- [Creating a React App](https://react.dev/learn/creating-a-react-app.md)

## API Reference

### Hooks
- [useState](https://react.dev/reference/react/useState.md)
- [useEffect](https://react.dev/reference/react/useEffect.md)

### External link (should be filtered)
- [GitHub](https://github.com/facebook/react)
- [npm](https://npmjs.com/package/react)
"""


def test_extract_doc_links_finds_md_urls() -> None:
    links = _extract_doc_links(SAMPLE_LLMS_TXT, "https://react.dev/llms.txt")
    assert len(links) == 7
    assert "https://react.dev/learn.md" in links
    assert "https://react.dev/reference/react/useState.md" in links


def test_extract_doc_links_filters_external_domains() -> None:
    links = _extract_doc_links(SAMPLE_LLMS_TXT, "https://react.dev/llms.txt")
    assert not any("github.com" in u for u in links)
    assert not any("npmjs.com" in u for u in links)


def test_extract_doc_links_deduplicates() -> None:
    content = """\
- [Intro](https://example.com/intro.md)
- [Intro again](https://example.com/intro.md)
- [Guide](https://example.com/guide.md)
"""
    links = _extract_doc_links(content, "https://example.com/llms.txt")
    assert len(links) == 2


def test_extract_doc_links_ignores_non_doc_extensions() -> None:
    content = """\
- [Docs](https://example.com/docs.md)
- [Image](https://example.com/logo.png)
- [Page](https://example.com/about)
- [JSON](https://example.com/schema.json)
"""
    links = _extract_doc_links(content, "https://example.com/llms.txt")
    assert links == ["https://example.com/docs.md"]


def test_extract_doc_links_empty_content() -> None:
    links = _extract_doc_links("", "https://example.com/llms.txt")
    assert links == []


def test_extract_doc_links_no_links() -> None:
    links = _extract_doc_links("# Just a title\n\nSome text.", "https://example.com/llms.txt")
    assert links == []


def test_extract_doc_links_allows_subdomain() -> None:
    content = "- [API](https://docs.stripe.com/api.md)"
    links = _extract_doc_links(content, "https://docs.stripe.com/llms.txt")
    assert len(links) == 1


# ------------------------------------------------------------------
# URL → rel_path conversion
# ------------------------------------------------------------------

def test_url_to_rel_path_basic() -> None:
    assert _url_to_rel_path("https://react.dev/learn/hooks.md") == "learn/hooks.md"


def test_url_to_rel_path_nested() -> None:
    assert _url_to_rel_path("https://react.dev/reference/react/useState.md") == "reference/react/useState.md"


def test_url_to_rel_path_root() -> None:
    assert _url_to_rel_path("https://react.dev/learn.md") == "learn.md"


def test_url_to_rel_path_rejects_traversal() -> None:
    assert _url_to_rel_path("https://evil.com/../etc/passwd") is None


def test_url_to_rel_path_empty_path() -> None:
    assert _url_to_rel_path("https://example.com/") is None
