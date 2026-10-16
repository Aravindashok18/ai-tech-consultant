"""Turn a human edit into reusable lessons (rules). Rules are individually revertible."""
from __future__ import annotations

import difflib
from datetime import UTC, datetime

from .config import KINDS, Config
from .llm import LLM, load_prompt

SCHEMA = {
    "type": "object",
    "properties": {"lessons": {"type": "array", "maxItems": 2, "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "scope": {"type": "string", "enum": ["all", *KINDS]}},
        "required": ["text", "scope"]}}},
    "required": ["lessons"],
}


def diff_summary(draft: str, final: str) -> str:
    return "\n".join(l for l in difflib.unified_diff(draft.splitlines(), final.splitlines(), "draft", "human", lineterm="", n=0)
                     if not l.startswith(("---", "+++")))[:3000]


def heuristic_lessons(draft: str, final: str) -> list[dict]:
    """No-key fallback: only learn things we can see without guessing."""
    out = []
    dw, fw = len(draft.split()), len(final.split())
    if fw < dw * 0.7:
        out.append({"text": "Keep replies shorter: the team cuts about a third of the length.", "scope": "all"})
    if "!" in draft and "!" not in final:
        out.append({"text": "Avoid exclamation marks.", "scope": "all"})
    return out


async def learn_from_edit(llm: LLM, cfg: Config, db, action_id: int, kind: str, subject: str,
                          draft: str, final: str) -> list[int]:
    if llm.available:
        system, ver = load_prompt("lesson")
        r = await llm.structured(purpose="lesson", model=cfg.fast_model, system=system, prompt_version=ver,
                                 user=f"Email kind: {kind}\nSubject: {subject}\n\nDIFF (draft -> human):\n{diff_summary(draft, final)}\n\nFINAL REPLY:\n{final[:2000]}",
                                 schema=SCHEMA, max_tokens=300)
        lessons = r.data["lessons"]
    else:
        lessons = heuristic_lessons(draft, final)
    ids = []
    for l in lessons:
        text = l["text"].strip()
        if not text or db.execute("SELECT 1 FROM rules WHERE active=1 AND lower(text)=lower(?)", (text,)).fetchone():
            continue
        cur = db.execute("INSERT INTO rules(text,kind,source_action,created_at) VALUES (?,?,?,?)",
                         (text, None if l["scope"] == "all" else l["scope"], action_id, datetime.now(UTC).isoformat()))
        ids.append(int(cur.lastrowid))
    db.commit()
    return ids
