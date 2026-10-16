from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize(t: str) -> str:
    return re.sub(r"\s+", " ", t.lower()).strip()


def similarity(a: str, b: str) -> float:
    """0..1. 'Unchanged' means >= SAME_THRESHOLD: whitespace and case never count as an edit, a changed fact does."""
    return SequenceMatcher(None, normalize(a), normalize(b), autojunk=False).ratio()
