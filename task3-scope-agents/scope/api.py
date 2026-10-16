from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .pipeline import NotApproved, Pipeline

STATIC = Path(__file__).resolve().parent.parent / "static"


class NewProject(BaseModel):
    client: str


class NewMeeting(BaseModel):
    label: str = "Meeting"
    transcript: str = Field(min_length=20, max_length=400_000)


class Approval(BaseModel):
    approved_by: str = Field(min_length=2)


def create_app(pipeline: Pipeline | None = None) -> FastAPI:
    st = {"p": pipeline}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st["p"] = st["p"] or Pipeline()
        yield
        await st["p"].aclose()

    app = FastAPI(title="Scope Agents API", version="1.0", lifespan=lifespan)
    P = lambda: st["p"]  # noqa: E731

    @app.post("/v1/projects")
    async def new_project(b: NewProject):
        return {"project_id": P().create_project(b.client)}

    @app.post("/v1/projects/{pid}/meetings")
    async def add_meeting(pid: int, b: NewMeeting):
        """Adds a transcript and analyzes it. The first meeting creates the scope; later ones update it."""
        mid = P().add_meeting(pid, b.label, b.transcript)
        v = await P().analyze(pid, mid)
        return {"meeting_id": mid, "version": v}

    @app.get("/v1/projects/{pid}/versions")
    async def versions(pid: int):
        rows = P().db.execute("SELECT version,status,meeting_id,approved_by,created_at FROM scope_versions WHERE project_id=? ORDER BY version", (pid,))
        return [dict(r) for r in rows]

    @app.get("/v1/projects/{pid}/versions/{v}")
    async def version(pid: int, v: int):
        import json
        r = P().db.execute("SELECT * FROM scope_versions WHERE project_id=? AND version=?", (pid, v)).fetchone()
        if not r:
            raise HTTPException(404)
        return {"status": r["status"], "scope": json.loads(r["scope_json"]), "changes": json.loads(r["changes_json"]), "proposal_md": r["proposal_md"]}

    @app.post("/v1/projects/{pid}/versions/{v}/approve")
    async def approve(pid: int, v: int, b: Approval):
        P().approve(pid, v, b.approved_by)
        return {"ok": True}

    @app.post("/v1/projects/{pid}/versions/{v}/reject")
    async def reject(pid: int, v: int):
        P().reject(pid, v)
        return {"ok": True}

    @app.get("/v1/projects/{pid}/versions/{v}/export", response_class=PlainTextResponse)
    async def export(pid: int, v: int):
        """Client-ready proposal. 409 until a human has approved this version."""
        try:
            return P().export_for_client(pid, v)
        except NotApproved as e:
            raise HTTPException(409, str(e)) from None
        except KeyError:
            raise HTTPException(404) from None

    @app.get("/", include_in_schema=False)
    async def page():
        return FileResponse(STATIC / "scope.html")

    return app
