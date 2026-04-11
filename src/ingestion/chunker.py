from __future__ import annotations


def chunk_markdown(text: str, target_max_words: int = 600, absolute_max_words: int = 1500) -> list[str]:
    """Split markdown into semantic chunks.

    Rules:
    - Heading and direct prose stay together.
    - Code fences remain in the same chunk as nearby explanatory prose.
    - New top-level heading starts a new chunk.
    - Absolute max enforced even inside code fences.
    """
    lines = text.splitlines()
    if not lines:
        return []

    chunks: list[str] = []
    current: list[str] = []
    # Track the opening fence character ("`" or "~") so only a matching
    # closing fence ends the block.  A simple bool toggle would flip on
    # unrelated fence lines when the opening/closing chars differ or when
    # a document contains an odd number of fence markers (malformed input).
    fence_char: str | None = None

    def flush_current() -> None:
        nonlocal current
        body = "\n".join(current).strip()
        if body:
            chunks.append(body)
        current = []

    for line in lines:
        stripped = line.strip()
        # Support both ``` and ~~~ code fences; match only the opening char
        if stripped.startswith("```") or stripped.startswith("~~~"):
            ch = stripped[0]
            if fence_char is None:
                # Opening fence
                fence_char = ch
            elif ch == fence_char:
                # Matching closing fence
                fence_char = None
            # else: different fence char inside a fence — treat as content
            current.append(line)
            continue

        in_code_fence = fence_char is not None

        if not in_code_fence and stripped.startswith("# "):
            if current:
                flush_current()
            current.append(line)
            continue

        current.append(line)

        wc = _word_count(current)
        if not in_code_fence and wc >= target_max_words:
            flush_current()
        elif wc >= absolute_max_words:
            # Force flush even inside code fences to prevent unbounded chunks
            flush_current()
            fence_char = None  # Reset fence state to avoid orphaned fence

    flush_current()
    return chunks


def _word_count(lines: list[str]) -> int:
    return sum(len(line.split()) for line in lines)
