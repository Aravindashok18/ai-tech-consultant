"""Long transcripts: split on speaker turns into overlapping windows so no requirement is cut in half."""
from __future__ import annotations


def chunk_transcript(text: str, max_chars: int = 6000, overlap_turns: int = 2) -> list[str]:
    turns = [t.strip() for t in text.split("\n") if t.strip()]
    chunks: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for t in turns:
        while len(t) > max_chars:                    # one giant monologue: hard split
            if cur:
                chunks.append(cur)
                cur, size = [], 0
            chunks.append([t[:max_chars]])
            t = t[max_chars:]
        if size + len(t) > max_chars and cur:
            chunks.append(cur)
            cur = cur[-overlap_turns:] if overlap_turns else []
            size = sum(len(x) for x in cur)
        cur.append(t)
        size += len(t)
    if cur:
        chunks.append(cur)
    return ["\n".join(c) for c in chunks] or [""]
