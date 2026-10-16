from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from . import autonomy
from .classify import classify
from .config import Config
from .db import connect
from .draft import make_draft
from .kb import KBClient
from .learn import learn_from_edit
from .llm import LLM
from .similarity import similarity


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Inbox:
    def __init__(self, cfg: Config | None = None, kb: KBClient | None = None, db: sqlite3.Connection | None = None) -> None:
        self.cfg = cfg or Config()
        self.db = db or connect(self.cfg.db_path)
        self.llm = LLM(self.cfg, self.db)
        self.kb = kb or KBClient(self.cfg)

    # ---- intake + draft ------------------------------------------------------------------------
    async def receive(self, sender: str, subject: str, body: str, sender_name: str = "") -> int:
        cur = self.db.execute("INSERT INTO emails(sender,sender_name,subject,body,received_at) VALUES (?,?,?,?,?)",
                              (sender, sender_name or sender.split("@")[0], subject, body, _now()))
        self.db.commit()
        await self.process(int(cur.lastrowid))
        return int(cur.lastrowid)

    async def process(self, email_id: int) -> dict:
        e = self.db.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        try:
            c = await classify(self.llm, self.cfg, e["subject"], e["body"])
        except Exception as ex:  # noqa: BLE001 - a failed classify must not lose the email
            self.db.execute("UPDATE emails SET status='held', kind='other', risk_flags=? WHERE id=?",
                            (json.dumps([f"classify_failed:{type(ex).__name__}"]), email_id))
            self.db.commit()
            return {"status": "held"}
        self.db.execute("UPDATE emails SET kind=?, urgency=?, sentiment=?, risk_flags=? WHERE id=?",
                        (c["kind"], c["urgency"], c["sentiment"], json.dumps(c["risk_flags"]), email_id))
        if c["kind"] == "spam":
            self.db.execute("UPDATE emails SET status='spam' WHERE id=?", (email_id,))
            self.db.commit()
            return {"status": "spam"}
        e = self.db.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        question = c["question"] or f"{e['subject']}. {e['body'][:400]}"
        facts = await self.kb.lookup(question)
        try:
            d = await make_draft(self.llm, self.cfg, self.db, e, facts)
        except Exception as ex:  # noqa: BLE001
            self.db.execute("UPDATE emails SET status='held' WHERE id=?", (email_id,))
            self.db.commit()
            return {"status": "held", "error": repr(ex)}
        cur = self.db.execute(
            "INSERT INTO drafts(email_id,subject,body,confidence,needs_human,kb_json,model,prompt_version,rules_used,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (email_id, d["subject"], d["body"], d["confidence"], d["needs_human"],
             json.dumps({"abstained": facts.abstained, "ok": facts.ok, "sources": facts.sources}),
             d["model"], d["prompt_version"], json.dumps(d["rules_used"]), _now()))
        draft_id = int(cur.lastrowid)
        status = "drafted"
        if autonomy.may_auto_send(self.db, self.cfg, c["kind"], d["confidence"], c["risk_flags"], d["needs_human"]):
            self._send(email_id, d["subject"], d["body"], auto=True)
            self.db.execute("INSERT INTO actions(email_id,draft_id,kind,action,final_body,similarity,ts) VALUES (?,?,?,?,?,?,?)",
                            (email_id, draft_id, c["kind"], "auto_sent", d["body"], 1.0, _now()))
            if autonomy.pick_for_spot_check(self.cfg):
                self.db.execute("INSERT INTO reviews(email_id,kind,verdict,ts) VALUES (?,?, 'pending', ?)", (email_id, c["kind"], _now()))
            status = "auto_sent"
        self.db.execute("UPDATE emails SET status=? WHERE id=?", (status, email_id))
        self.db.commit()
        return {"status": status, "draft_id": draft_id}

    def _send(self, email_id: int, subject: str, body: str, auto: bool) -> None:
        e = self.db.execute("SELECT sender FROM emails WHERE id=?", (email_id,)).fetchone()
        self.db.execute("INSERT INTO outbox(email_id,to_addr,subject,body,auto,ts) VALUES (?,?,?,?,?,?)",
                        (email_id, e["sender"], subject, body, int(auto), _now()))   # swap for SMTP/Gmail here

    # ---- the human acts --------------------------------------------------------------------------
    async def resolve(self, email_id: int, final_body: str | None = None, own_reply: bool = False) -> dict:
        """final_body None = 'send as is'. own_reply = the human ignored the draft and wrote their own."""
        e = self.db.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        d = self.db.execute("SELECT * FROM drafts WHERE email_id=? ORDER BY id DESC LIMIT 1", (email_id,)).fetchone()
        if e is None:
            raise KeyError(email_id)
        draft_body = d["body"] if d else ""
        final = draft_body if final_body is None else final_body
        sim = similarity(draft_body, final) if d else 0.0
        action = "own_reply" if (own_reply or not d) else ("sent_unchanged" if sim >= self.cfg.same_threshold else "edited")
        cur = self.db.execute("INSERT INTO actions(email_id,draft_id,kind,action,final_body,similarity,ts) VALUES (?,?,?,?,?,?,?)",
                              (email_id, d["id"] if d else None, e["kind"], action, final, sim, _now()))
        action_id = int(cur.lastrowid)
        was_auto = e["status"] == "auto_sent"
        self._send(email_id, d["subject"] if d else f"Re: {e['subject']}", final, auto=False)
        self.db.execute("UPDATE emails SET status='sent' WHERE id=?", (email_id,))
        self.db.commit()
        out = autonomy.record_human_action(self.db, self.cfg, e["kind"], action)
        lessons: list[int] = []
        if action != "sent_unchanged":
            lessons = await learn_from_edit(self.llm, self.cfg, self.db, action_id, e["kind"], e["subject"], draft_body, final)
        return {"action": action, "similarity": round(sim, 3), "lessons": lessons, **out}

    def review(self, email_id: int, verdict: str, note: str = "") -> dict:
        e = self.db.execute("SELECT kind FROM emails WHERE id=?", (email_id,)).fetchone()
        self.db.execute("DELETE FROM reviews WHERE email_id=? AND verdict='pending'", (email_id,))
        lost = autonomy.record_review(self.db, email_id, e["kind"], verdict, note)
        return {"revoked": lost}

    def set_rule_active(self, rule_id: int, active: bool) -> None:
        """Undo (or redo) one learned rule."""
        self.db.execute("UPDATE rules SET active=?, deactivated_at=? WHERE id=?",
                        (int(active), None if active else _now(), rule_id))
        self.db.commit()

    async def aclose(self) -> None:
        await self.kb.aclose()
