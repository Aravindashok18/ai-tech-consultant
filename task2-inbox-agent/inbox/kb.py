"""Client for Task 1. The inbox agent never copies Knowledge Brain code; it only calls this API."""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .config import Config


@dataclass
class Facts:
    answer: str = ""
    abstained: bool = True
    sources: list[dict] = field(default_factory=list)
    task_id: int | None = None
    ok: bool = False          # False when the KB was unreachable
    error: str = ""


class KBClient:
    def __init__(self, cfg: Config, client: httpx.AsyncClient | None = None) -> None:
        h = {"X-API-Key": cfg.kb_api_key} if cfg.kb_api_key else {}
        self.c = client or httpx.AsyncClient(base_url=cfg.kb_url, headers=h, timeout=60)

    async def lookup(self, question: str) -> Facts:
        """Ask Task 1. A down or slow KB degrades to 'no facts', which forces a human check, never a guess."""
        for attempt in range(2):
            try:
                r = await self.c.post("/v1/ask", json={"question": question[:1500]})
                r.raise_for_status()
                d = r.json()
                return Facts(d["answer"], d["abstained"], d["sources"], d.get("task_id"), True)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                err = repr(e)
        return Facts(error=err)

    async def aclose(self) -> None:
        await self.c.aclose()
