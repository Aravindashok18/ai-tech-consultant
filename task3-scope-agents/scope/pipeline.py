from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from datetime import UTC, datetime

from . import agents
from .chunk import chunk_transcript
from .config import Config
from .db import connect
from .kb import KB
from .llm import LLM


def _now() -> str:
    return datetime.now(UTC).isoformat()


class NotApproved(Exception):
    ...


class Pipeline:
    def __init__(self, cfg: Config | None = None, kb: KB | None = None, db: sqlite3.Connection | None = None) -> None:
        self.cfg = cfg or Config()
        self.db = db or connect(self.cfg.db_path)
        self.llm = LLM(self.cfg, self.db)
        self.kb = kb or KB(self.cfg)

    # ---- projects & meetings -----------------------------------------------------------------
    def create_project(self, client: str) -> int:
        cur = self.db.execute("INSERT INTO projects(client,created_at) VALUES (?,?)", (client, _now()))
        self.db.commit()
        return int(cur.lastrowid)

    def add_meeting(self, project_id: int, label: str, transcript: str) -> int:
        n = self.db.execute("SELECT COUNT(*) FROM meetings WHERE project_id=?", (project_id,)).fetchone()[0]
        cur = self.db.execute("INSERT INTO meetings(project_id,label,transcript,ord,created_at) VALUES (?,?,?,?,?)",
                              (project_id, label, transcript, n + 1, _now()))
        self.db.commit()
        return int(cur.lastrowid)

    def latest_scope(self, project_id: int, approved_only: bool = False) -> dict | None:
        q = "SELECT * FROM scope_versions WHERE project_id=? AND status!='rejected'"
        if approved_only:
            q += " AND status='approved'"
        r = self.db.execute(q + " ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        return json.loads(r["scope_json"]) if r else None

    # ---- the agent team ----------------------------------------------------------------------------
    async def _extract_meeting(self, transcript: str) -> dict:
        chunks = chunk_transcript(transcript, self.cfg.chunk_chars)
        sem = asyncio.Semaphore(4)

        async def one(c: str) -> dict:
            async with sem:
                return await agents.extract(self.llm, self.cfg, c)

        parts = await asyncio.gather(*(one(c) for c in chunks))
        reqs = agents.merge_requirements([r for p in parts for r in p["requirements"]])
        return {"requirements": reqs, "chunks": len(chunks),
                "constraints": sorted({c for p in parts for c in p["constraints"]}),
                "out_of_scope": sorted({c for p in parts for c in p["out_of_scope"]})}

    async def analyze(self, project_id: int, meeting_id: int) -> int:
        """Turn one meeting into a new draft scope version. Earlier work is reused, not redone."""
        m = self.db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        prev = self.latest_scope(project_id)
        ext = await self._extract_meeting(m["transcript"])
        new = ext["requirements"]
        changes = {"changed": [], "new": [], "dropped": [], "unchanged": [], "chunks": ext["chunks"]}

        if prev is None:
            reqs = [{**r, "id": f"R{i}", "meeting": m["ord"], "status": "agreed", "change": ""} for i, r in enumerate(new, 1)]
            tasks: list[dict] = []
            todo = reqs
            scope = {"requirements": reqs, "tasks": tasks, "questions": [], "constraints": ext["constraints"],
                     "out_of_scope": ext["out_of_scope"], "review": {}}
        else:
            scope = json.loads(json.dumps(prev))
            reqs, tasks = scope["requirements"], scope["tasks"]
            agreed = [r for r in reqs if r["status"] != "dropped"]
            rec = await agents.reconcile(self.llm, self.cfg, agreed, new)
            by_id = {r["id"]: r for r in reqs}
            nextn = max([int(r["id"][1:]) for r in reqs] + [0]) + 1
            todo = []
            by_title = {r["title"]: r for r in new}
            for rel in rec["relations"]:
                src = by_title.get(rel["new_title"], {"title": rel["new_title"], "description": "", "type": "other", "source_quote": "", "assumption": ""})
                tgt = by_id.get(rel["matches_id"])
                if rel["relation"] == "same" and tgt:
                    changes["unchanged"].append(tgt["id"])
                elif rel["relation"] == "changed" and tgt:
                    tgt.update(description=src["description"] or tgt["description"], status="changed", change=rel["change"],
                               source_quote=src["source_quote"] or tgt["source_quote"], meeting=m["ord"])
                    tasks[:] = [t for t in tasks if t["req_id"] != tgt["id"]]     # only this item gets re-estimated
                    todo.append(tgt)
                    changes["changed"].append(tgt["id"])
                elif rel["relation"] == "removes" and tgt:
                    tgt["status"] = "dropped"
                    tgt["change"] = rel["change"] or "client dropped this in meeting %d" % m["ord"]
                    changes["dropped"].append(tgt["id"])
                else:   # new => outside the agreed scope: quote extra
                    r = {**src, "id": f"R{nextn}", "meeting": m["ord"], "status": "change_request", "change": "Not in the agreed scope: quoted separately."}
                    nextn += 1
                    reqs.append(r)
                    todo.append(r)
                    changes["new"].append(r["id"])
            for d in rec["dropped_ids"]:
                if d in by_id and by_id[d]["status"] != "dropped":
                    by_id[d]["status"], by_id[d]["change"] = "dropped", "de-prioritised in meeting %d" % m["ord"]
                    changes["dropped"].append(d)
            scope["constraints"] = sorted(set(scope.get("constraints", [])) | set(ext["constraints"]))
            scope["out_of_scope"] = sorted(set(scope.get("out_of_scope", [])) | set(ext["out_of_scope"]))

        # Knowledge Brain: can an existing setting do it? (only for items that need estimating)
        matches = await asyncio.gather(*(self.kb.check(f"{r['title']}. {r['description']}") for r in todo))
        notes = {r["id"]: {"found": mt.found, "summary": mt.summary, "url": mt.url, "title": mt.title} for r, mt in zip(todo, matches)}
        kb_down = any(not mt.reachable for mt in matches)

        if todo:
            est = await agents.estimate(self.llm, self.cfg, todo, notes)
            start = max([int(t["id"][1:]) for t in tasks] + [0]) + 1
            for i, t in enumerate(est):
                hi = max(t["hours_high"], t["hours_low"])
                tasks.append({"id": f"T{start + i}", "req_id": t["requirement_id"], "title": t["title"],
                              "via": "setting" if t["kind"] == "configure" else "custom",
                              "hours_low": round(t["hours_low"], 2), "hours_high": round(hi, 2), "rationale": t["rationale"],
                              "assumptions": t["assumptions"], "article": t.get("via_article", ""),
                              "would_cost_if_custom": t.get("would_cost_hours_if_custom", 0)})
            valid_ids = {r["id"] for r in reqs}
            tasks[:] = [t for t in tasks if t["req_id"] in valid_ids]

        live = [r for r in reqs if r["status"] != "dropped"]
        scope["questions"] = await agents.clarify(self.llm, self.cfg, live)
        live_tasks = [t for t in tasks if any(r["id"] == t["req_id"] for r in live)]
        scope["review"] = await agents.review(self.llm, self.cfg, live, live_tasks, scope["questions"])
        if kb_down:
            scope["review"].setdefault("risks", []).append("Knowledge Brain was unreachable: no check for existing settings, so estimates may be too high.")
        self._apply_review(scope)
        scope["totals"] = self._totals(scope)
        narr = await agents.narrative(self.llm, self.cfg, scope)
        version = (self.db.execute("SELECT COALESCE(MAX(version),0) FROM scope_versions WHERE project_id=?", (project_id,)).fetchone()[0]) + 1
        md = self.render_proposal(project_id, version, scope, narr, changes, prev)
        self.db.execute("INSERT INTO scope_versions(project_id,version,meeting_id,status,scope_json,changes_json,proposal_md,created_at)"
                        " VALUES (?,?,?,?,?,?,?,?)", (project_id, version, meeting_id, "draft", json.dumps(scope), json.dumps(changes), md, _now()))
        self.db.execute("UPDATE meetings SET analyzed=1 WHERE id=?", (meeting_id,))
        self.db.commit()
        return version

    def _apply_review(self, scope: dict) -> None:
        rev, tasks = scope["review"], scope["tasks"]
        by = {t["id"]: t for t in tasks}
        for c in rev.get("estimate_concerns", []):
            t = by.get(c["task_id"])
            if t and t["via"] == "custom" and 0 < c["suggested_low"] <= c["suggested_high"]:
                t["hours_low"], t["hours_high"] = c["suggested_low"], c["suggested_high"]
                t["rationale"] += f" Senior review adjusted: {c['reason']}"
        n = max([int(t["id"][1:]) for t in tasks] + [0]) + 1
        scope["tasks"] = [t for t in tasks if t["req_id"] != "REVIEW"]   # senior-review additions are recomputed each run
        for i, w in enumerate(rev.get("missing_work", [])):
            if w["hours_low"] > 0:
                scope["tasks"].append({"id": f"T{n + i}", "req_id": "REVIEW", "title": w["title"], "via": "custom",
                                       "hours_low": w["hours_low"], "hours_high": max(w["hours_high"], w["hours_low"]),
                                       "rationale": f"Added by senior review: {w['why']}", "assumptions": [], "article": "", "would_cost_if_custom": 0})

    def _totals(self, scope: dict) -> dict:
        live_ids = {r["id"] for r in scope["requirements"] if r["status"] != "dropped"} | {"REVIEW"}
        ts = [t for t in scope["tasks"] if t["req_id"] in live_ids]
        base = [t for t in ts if self._req(scope, t["req_id"]).get("status") != "change_request"]
        extra = [t for t in ts if self._req(scope, t["req_id"]).get("status") == "change_request"]

        def tot(items: list[dict]) -> dict:
            lo, hi = sum(t["hours_low"] for t in items), sum(t["hours_high"] for t in items)
            lo, hi = lo * (1 + self.cfg.pm_qa_fraction), hi * (1 + self.cfg.pm_qa_fraction)   # PM/QA added by code
            return {"hours_low": round(lo, 1), "hours_high": round(hi, 1),
                    "price_low": round(lo * self.cfg.hourly_rate), "price_high": round(hi * self.cfg.hourly_rate),
                    "days_low": math.ceil(lo / self.cfg.hours_per_day) if lo else 0,
                    "days_high": math.ceil(hi / self.cfg.hours_per_day) if hi else 0}
        saved = sum(max(0, t.get("would_cost_if_custom", 0) - (t["hours_low"] + t["hours_high"]) / 2) for t in ts if t["via"] == "setting")
        return {"agreed": tot(base), "change_requests": tot(extra), "saved_by_settings_hours": round(saved, 1)}

    @staticmethod
    def _req(scope: dict, rid: str) -> dict:
        return next((r for r in scope["requirements"] if r["id"] == rid), {})

    # ---- proposal text -------------------------------------------------------------------------------
    def render_proposal(self, project_id: int, version: int, scope: dict, narr: dict, changes: dict, prev: dict | None) -> str:
        c = self.cfg.currency
        client = self.db.execute("SELECT client FROM projects WHERE id=?", (project_id,)).fetchone()["client"]
        T = scope["totals"]
        L = [f"# Proposal for {client} (v{version})", "", "> DRAFT: not for the client until a person approves it.", "",
             "## Summary", narr["summary"], "", "## What you get"]
        L += [f"- {d}" for d in narr["deliverables"]]
        if narr.get("savings_note"):
            L += ["", f"**Good news:** {narr['savings_note']}"]
        if prev is not None:
            L += ["", "## What changed since last time"]
            for key, label in (("changed", "Changed"), ("new", "New (outside the agreed scope, quoted extra)"), ("dropped", "Dropped")):
                for rid in changes[key]:
                    r = self._req(scope, rid)
                    L.append(f"- **{label}:** {r['title']}: {r.get('change') or r['description']}")
            if changes["unchanged"]:
                L.append(f"- Unchanged: {len(changes['unchanged'])} items")
        L += ["", "## Tasks and hours", "| Task | Via | Hours |", "|---|---|---|"]
        for t in scope["tasks"]:
            r = self._req(scope, t["req_id"])
            if r.get("status") == "dropped":
                continue
            via = f"existing setting ([article]({t['article']}))" if t["via"] == "setting" and t["article"] else t["via"]
            extra = " *(extra)*" if r.get("status") == "change_request" else ""
            L.append(f"| {t['title']}{extra} | {via} | {t['hours_low']}-{t['hours_high']} |")
        L += ["", f"Project management and testing add {int(self.cfg.pm_qa_fraction * 100)}%.", "", "## Price and time"]
        a = T["agreed"]
        L.append(f"- Agreed scope: **{c} {a['price_low']:,}-{a['price_high']:,}**, about {a['days_low']}-{a['days_high']} working days.")
        x = T["change_requests"]
        if x["hours_high"]:
            L.append(f"- Extra items outside the agreed scope: **{c} {x['price_low']:,}-{x['price_high']:,}**, about {x['days_low']}-{x['days_high']} extra days.")
        if T["saved_by_settings_hours"]:
            L.append(f"- Saved by using settings the product already has: about {T['saved_by_settings_hours']} hours ({c} {round(T['saved_by_settings_hours'] * self.cfg.hourly_rate):,}).")
        if scope.get("out_of_scope"):
            L += ["", "## Not included"] + [f"- {o}" for o in scope["out_of_scope"]]
        L += ["", "## Assumptions"] + [f"- {x}" for x in narr["assumptions"]]
        if scope["questions"]:
            L += ["", "## Questions we need answered"] + [f"- {q['question']}" for q in scope["questions"]]
        if scope["review"].get("risks"):
            L += ["", "## Risks"] + [f"- {r}" for r in scope["review"]["risks"]]
        L += ["", "## Next steps"] + [f"- {s}" for s in narr["next_steps"]]
        return "\n".join(L)

    # ---- human approval gate ---------------------------------------------------------------------------
    def approve(self, project_id: int, version: int, by: str) -> None:
        self.db.execute("UPDATE scope_versions SET status='approved', approved_by=?, approved_at=? WHERE project_id=? AND version=?",
                        (by, _now(), project_id, version))
        self.db.commit()

    def reject(self, project_id: int, version: int) -> None:
        self.db.execute("UPDATE scope_versions SET status='rejected' WHERE project_id=? AND version=?", (project_id, version))
        self.db.commit()

    def export_for_client(self, project_id: int, version: int) -> str:
        r = self.db.execute("SELECT status, proposal_md FROM scope_versions WHERE project_id=? AND version=?", (project_id, version)).fetchone()
        if r is None:
            raise KeyError(version)
        if r["status"] != "approved":
            raise NotApproved(f"version {version} is {r['status']}; a human must approve it first")
        return r["proposal_md"].replace("> DRAFT: not for the client until a person approves it.\n\n", "")

    async def aclose(self) -> None:
        await self.kb.aclose()
