from ingestion.chunker import chunk_markdown


def test_chunker_keeps_heading_with_direct_prose() -> None:
    text = """# Getting Started

Install the package and initialize the client.

Then configure authentication.
"""
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert chunks[0].startswith("# Getting Started")
    assert "Install the package" in chunks[0]


def test_chunker_keeps_code_block_with_explanatory_text() -> None:
    text = """# API Usage

Use the following snippet:

```python
client = Client()
client.run()
```

This call starts the worker process.
"""
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert "```python" in chunks[0]
    assert "This call starts the worker process." in chunks[0]


def test_chunker_splits_on_new_heading_boundaries() -> None:
    text = """# Intro
Overview text.

# Advanced
Deep technical details.
"""
    chunks = chunk_markdown(text)
    assert len(chunks) == 2
    assert chunks[0].startswith("# Intro")
    assert chunks[1].startswith("# Advanced")
