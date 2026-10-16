"""Turn a help-center article page into clean text plus an ordered image list."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

SKIP_TAGS = {"script", "style", "noscript", "svg"}
HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
BLOCKS = {"p", "div", "ul", "ol", "table", "tr", "pre", "blockquote", "section", "br"}


@dataclass
class ImageRef:
    src: str
    alt: str = ""
    index: int = 0


@dataclass
class ParsedArticle:
    title: str
    category: str
    folder: str
    text: str
    images: list[ImageRef] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        key = self.text + "|" + "|".join(i.src.split("?")[0] for i in self.images)
        return hashlib.sha256(key.encode()).hexdigest()


def _walk(node: Node, out: list[str], images: list[ImageRef], base: str) -> None:
    for ch in node.iter(include_text=True):
        tag = ch.tag
        if tag == "-text":
            out.append(re.sub(r"\s+", " ", ch.text(deep=False)))
        elif tag in SKIP_TAGS:
            continue
        elif tag == "img":
            src = ch.attributes.get("data-src") or ch.attributes.get("src") or ""
            if not src or src.startswith("data:") or src.lower().split("?")[0].endswith(".svg"):
                continue
            if "emoji" in src:
                continue
            ref = ImageRef(urljoin(base, src), (ch.attributes.get("alt") or "").strip(), len(images))
            images.append(ref)
            out.append(f" [[IMAGE {ref.index}]] ")
        elif tag in HEADINGS:
            out.append(f"\n\n{HEADINGS[tag]} ")
            _walk(ch, out, images, base)
            out.append("\n\n")
        elif tag == "li":
            out.append("\n- ")
            _walk(ch, out, images, base)
        elif tag in ("td", "th"):
            _walk(ch, out, images, base)
            out.append(" | ")
        elif tag in BLOCKS:
            out.append("\n")
            _walk(ch, out, images, base)
            out.append("\n")
        else:
            _walk(ch, out, images, base)


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_article(html: str, url: str) -> ParsedArticle | None:
    tree = HTMLParser(html)
    body = tree.css_first("#article-body")
    if body is None:
        return None
    title_node = body.css_first("h1.post-title") or tree.css_first("h1")
    title = _clean(title_node.text()) if title_node else ""
    for sel in ("h1.post-title", "p.the-article-link"):
        for n in body.css(sel):
            n.decompose()

    category = folder = ""
    for a in tree.css(".breadcrumb a"):
        t = _clean(a.text())
        href = a.attributes.get("href") or ""
        if "/solutions/folders/" in href:
            folder = t
        elif re.search(r"/support/solutions/\d+$", href) and not category:   # sections link without a "Section:" label
            category = t.removeprefix("Section:").strip()

    out: list[str] = []
    images: list[ImageRef] = []
    _walk(body, out, images, url)
    return ParsedArticle(title=title, category=category, folder=folder, text=_clean("".join(out)), images=images)
