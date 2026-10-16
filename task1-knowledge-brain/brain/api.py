"""HTTP API. Interactive docs at /docs, machine-readable spec at /openapi.json."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .answer import Chatbot
from .core import Brain
from .crawl import daily_sync, initial_crawl
from .tasks import answer_task

STATIC = Path(__file__).resolve().parent.parent / "static"


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000, description="The user's question in plain language.")
    history: list[Message] = Field(default_factory=list, description="Earlier turns, so follow-ups like 'and how do I undo it?' work.")
    top_k: int = Field(6, ge=1, le=12)


class SourceOut(BaseModel):
    title: str
    url: str
    snippet: str
    kind: str = Field(description="text | image | expert")
    image_url: str | None = None


class AskResponse(BaseModel):
    answer: str
    confidence: str = Field(description="high | low | none")
    abstained: bool = Field(description="True when the brain does not know. A task was opened for a human expert.")
    sources: list[SourceOut]
    task_id: int | None = None
    mode: str
    model: str | None = None
    cost_usd: float
    latency_ms: int


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(6, ge=1, le=20)


class ExpertAnswer(BaseModel):
    answer: str = Field(min_length=5)
    expert: str = "expert"


class Feedback(BaseModel):
    question: str
    answer: str
    helpful: bool
    note: str = ""


def create_app(brain: Brain | None = None, run_scheduler: bool = True) -> FastAPI:
    state: dict = {"brain": brain}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["brain"] is None:
            state["brain"] = Brain()
        b = state["brain"]
        state["bot"] = Chatbot(b)
        task = asyncio.create_task(_scheduler(b)) if run_scheduler else None
        yield
        if task:
            task.cancel()
        await b.aclose()

    async def _scheduler(b: Brain) -> None:
        """Run the daily sync at SYNC_HOUR_UTC. Also catches up if the server was down at that time."""
        while True:
            now = datetime.now(UTC)
            nxt = now.replace(hour=b.cfg.sync_hour_utc, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await asyncio.sleep((nxt - now).total_seconds())
            try:
                await daily_sync(b)
            except Exception as e:  # noqa: BLE001 - never kill the scheduler
                print(f"[sync] failed: {e!r}")

    def auth(x_api_key: str | None = Header(default=None)) -> None:
        key = state["brain"].cfg.brain_api_key
        if key and x_api_key != key:
            raise HTTPException(401, "missing or wrong X-API-Key")

    app = FastAPI(title="Knowledge Brain API", version="1.0",
                  description="Ask questions about a help center, search it, and feed expert answers back. "
                              "Used by the Inbox Agent (Task 2) and the Scope Agent (Task 3).",
                  lifespan=lifespan)

    def B() -> Brain:
        return state["brain"]

    @app.get("/health", tags=["ops"])
    async def health():
        n = B().db.execute("SELECT COUNT(*) c FROM pages WHERE status='active' AND kind='article'").fetchone()["c"]
        return {"ok": True, "articles": n, "llm": B().llm.available}

    @app.post("/v1/ask", response_model=AskResponse, tags=["chat"], dependencies=[Depends(auth)])
    async def ask(req: AskRequest):
        a = await state["bot"].ask(req.question, [m.model_dump() for m in req.history], req.top_k)
        return AskResponse(**{**asdict(a), "sources": [SourceOut(**asdict(s)) for s in a.sources]})

    @app.post("/v1/search", tags=["chat"], dependencies=[Depends(auth)])
    async def search(req: SearchRequest):
        hits = await state["bot"].retriever.search(req.query, k=req.top_k)
        return [{"title": h.title, "url": h.page_url, "text": h.text, "kind": h.kind, "image_url": h.image_src,
                 "score": round(h.score, 5), "cosine": round(h.cosine, 3)} for h in hits]

    @app.get("/v1/articles", tags=["memory"], dependencies=[Depends(auth)])
    async def get_article(url: str = Query(description="Article URL as returned in sources")):
        r = B().db.execute("SELECT * FROM pages WHERE url=?", (url,)).fetchone()
        if not r:
            raise HTTPException(404, "unknown article")
        imgs = B().db.execute("SELECT src,alt,caption,caption_source FROM images WHERE page_url=?", (url,)).fetchall()
        return {**{k: r[k] for k in ("url", "title", "category", "folder", "lastmod", "status", "fetched_at", "text")},
                "images": [dict(i) for i in imgs]}

    @app.get("/v1/tasks", tags=["experts"], dependencies=[Depends(auth)])
    async def list_tasks(status: str = Query("open", pattern="^(open|answered|all)$")):
        q = "SELECT id,question,reason,status,asked_count,answer,expert,created_at,answered_at FROM tasks"
        rows = B().db.execute(q + ("" if status == "all" else " WHERE status=?") + " ORDER BY asked_count DESC, id",
                              () if status == "all" else (status,)).fetchall()
        return [dict(r) for r in rows]

    @app.post("/v1/tasks/{task_id}/answer", tags=["experts"], dependencies=[Depends(auth)])
    async def post_answer(task_id: int, body: ExpertAnswer):
        try:
            url = await answer_task(B(), task_id, body.answer, body.expert)
        except KeyError:
            raise HTTPException(404, "unknown task") from None
        return {"ok": True, "memory_url": url}

    @app.post("/v1/feedback", tags=["chat"], dependencies=[Depends(auth)])
    async def feedback(f: Feedback):
        B().db.execute("INSERT INTO feedback(question,answer,helpful,note,ts) VALUES (?,?,?,?,datetime('now'))",
                       (f.question, f.answer, int(f.helpful), f.note))
        B().db.commit()
        return {"ok": True}

    @app.post("/v1/admin/sync", tags=["ops"], dependencies=[Depends(auth)])
    async def run_sync():
        return await daily_sync(B())

    @app.post("/v1/admin/crawl", tags=["ops"], dependencies=[Depends(auth)])
    async def run_crawl(limit: int | None = None):
        return await initial_crawl(B(), limit)

    @app.get("/v1/stats", tags=["ops"], dependencies=[Depends(auth)])
    async def stats():
        db = B().db
        one = lambda q: db.execute(q).fetchone()[0]  # noqa: E731
        runs = [dict(r) | {"stats": json.loads(r["stats"] or "{}")}
                for r in db.execute("SELECT id,kind,started_at,finished_at,status,stats FROM runs ORDER BY id DESC LIMIT 10")]
        return {
            "articles": one("SELECT COUNT(*) FROM pages WHERE status='active' AND kind='article'"),
            "removed": one("SELECT COUNT(*) FROM pages WHERE status='removed'"),
            "chunks": one("SELECT COUNT(*) FROM chunks"),
            "images_captioned": one("SELECT COUNT(*) FROM images WHERE caption IS NOT NULL"),
            "images_total": one("SELECT COUNT(*) FROM images"),
            "queue": {r["state"]: r["c"] for r in db.execute("SELECT state, COUNT(*) c FROM crawl_queue GROUP BY state")},
            "open_tasks": one("SELECT COUNT(*) FROM tasks WHERE status='open'"),
            "llm_cost_usd": one("SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage"),
            "recent_runs": runs,
        }

    @app.get("/", include_in_schema=False)
    async def chat_page():
        return FileResponse(STATIC / "chat.html")

    @app.get("/experts", include_in_schema=False)
    async def experts_page():
        return FileResponse(STATIC / "experts.html")

    return app
