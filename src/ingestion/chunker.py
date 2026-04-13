from __future__ import annotations


def chunk_markdown(text: str, target_max_words: int = 600, absolute_max_words: int = 1500) -> list[str]:
    """Split markdown into semantic chunks.

    Rules:
    - H1 (``# ``) always starts a new chunk.
    - H2 (``## ``) and H3 (``### ``) flush when the current chunk already exceeds
      half the target word count, producing finer-grained topic chunks.
    - Code fences remain glued to their nearest explanatory prose unless the
      absolute_max_words hard limit forces a flush.
    - Absolute max is enforced even inside code fences.
    """
    lines = text.splitlines()
    if not lines:
        return []

    chunks: list[str] = []
    current: list[str] = []
    fence_char: str | None = None

    def flush_current() -> None:
        nonlocal current
        body = "\n".join(current).strip()
        if body:
            chunks.append(body)
        current = []

    for line in lines:
        stripped = line.strip()

        # Track code fence state (``` or ~~~)
        if stripped.startswith("```") or stripped.startswith("~~~"):
            ch = stripped[0]
            if fence_char is None:
                fence_char = ch
            elif ch == fence_char:
                fence_char = None
            current.append(line)
            continue

        in_code_fence = fence_char is not None

        if not in_code_fence:
            if stripped.startswith("# "):
                # H1 always starts a new chunk
                if current:
                    flush_current()
                current.append(line)
                continue

            if stripped.startswith("## ") or stripped.startswith("### "):
                # H2/H3 flush only when chunk already has half the target words
                wc = _word_count(current)
                if current and wc >= target_max_words // 2:
                    flush_current()
                current.append(line)
                continue

        current.append(line)

        wc = _word_count(current)
        if not in_code_fence and wc >= target_max_words:
            flush_current()
        elif wc >= absolute_max_words:
            flush_current()
            fence_char = None  # Reset fence state on forced flush

    flush_current()
    return chunks


def _word_count(lines: list[str]) -> int:
    return sum(len(line.split()) for line in lines)
