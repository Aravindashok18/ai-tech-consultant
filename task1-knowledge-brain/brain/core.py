"""Brain: ingest, update and remove pages in the memory (SQLite rows + FTS + vectors)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

import numpy as np

from .chunker import chunk_article
from .config import Config
from .db import connect
from .embeddings import make_embedder
from .http import PoliteClient
from .llm import LLM
from .parser import parse_article
from .vision import caption_image


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Brain:
    def __init__(self, cfg: Config | None = None, embedder=None, http: PoliteClient | None = None,
                 db: sqlite3.Connection | None = None) -> None:
        self.cfg = cfg or Config()
        self.db = db or connect(self.cfg.db_path)
        self.embedder = embedder or make_embedder(self.cfg.embedder)
        self.llm = LLM(self.cfg, self.db)
        self.http = http or PoliteClient(self.cfg)
        self.version = 0          # bumped on every index change so retrieval can reload vectors
        self._write_lock = asyncio.Lock()

    # ---- embedding helpers -------------------------------------------------------------------
    async def embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self.embedder.embed, texts)

    # ---- page lifecycle ------------------------------------------------------------------------
    async def ingest(self, url: str, lastmod: str | None) -> str:
        """Fetch and index one article. Returns 'added' | 'updated' | 'unchanged' | 'removed' | 'skipped'."""
        r = await self.http.get(url)
        if r.status_code in (404, 410):
            self.remove_page(url)
            return "removed"
        r.raise_for_status()
        art = parse_article(r.text, url)
        if art is None or len(art.text) < 20:
            return "skipped"
        old = self.db.execute("SELECT content_hash, status FROM pages WHERE url=?", (url,)).fetchone()
        if old and old["status"] == "active" and old["content_hash"] == art.content_hash:
            self.db.execute("UPDATE pages SET lastmod=?, fetched_at=? WHERE url=?", (lastmod, now(), url))
            self.db.commit()
            return "unchanged"           # nothing to re-caption or re-embed

        # screenshots: caption each (cached by image hash), keep the link to this article
        captions: list[tuple[str, str, str | None, str, str]] = []
        for ref in art.images[: self.cfg.max_images_per_page]:
            sha, cap, how = await caption_image(self.cfg, self.db, self.llm, self.http.client,
                                                art.title, ref.src, ref.alt)
            captions.append((ref.src, ref.alt, sha, cap or "", how))

        text_chunks = chunk_article(art.title, art.category, art.folder, art.text)
        trail = " > ".join(x for x in (art.category, art.folder, art.title) if x)
        img_chunks = [f"[{trail} > Screenshot]\n{cap}" for (_, _, _, cap, _) in captions if cap]
        vectors = await self.embed(text_chunks + img_chunks) if (text_chunks or img_chunks) else np.zeros((0, 1))

        async with self._write_lock:
            self._replace_page(url, art, lastmod, text_chunks, captions, vectors)
        return "updated" if old else "added"

    def _replace_page(self, url, art, lastmod, text_chunks, captions, vectors) -> None:
        db = self.db
        db.execute("DELETE FROM chunks WHERE page_url=?", (url,))
        db.execute("DELETE FROM images WHERE page_url=?", (url,))
        db.execute(
            "INSERT INTO pages(url,kind,title,category,folder,lastmod,content_hash,text,status,fetched_at,removed_at)"
            " VALUES (?,?,?,?,?,?,?,?, 'active', ?, NULL)"
            " ON CONFLICT(url) DO UPDATE SET title=excluded.title, category=excluded.category,"
            " folder=excluded.folder, lastmod=excluded.lastmod, content_hash=excluded.content_hash,"
            " text=excluded.text, status='active', fetched_at=excluded.fetched_at, removed_at=NULL",
            (url, "article", art.title, art.category, art.folder, lastmod, art.content_hash, art.text, now()))
        for i, t in enumerate(text_chunks):
            db.execute("INSERT INTO chunks(page_url,ord,kind,text,embedding) VALUES (?,?,?,?,?)",
                       (url, i, "text", t, vectors[i].astype(np.float32).tobytes()))
        k = len(text_chunks)
        for src, alt, sha, cap, how in captions:
            cur = db.execute("INSERT INTO images(page_url,src,alt,sha256,caption,caption_source) VALUES (?,?,?,?,?,?)",
                             (url, src, alt, sha, cap or None, how))
            if cap:
                db.execute("INSERT INTO chunks(page_url,ord,kind,image_id,text,embedding) VALUES (?,?,?,?,?,?)",
                           (url, k, "image", cur.lastrowid,
                            f"[{art.title} > Screenshot]\n{cap}", vectors[k].astype(np.float32).tobytes()))
                k += 1
        db.commit()
        self.version += 1

    def remove_page(self, url: str) -> None:
        """A removed article stops appearing in answers: chunks and vectors are deleted, the row is kept as a tombstone."""
        self.db.execute("DELETE FROM chunks WHERE page_url=?", (url,))
        self.db.execute("DELETE FROM images WHERE page_url=?", (url,))
        self.db.execute("UPDATE pages SET status='removed', removed_at=? WHERE url=?", (now(), url))
        self.db.execute("UPDATE crawl_queue SET state='done' WHERE url=?", (url,))
        self.db.commit()
        self.version += 1

    # ---- run log -------------------------------------------------------------------------------
    def start_run(self, kind: str) -> int:
        cur = self.db.execute("INSERT INTO runs(kind,started_at,status) VALUES (?,?, 'running')", (kind, now()))
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, stats: dict) -> None:
        self.db.execute("UPDATE runs SET finished_at=?, status=?, stats=? WHERE id=?",
                        (now(), status, json.dumps(stats), run_id))
        self.db.commit()

    async def aclose(self) -> None:
        await self.http.aclose()
