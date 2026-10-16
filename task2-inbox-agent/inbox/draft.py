from __future__ import annotations

import json
import sqlite3

from .config import Config
from .kb import Facts
from .llm import LLM, load_prompt

SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needs_human": {"type": "string", "description": "Why a person must check this before sending; empty string if not needed."},
    },
    "required": ["subject", "body", "confidence", "needs_human"],
}


def active_rules(db: sqlite3.Connection, kind: str, limit: int = 12) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id,text FROM rules WHERE active=1 AND (kind IS NULL OR kind=?) "
        "ORDER BY (kind IS NULL), id DESC LIMIT ?", (kind, limit)).fetchall()


def examples(db: sqlite3.Connection, kind: str, n: int = 3) -> str:
    rows = db.execute(
        "SELECT e.subject, a.final_body FROM actions a JOIN emails e ON e.id=a.email_id "
        "WHERE a.kind=? AND a.action IN ('sent_unchanged','edited','own_reply') ORDER BY a.id DESC LIMIT ?",
        (kind, n)).fetchall()
    return "\n\n".join(f"Subject: {r['subject']}\nReply sent:\n{r['final_body']}" for r in rows) or "(none yet)"


async def make_draft(llm: LLM, cfg: Config, db: sqlite3.Connection, email: sqlite3.Row, facts: Facts) -> dict:
    rules = active_rules(db, email["kind"])
    flags = json.loads(email["risk_flags"] or "[]")
    kb_text = (facts.answer if facts.ok and not facts.abstained
               else "(The knowledge base could not find an answer.)" if facts.ok else "(The knowledge base was unreachable.)")
    sources = [s["url"] for s in facts.sources if s["url"].startswith("http")]
    first = (email["sender_name"] or "there").split()[0]

    if llm.available:
        system, ver = load_prompt("draft")
        system = system.format(rules="\n".join(f"- {r['text']}" for r in rules) or "- (none yet)",
                               signoff=cfg.signoff, examples=examples(db, email["kind"]))
        user = (f"KNOWLEDGE:\n{kb_text}\nSource articles: {', '.join(sources) or 'none'}\n\n"
                f"EMAIL kind={email['kind']} sentiment={email['sentiment']} flags={flags}\n"
                f"From: {email['sender_name']} <{email['sender']}>\nSubject: {email['subject']}\n\n{email['body'][:6000]}")
        r = await llm.structured(purpose="draft", model=cfg.draft_model, system=system, prompt_version=ver,
                                 user=user, schema=SCHEMA, max_tokens=1200)
        d, model, cost, ms = r.data, r.model, r.cost_usd, r.latency_ms
    else:   # offline fallback: a plain template around the KB answer
        ver, model, cost, ms = "template", "none", 0.0, 0
        body = (f"Hi {first},\n\nThanks for your email. " +
                (f"{kb_text}\n\n" + (f"More here: {sources[0]}\n\n" if sources else "")
                 if facts.ok and not facts.abstained else "We're looking into this and will get back to you shortly.\n\n") +
                f"Best,\n{cfg.signoff}")
        d = {"subject": f"Re: {email['subject']}", "body": body, "confidence": 0.6 if facts.ok and not facts.abstained else 0.2,
             "needs_human": "" if facts.ok and not facts.abstained else "no verified answer"}

    # guardrails that do not depend on the model behaving
    if not facts.ok or facts.abstained:
        d["confidence"] = min(d["confidence"], 0.5)
        d["needs_human"] = d["needs_human"] or "knowledge base had no verified answer"
    if "prompt_injection" in flags:
        d["needs_human"] = d["needs_human"] or "email contains instructions aimed at the AI"
    return {**d, "model": model, "prompt_version": ver, "rules_used": [r["id"] for r in rules],
            "cost_usd": cost, "latency_ms": ms}
