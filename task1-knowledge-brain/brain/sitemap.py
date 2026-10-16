from __future__ import annotations

import re
from dataclasses import dataclass

ARTICLE_RE = re.compile(r"/support/solutions/articles/(\d+)")


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: str | None


def canonical(url: str) -> str:
    """Article URLs are 'articles/<id>-slug'; the numeric id is the stable identity."""
    m = ARTICLE_RE.search(url)
    if not m:
        return url
    return url.split("/articles/")[0] + f"/articles/{m.group(1)}"


def parse_sitemap(xml: str) -> dict[str, SitemapEntry]:
    """Return {canonical_url: entry} for article pages only."""
    out: dict[str, SitemapEntry] = {}
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S)
        if not loc or not ARTICLE_RE.search(loc.group(1)):
            continue
        mod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S)
        c = canonical(loc.group(1).replace("&amp;", "&"))
        out[c] = SitemapEntry(loc.group(1).replace("&amp;", "&"), mod.group(1) if mod else None)
    return out
