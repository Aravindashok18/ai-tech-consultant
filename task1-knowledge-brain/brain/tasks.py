"""Human-expert loop: unanswerable questions become tasks; the expert's answer becomes searchable memory."""
from __future__ import annotations

import numpy as np

from .chunker import chunk_article
from .core import Brain, now

DUP_COSINE = 0.9


async def open_task(brain: Brain, question: str, reason: str) -> int:
    """Create a task, or bump an existing open one for the same question (no duplicate work for experts)."""
    qv = (await brain.embed([question]))[0]
    for r in brain.db.execute("SELECT id, question_emb FROM tasks WHERE status='open'"):
        if r["question_emb"] and float(np.frombuffer(r["question_emb"], dtype=np.float32) @ qv) >= DUP_COSINE:
            brain.db.execute("UPDATE tasks SET asked_count=asked_count+1 WHERE id=?", (r["id"],))
            brain.db.commit()
            return int(r["id"])
    cur = brain.db.execute(
        "INSERT INTO tasks(question,question_emb,reason,created_at) VALUES (?,?,?,?)",
        (question, qv.astype(np.float32).tobytes(), reason, now()))
    brain.db.commit()
    return int(cur.lastrowid)


async def answer_task(brain: Brain, task_id: int, answer: str, expert: str = "expert") -> str:
    """Store the expert's answer as a first-class page so the next ask finds it."""
    t = brain.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if t is None:
        raise KeyError(task_id)
    url = f"expert://task/{task_id}"
    title = f"Expert answer: {t['question']}"
    text = f"Q: {t['question']}\nA: {answer}"
    chunks = chunk_article(title, "EXPERT", "", text) or [f"[{title}]\n{text}"]
    chunks = [c.replace("[EXPERT", "[EXPERT answer", 1) if c.startswith("[EXPERT") else c for c in chunks]
    vecs = await brain.embed(chunks)
    async with brain._write_lock:
        brain.db.execute("DELETE FROM chunks WHERE page_url=?", (url,))
        brain.db.execute(
            "INSERT INTO pages(url,kind,title,category,text,status,fetched_at) VALUES (?, 'expert', ?, 'EXPERT', ?, 'active', ?)"
            " ON CONFLICT(url) DO UPDATE SET title=excluded.title, text=excluded.text, status='active', fetched_at=excluded.fetched_at",
            (url, title, text, now()))
        for i, c in enumerate(chunks):
            brain.db.execute("INSERT INTO chunks(page_url,ord,kind,text,embedding) VALUES (?,?,?,?,?)",
                             (url, i, "text", c, vecs[i].astype(np.float32).tobytes()))
        brain.db.execute("UPDATE tasks SET status='answered', answer=?, expert=?, page_url=?, answered_at=? WHERE id=?",
                         (answer, expert, url, now(), task_id))
        brain.db.commit()
        brain.version += 1
    return url
