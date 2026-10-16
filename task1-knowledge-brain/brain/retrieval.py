"""Hybrid retrieval: BM25 (FTS5) + vector cosine, fused with reciprocal rank fusion."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .core import Brain

RRF_K = 60
EXPERT_BOOST = 1.15
STOP = {"the", "a", "an", "to", "do", "i", "how", "can", "my", "is", "of", "and", "in", "on", "for", "it", "what", "does", "with"}


@dataclass
class Hit:
    chunk_id: int
    page_url: str
    title: str
    kind: str           # text | image
    page_kind: str      # article | expert
    text: str
    image_src: str | None
    score: float
    cosine: float


class Retriever:
    def __init__(self, brain: Brain) -> None:
        self.b = brain
        self._ver = -1
        self._ids: np.ndarray = np.zeros(0, dtype=np.int64)
        self._mat: np.ndarray = np.zeros((0, 1), dtype=np.float32)

    def _reload(self) -> None:
        if self._ver == self.b.version and self._ver >= 0:
            return
        rows = self.b.db.execute(
            "SELECT c.id, c.embedding FROM chunks c JOIN pages p ON p.url=c.page_url "
            "WHERE p.status='active' AND c.embedding IS NOT NULL").fetchall()
        self._ids = np.array([r["id"] for r in rows], dtype=np.int64)
        self._mat = (np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
                     if rows else np.zeros((0, 1), dtype=np.float32))
        self._ver = self.b.version

    def _fts(self, query: str, n: int) -> list[int]:
        words = [w for w in re.findall(r"[A-Za-z0-9]+", query.lower()) if w not in STOP]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        rows = self.b.db.execute(
            "SELECT f.rowid id FROM chunks_fts f JOIN chunks c ON c.id=f.rowid JOIN pages p ON p.url=c.page_url "
            "WHERE chunks_fts MATCH ? AND p.status='active' ORDER BY bm25(chunks_fts) LIMIT ?", (match, n)).fetchall()
        return [r["id"] for r in rows]

    async def search(self, query: str, k: int = 6, pool: int = 30) -> list[Hit]:
        self._reload()
        qv = (await self.b.embed([query]))[0]
        cos: dict[int, float] = {}
        vec_rank: list[int] = []
        if len(self._ids):
            sims = self._mat @ qv
            top = np.argsort(-sims)[:pool]
            vec_rank = [int(self._ids[i]) for i in top]
            cos = {int(self._ids[i]): float(sims[i]) for i in top}
        fts_rank = self._fts(query, pool)
        fused: dict[int, float] = {}
        for rank_list in (vec_rank, fts_rank):
            for r, cid in enumerate(rank_list):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r)
        ids = list(fused)
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in self.b.db.execute(
            f"SELECT c.id,c.page_url,c.kind,c.text,c.image_id,p.title,p.kind pk FROM chunks c "
            f"JOIN pages p ON p.url=c.page_url WHERE c.id IN ({q})", ids)}
        srcs = {r["id"]: r["src"] for r in self.b.db.execute(
            f"SELECT id,src FROM images WHERE id IN (SELECT image_id FROM chunks WHERE id IN ({q}) AND image_id IS NOT NULL)", ids)}
        hits = []
        for cid, sc in fused.items():
            r = rows[cid]
            if r["pk"] == "expert":
                sc *= EXPERT_BOOST
            if cid not in cos:   # lexical-only hit: compute its cosine so the confidence gate sees it
                emb = self.b.db.execute("SELECT embedding FROM chunks WHERE id=?", (cid,)).fetchone()["embedding"]
                cos[cid] = float(np.frombuffer(emb, dtype=np.float32) @ qv)
            hits.append(Hit(cid, r["page_url"], r["title"], r["kind"], r["pk"], r["text"],
                            srcs.get(r["image_id"]) if r["image_id"] else None, sc, cos[cid]))
        hits.sort(key=lambda h: -h.score)
        # at most 2 chunks per page so one long article cannot crowd out the rest
        out, per = [], {}
        for h in hits:
            if per.get(h.page_url, 0) >= 2:
                continue
            per[h.page_url] = per.get(h.page_url, 0) + 1
            out.append(h)
            if len(out) == k:
                break
        return out
