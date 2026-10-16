"""Understand screenshots: download once, caption once (cached by image hash), tie caption to its article."""
from __future__ import annotations

import hashlib
import sqlite3

import httpx

from .config import Config
from .llm import LLM, load_prompt

SCHEMA = {
    "type": "object",
    "properties": {"decorative": {"type": "boolean"}, "caption": {"type": "string"}},
    "required": ["decorative", "caption"],
}
MEDIA = {"image/png", "image/jpeg", "image/gif", "image/webp"}


async def caption_image(cfg: Config, db: sqlite3.Connection, llm: LLM, http: httpx.AsyncClient,
                        page_title: str, src: str, alt: str) -> tuple[str | None, str | None, str]:
    """Returns (sha256, caption, source) where source is vision|alt|none."""
    fallback = (None, alt, "alt") if alt and len(alt) > 3 else (None, None, "none")
    if not llm.available:        # no key: do not download thousands of images we cannot caption
        return fallback
    try:
        r = await http.get(src, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError:
        return fallback
    media = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if media not in MEDIA or len(r.content) > cfg.max_image_bytes or len(r.content) < 1500:
        return fallback
    sha = hashlib.sha256(r.content).hexdigest()
    row = db.execute("SELECT caption FROM caption_cache WHERE sha256=?", (sha,)).fetchone()
    if row:
        return sha, row["caption"] or None, "vision" if row["caption"] else "none"
    if not llm.available:
        return sha, fallback[1], fallback[2]
    system, version = load_prompt("caption")
    try:
        res = await llm.structured(
            purpose="caption", model=cfg.fast_model, system=system, prompt_version=version,
            user=f"Article: {page_title}\nAlt text: {alt or '(none)'}", schema=SCHEMA,
            max_tokens=400, image=(r.content, media))
    except Exception:  # noqa: BLE001 - one bad image must not stop a crawl
        return sha, fallback[1], fallback[2]
    cap = "" if res.data.get("decorative") else res.data.get("caption", "").strip()
    db.execute("INSERT OR REPLACE INTO caption_cache(sha256,caption,model) VALUES (?,?,?)", (sha, cap, res.model))
    db.commit()
    return sha, cap or None, "vision" if cap else "none"
