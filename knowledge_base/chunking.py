"""Deterministic, dependency-free text chunking for local knowledge."""

from __future__ import annotations

import re


def chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    """Split text near natural boundaries with approximate character overlap."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be greater than zero")
    if overlap_chars < 0 or overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars must be between zero and chunk_chars")

    normalized = _normalize_text(text)
    if not normalized:
        return []

    chunks = []
    start = 0
    previous_end = -1
    text_length = len(normalized)

    while start < text_length:
        target_end = min(start + chunk_chars, text_length)
        end = _choose_end(normalized, start, target_end, chunk_chars)
        if end <= previous_end:
            start = previous_end
            while start < text_length and normalized[start].isspace():
                start += 1
            continue
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break

        previous_end = end
        next_start = _overlap_start(normalized, start, end, overlap_chars)
        start = next_start if next_start > start else end

    return chunks


def _normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _choose_end(text: str, start: int, target_end: int, chunk_chars: int) -> int:
    if target_end >= len(text):
        return len(text)

    minimum = start + max(1, chunk_chars // 2)
    search = text[minimum:target_end]

    paragraph = search.rfind("\n\n")
    if paragraph >= 0:
        return minimum + paragraph

    sentence_ends = list(re.finditer(r"[.!?](?=\s)", search))
    if sentence_ends:
        return minimum + sentence_ends[-1].end()

    newline = search.rfind("\n")
    if newline >= 0:
        return minimum + newline

    whitespace = max(text.rfind(" ", start, target_end), text.rfind("\n", start, target_end))
    if whitespace > start:
        return whitespace

    next_whitespace = re.search(r"\s", text[target_end:])
    if next_whitespace:
        return target_end + next_whitespace.start()
    return len(text)


def _overlap_start(text: str, start: int, end: int, overlap_chars: int) -> int:
    if overlap_chars == 0:
        next_start = end
    else:
        desired = max(start + 1, end - overlap_chars)
        previous_space = max(
            text.rfind(" ", start, desired + 1),
            text.rfind("\n", start, desired + 1),
        )
        if previous_space >= start:
            next_start = previous_space + 1
        else:
            following_space = re.search(r"\s", text[desired:end])
            next_start = (
                desired + following_space.end()
                if following_space
                else end
            )

    while next_start < end and text[next_start].isspace():
        next_start += 1
    return next_start
