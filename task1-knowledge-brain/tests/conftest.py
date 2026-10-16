from __future__ import annotations

import httpx
import pytest

from brain.config import Config
from brain.core import Brain
from brain.db import connect
from brain.embeddings import HashEmbedder

BASE = "https://support.brilliantdirectories.com/support/solutions/articles"


def page(title: str, body: str, imgs: str = "") -> str:
    return (f'<div class="breadcrumb"><a href="/support/solutions/1">Section: Payments</a>'
            f'<a href="/support/solutions/folders/2">Refunds</a></div>'
            f'<article id="article-body"><h1 class="post-title">{title}</h1>'
            f'<p class="the-article-link">Link: x</p>{body}{imgs}</article>')


class FakeResp:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code, self.text, self.headers, self.content = status, text, {}, b""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPError(str(self.status_code))


class FakeHttp:
    """Stands in for PoliteClient. `site` maps url -> html (or None for 404)."""

    def __init__(self) -> None:
        self.site: dict[str, str | None] = {}
        self.calls: list[str] = []
        self.client = self

    async def get(self, url: str, **_) -> FakeResp:
        self.calls.append(url)
        if url.endswith("sitemap.xml"):
            return FakeResp(200, self.sitemap)
        if url.endswith("robots.txt"):
            return FakeResp(200, "")
        html = self.site.get(url)
        return FakeResp(404) if html is None else FakeResp(200, html)

    async def load_robots(self) -> None: ...
    async def aclose(self) -> None: ...

    sitemap = ""

    def set_sitemap(self, entries: dict[str, str]) -> None:
        self.sitemap = "<urlset>" + "".join(
            f"<url><loc>{BASE}/{i}-slug</loc><lastmod>{m}</lastmod></url>" for i, m in entries.items()) + "</urlset>"


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def brain(http: FakeHttp) -> Brain:
    cfg = Config(api_key="", embedder="hash", crawl_delay_s=0, crawl_concurrency=2, min_cosine=0.2, removal_guard_floor=2)
    return Brain(cfg, embedder=HashEmbedder(), http=http, db=connect(":memory:"))
