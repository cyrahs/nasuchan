from __future__ import annotations

_DEFAULT_TEXT_LIMIT = 4000


def split_text_chunks(text: str, *, limit: int = _DEFAULT_TEXT_LIMIT) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return ['']
    if len(normalized) <= limit:
        return [normalized]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in normalized.splitlines():
        candidate_length = current_length + len(line) + (1 if current else 0)
        if current and candidate_length > limit:
            chunks.append('\n'.join(current))
            current = [line]
            current_length = len(line)
            continue
        if len(line) > limit:
            if current:
                chunks.append('\n'.join(current))
                current = []
                current_length = 0
            for start in range(0, len(line), limit):
                chunks.append(line[start : start + limit])
            continue
        current.append(line)
        current_length = candidate_length

    if current:
        chunks.append('\n'.join(current))
    return chunks
