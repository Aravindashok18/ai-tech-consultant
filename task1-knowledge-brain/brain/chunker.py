from __future__ import annotations

import re

IMAGE_TOKEN = re.compile(r"\[\[IMAGE (\d+)\]\]")
TARGET = 900


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split on markdown headings -> [(heading, body)]."""
    parts: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in text.splitlines():
        if re.match(r"#{1,6} ", line):
            if "".join(buf).strip():
                parts.append((heading, "\n".join(buf).strip()))
            heading, buf = line.lstrip("# ").strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        parts.append((heading, "\n".join(buf).strip()))
    return parts


def chunk_article(title: str, category: str, folder: str, text: str) -> list[str]:
    """Heading-aware chunks (~900 chars). Each carries 'Category > Folder > Title > Heading' for context."""
    text = IMAGE_TOKEN.sub("", text)
    trail = " > ".join(x for x in (category, folder, title) if x)
    chunks: list[str] = []
    for heading, body in split_sections(text):
        prefix = f"{trail} > {heading}" if heading else trail
        paras = [p.strip() for p in re.split(r"\n{2,}|\n(?=- )", body) if p.strip()]
        cur = ""
        for p in paras:
            if cur and len(cur) + len(p) > TARGET:
                chunks.append(f"[{prefix}]\n{cur}")
                cur = ""
            cur = f"{cur}\n{p}".strip()
        if cur:
            chunks.append(f"[{prefix}]\n{cur}")
    return chunks
