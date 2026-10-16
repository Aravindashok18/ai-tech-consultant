from __future__ import annotations

from brain.answer import Chatbot
from brain.crawl import daily_sync, initial_crawl
from brain.parser import parse_article
from brain.sitemap import canonical, parse_sitemap
from brain.tasks import answer_task

from .conftest import BASE, page

REFUND = page("How refunds work", "<h2>Refund window</h2><p>Members can request a refund within 14 days of purchase "
              "from Payments > Refunds in the admin panel.</p>",
              '<img src="https://cdn.example.com/a.png" alt="Refund button location in admin">')
CNAME = page("Connect a custom domain", "<p>Point the CNAME record of your domain to the directory host name.</p>")


def test_parser_extracts_structure_and_images():
    art = parse_article(REFUND, f"{BASE}/1")
    assert art and art.title == "How refunds work"
    assert (art.category, art.folder) == ("Payments", "Refunds")
    assert "14 days" in art.text and "[[IMAGE 0]]" in art.text
    assert art.images[0].alt == "Refund button location in admin"
    assert "Link: x" not in art.text


REAL_BREADCRUMB = (
    '<div class="breadcrumb"><a href="/support/solutions"> Solution home </a>'
    '<a href="/support/solutions/12000001358">Get Started Here</a>'
    '<a href="/support/solutions/folders/12000006926">Changelog - Software Updates</a>'
    f'<a title="x" href="{BASE}/12000030685-x"> x </a></div>'
    '<article id="article-body"><h1 class="post-title">06/02/17 - Software Updates</h1><p>Fixed a bug.</p></article>')


def test_parser_reads_category_without_section_label():
    # Real help-center pages link the category as /support/solutions/<id> with no "Section:" prefix.
    art = parse_article(REAL_BREADCRUMB, f"{BASE}/12000030685")
    assert (art.category, art.folder) == ("Get Started Here", "Changelog - Software Updates")


def test_sitemap_canonical_id():
    assert canonical(f"{BASE}/123-some-slug") == f"{BASE}/123"
    xml = f"<urlset><url><loc>{BASE}/9-a</loc><lastmod>2026-01-01</lastmod></url><url><loc>https://x/support/home</loc></url></urlset>"
    assert list(parse_sitemap(xml)) == [f"{BASE}/9"]


async def test_crawl_sync_add_update_remove(brain, http):
    http.site = {f"{BASE}/1": REFUND, f"{BASE}/2": CNAME}
    http.set_sitemap({"1": "d1", "2": "d1"})
    stats = await initial_crawl(brain, log=lambda *_: None)
    assert stats["added"] == 2
    bot = Chatbot(brain)
    assert any("14 days" in h.text for h in await bot.retriever.search("refund window how many days"))

    # day 2: page 1 updated, page 2 removed, page 3 added
    http.site[f"{BASE}/1"] = page("How refunds work", "<p>Refunds are now allowed within 30 days.</p>")
    http.site[f"{BASE}/3"] = page("Hide the footer", "<p>Edit the footer widget to hide it.</p>")
    http.set_sitemap({"1": "d2", "3": "d2"})
    http.calls.clear()
    out = await daily_sync(brain, log=lambda *_: None)
    assert (out["new"], out["changed"], out["removed"]) == (1, 1, 1)
    assert f"{BASE}/2" not in http.calls            # untouched pages are not re-fetched
    hits = await bot.retriever.search("custom domain CNAME")
    assert not any(h.page_url == f"{BASE}/2" for h in hits)      # removed => never in answers
    hits = await bot.retriever.search("refund days")
    assert any("30 days" in h.text for h in hits) and not any("14 days" in h.text for h in hits)


async def test_unchanged_page_is_not_reembedded(brain, http):
    http.site = {f"{BASE}/1": REFUND}
    assert await brain.ingest(f"{BASE}/1", "d1") == "added"
    assert await brain.ingest(f"{BASE}/1", "d2") == "unchanged"


async def test_sync_refuses_mass_delete_on_broken_sitemap(brain, http):
    http.site = {f"{BASE}/{i}": page(f"Article {i}", f"<p>Some useful content number {i} here.</p>") for i in range(1, 5)}
    http.set_sitemap({str(i): "d1" for i in range(1, 5)})
    await initial_crawl(brain, log=lambda *_: None)
    http.set_sitemap({"1": "d1"})                    # 3 of 4 vanish: suspicious
    try:
        await daily_sync(brain, log=lambda *_: None)
        raise AssertionError("should have refused")
    except RuntimeError as e:
        assert "refusing" in str(e)
    assert brain.db.execute("SELECT COUNT(*) FROM pages WHERE status='active'").fetchone()[0] == 4


async def test_crawl_resumes_and_records_failures(brain, http):
    http.site = {f"{BASE}/1": REFUND}                # page 2 will 404 -> removed/skip, page 1 ok
    http.set_sitemap({"1": "d1", "2": "d1"})
    await initial_crawl(brain, limit=1, log=lambda *_: None)
    left = brain.db.execute("SELECT COUNT(*) FROM crawl_queue WHERE state='pending'").fetchone()[0]
    assert left == 1                                  # a stopped crawl leaves the rest pending
    await initial_crawl(brain, log=lambda *_: None)
    assert brain.db.execute("SELECT COUNT(*) FROM crawl_queue WHERE state='pending'").fetchone()[0] == 0


async def test_dont_know_opens_task_and_expert_answer_flows_back(brain, http):
    http.site = {f"{BASE}/1": REFUND}
    http.set_sitemap({"1": "d1"})
    await initial_crawl(brain, log=lambda *_: None)
    bot = Chatbot(brain)
    q = "Do you offer white label mobile apps for iOS?"
    a = await bot.ask(q)
    assert a.abstained and a.task_id and not a.sources
    assert (await bot.ask(q)).task_id == a.task_id                  # same question does not create a second task
    assert brain.db.execute("SELECT asked_count FROM tasks").fetchone()[0] == 2

    await answer_task(brain, a.task_id, "Yes, white label iOS apps are offered on the Enterprise plan.")
    b = await bot.ask(q)
    assert not b.abstained
    assert b.sources[0].kind == "expert" or any(s.kind == "expert" for s in b.sources)
    assert "Enterprise plan" in b.answer
