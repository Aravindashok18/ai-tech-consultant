"""Task 1 client: 'does the product already have a setting for this?'"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import Config


@dataclass
class SettingMatch:
    found: bool = False
    summary: str = ""
    url: str = ""
    title: str = ""
    reachable: bool = True


class KB:
    def __init__(self, cfg: Config, client: httpx.AsyncClient | None = None) -> None:
        h = {"X-API-Key": cfg.kb_api_key} if cfg.kb_api_key else {}
        self.c = client or httpx.AsyncClient(base_url=cfg.kb_url, headers=h, timeout=60)

    async def check(self, requirement: str) -> SettingMatch:
        q = f"Can this be done with an existing setting or built-in feature, and where? {requirement}"
        try:
            r = await self.c.post("/v1/ask", json={"question": q})
            r.raise_for_status()
            d = r.json()
        except (httpx.HTTPError, ValueError):
            return SettingMatch(reachable=False)
        src = next((s for s in d["sources"] if s["url"].startswith("http")), None)
        if d["abstained"] or not src:
            return SettingMatch()
        return SettingMatch(True, d["answer"], src["url"], src["title"])

    async def aclose(self) -> None:
        await self.c.aclose()
