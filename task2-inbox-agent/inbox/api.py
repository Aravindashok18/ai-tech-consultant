from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import KINDS, RISKY_KINDS
from .service import Inbox

STATIC = Path(__file__).resolve().parent.parent / "static"


class NewEmail(BaseModel):
    sender: str = Field(examples=["jo@example.com"])
    subject: str = Field(max_length=300)
    body: str = Field(max_length=20000)
    sender_name: str = ""


class Resolve(BaseModel):
    final_body: str | None = Field(None, description="Omit to send the draft as is.")
    own_reply: bool = Field(False, description="True when the human wrote their own reply instead of using the draft.")


class Review(BaseModel):
    verdict: str = Field(pattern="^(ok|bad)$")
    note: str = ""


def create_app(inbox: Inbox | None = None) -> FastAPI:
    st = {"i": inbox}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st["i"] = st["i"] or Inbox()
        yield
        await st["i"].aclose()

    app = FastAPI(title="Inbox Agent API", version="1.0", lifespan=lifespan)
    I = lambda: st["i"]  # noqa: E731

    @app.post("/v1/emails")
    async def receive(m: NewEmail):
        eid = await I().receive(m.sender, m.subject, m.body, m.sender_name)
        return await get_email(eid)

    @app.get("/v1/emails")
    async def list_emails(status: str | None = None):
        q = "SELECT id,sender_name,subject,kind,urgency,sentiment,status,received_at FROM emails"
        rows = I().db.execute(q + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT 200", (status,) if status else ())
        return [dict(r) for r in rows]

    @app.get("/v1/emails/{eid}")
    async def get_email(eid: int):
        db = I().db
        e = db.execute("SELECT * FROM emails WHERE id=?", (eid,)).fetchone()
        if not e:
            raise HTTPException(404)
        d = db.execute("SELECT * FROM drafts WHERE email_id=? ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
        return {"email": dict(e), "draft": dict(d) if d else None}

    @app.post("/v1/emails/{eid}/resolve")
    async def resolve(eid: int, r: Resolve):
        try:
            return await I().resolve(eid, r.final_body, r.own_reply)
        except KeyError:
            raise HTTPException(404) from None

    @app.post("/v1/emails/{eid}/review")
    async def review(eid: int, r: Review):
        return I().review(eid, r.verdict, r.note)

    @app.get("/v1/autonomy")
    async def autonomy_board():
        db = I().db
        out = []
        for k in KINDS:
            if k == "spam":
                continue
            s = db.execute("SELECT * FROM autonomy WHERE kind=?", (k,)).fetchone()
            out.append({"kind": k, "streak": s["streak"] if s else 0, "needed": I().cfg.auto_streak,
                        "enabled": bool(s and s["enabled"]), "never_auto": k in RISKY_KINDS,
                        "revoked_reason": s["revoked_reason"] if s else None})
        return out

    @app.get("/v1/rules")
    async def rules():
        return [dict(r) for r in I().db.execute("SELECT * FROM rules ORDER BY id DESC")]

    @app.post("/v1/rules/{rid}/active/{flag}")
    async def toggle(rid: int, flag: bool):
        I().set_rule_active(rid, flag)
        return {"ok": True}

    @app.get("/v1/stats")
    async def stats():
        db = I().db
        a = db.execute("SELECT action, COUNT(*) c, AVG(similarity) s FROM actions GROUP BY action").fetchall()
        return {"actions": {r["action"]: {"n": r["c"], "avg_similarity": r["s"]} for r in a},
                "llm_cost_usd": db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage").fetchone()[0],
                "pending_reviews": db.execute("SELECT COUNT(*) FROM reviews WHERE verdict='pending'").fetchone()[0],
                "outbox": db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]}

    @app.get("/", include_in_schema=False)
    async def page():
        return FileResponse(STATIC / "inbox.html")

    return app
