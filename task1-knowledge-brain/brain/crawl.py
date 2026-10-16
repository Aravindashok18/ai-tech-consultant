"""Initial crawl (resumable queue) and daily sync (diff against the sitemap)."""
from __future__ import annotations

import asyncio
from collections import Counter

from .core import Brain, now
from .sitemap import parse_sitemap

MAX_ATTEMPTS = 4


async def fetch_sitemap(brain: Brain) -> dict:
    await brain.http.load_robots()
    r = await brain.http.get(brain.cfg.sitemap_url)
    r.raise_for_status()
    entries = parse_sitemap(r.text)
    if not entries:
        raise RuntimeError("sitemap parsed to zero articles; refusing to continue")
    return entries


def plan_changes(brain: Brain, entries: dict) -> dict[str, list[str]]:
    """Compare the sitemap with memory. Only these pages need work."""
    known = {r["url"]: r for r in brain.db.execute("SELECT url,lastmod,status FROM pages WHERE kind='article'")}
    new = [u for u in entries if u not in known]
    changed = [u for u, e in entries.items() if u in known and known[u]["status"] == "active"
               and e.lastmod != known[u]["lastmod"]]
    revived = [u for u in entries if u in known and known[u]["status"] == "removed"]
    gone = [u for u, r in known.items() if u not in entries and r["status"] == "active"]
    return {"new": new, "changed": changed, "revived": revived, "removed": gone}


def enqueue(brain: Brain, entries: dict, urls: list[str]) -> None:
    brain.db.executemany(
        "INSERT INTO crawl_queue(url,state,attempts,lastmod,updated_at) VALUES (?, 'pending', 0, ?, ?)"
        " ON CONFLICT(url) DO UPDATE SET state='pending', attempts=0, lastmod=excluded.lastmod, updated_at=excluded.updated_at",
        [(u, entries[u].lastmod, now()) for u in urls])
    brain.db.commit()


async def drain_queue(brain: Brain, limit: int | None = None, log=print) -> Counter:
    """Work through pending URLs. Safe to kill at any time: each page is committed before it is marked done."""
    stats: Counter = Counter()
    rows = brain.db.execute(
        "SELECT url,lastmod,attempts FROM crawl_queue WHERE state='pending' ORDER BY url LIMIT ?",
        (limit or -1,)).fetchall()
    queue: asyncio.Queue = asyncio.Queue()
    for r in rows:
        queue.put_nowait((r["url"], r["lastmod"], r["attempts"]))

    async def worker() -> None:
        while True:
            try:
                url, lastmod, attempts = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                result = await brain.ingest(url, lastmod)
                brain.db.execute("UPDATE crawl_queue SET state='done', last_error=NULL, updated_at=? WHERE url=?",
                                 (now(), url))
                stats[result] += 1
            except Exception as e:  # noqa: BLE001
                attempts += 1
                state = "failed" if attempts >= MAX_ATTEMPTS else "pending"
                brain.db.execute("UPDATE crawl_queue SET state=?, attempts=?, last_error=?, updated_at=? WHERE url=?",
                                 (state, attempts, repr(e)[:300], now(), url))
                stats["error"] += 1
            brain.db.commit()
            done = sum(stats.values())
            if done % 25 == 0:
                log(f"[crawl] {done}/{len(rows)} {dict(stats)}")

    await asyncio.gather(*(worker() for _ in range(brain.cfg.crawl_concurrency)))
    return stats


async def initial_crawl(brain: Brain, limit: int | None = None, log=print) -> dict:
    run = brain.start_run("crawl")
    try:
        entries = await fetch_sitemap(brain)
        # resume: only enqueue what memory does not have yet and the queue is not already tracking
        known = {r["url"] for r in brain.db.execute("SELECT url FROM pages WHERE status='active'")}
        tracked = {r["url"] for r in brain.db.execute("SELECT url FROM crawl_queue")}
        todo = [u for u in entries if u not in known and u not in tracked]
        enqueue(brain, entries, todo)
        pending = brain.db.execute("SELECT COUNT(*) c FROM crawl_queue WHERE state='pending'").fetchone()["c"]
        log(f"[crawl] sitemap has {len(entries)} articles; {pending} pending")
        stats = await drain_queue(brain, limit, log)
        brain.finish_run(run, "ok", {**stats, "sitemap": len(entries)})
        return dict(stats)
    except BaseException:
        brain.finish_run(run, "failed", {})
        raise


async def daily_sync(brain: Brain, log=print) -> dict:
    """Find added/updated/removed pages and touch only those."""
    run = brain.start_run("sync")
    try:
        entries = await fetch_sitemap(brain)
        plan = plan_changes(brain, entries)
        active = brain.db.execute("SELECT COUNT(*) c FROM pages WHERE status='active' AND kind='article'").fetchone()["c"]
        if len(plan["removed"]) > max(brain.cfg.removal_guard_floor, active * brain.cfg.max_removal_fraction):
            raise RuntimeError(f"sitemap would remove {len(plan['removed'])}/{active} pages; refusing (looks broken)")
        for u in plan["removed"]:
            brain.remove_page(u)
        enqueue(brain, entries, plan["new"] + plan["changed"] + plan["revived"])
        # also retry anything left pending/failed from an interrupted crawl
        brain.db.execute("UPDATE crawl_queue SET state='pending', attempts=0 WHERE state='failed'")
        brain.db.commit()
        stats = await drain_queue(brain, None, log)
        out = {"new": len(plan["new"]), "changed": len(plan["changed"]), "revived": len(plan["revived"]),
               "removed": len(plan["removed"]), **stats}
        brain.finish_run(run, "ok", out)
        return out
    except BaseException:
        brain.finish_run(run, "failed", {})
        raise


async def recaption(brain: Brain, log=print) -> dict:
    """After adding an API key: re-read pages whose screenshots were never captioned by vision."""
    rows = brain.db.execute("SELECT DISTINCT p.url, p.lastmod FROM pages p JOIN images i ON i.page_url=p.url "
                            "WHERE p.status='active' AND i.caption_source!='vision'").fetchall()
    brain.db.executemany("UPDATE pages SET content_hash=NULL WHERE url=?", [(r["url"],) for r in rows])
    brain.db.executemany(
        "INSERT INTO crawl_queue(url,state,attempts,lastmod,updated_at) VALUES (?, 'pending', 0, ?, ?)"
        " ON CONFLICT(url) DO UPDATE SET state='pending', attempts=0", [(r["url"], r["lastmod"], now()) for r in rows])
    brain.db.commit()
    log(f"[recaption] {len(rows)} pages queued")
    return dict(await drain_queue(brain, None, log))
