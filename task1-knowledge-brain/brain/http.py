"""A polite HTTP client: robots.txt, global rate limit with jitter, retries, Retry-After, adaptive slowdown."""
from __future__ import annotations

import asyncio
import random
import time
from urllib.robotparser import RobotFileParser

import httpx

from .config import Config


class PoliteClient:
    def __init__(self, cfg: Config, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": cfg.user_agent}, follow_redirects=True, timeout=30)
        self.delay = cfg.crawl_delay_s
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        self._sem = asyncio.Semaphore(cfg.crawl_concurrency)
        self._robots: RobotFileParser | None = None

    async def load_robots(self) -> None:
        rp = RobotFileParser()
        try:
            r = await self.client.get(self.cfg.base_url + "/robots.txt")
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            rp.parse([])
        self._robots = rp

    def allowed(self, url: str) -> bool:
        return self._robots is None or self._robots.can_fetch(self.cfg.user_agent, url)

    async def _wait_turn(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.delay * random.uniform(0.9, 1.3)
        if wait:
            await asyncio.sleep(wait)

    async def get(self, url: str, retries: int = 5) -> httpx.Response:
        if not self.allowed(url):
            raise PermissionError(f"blocked by robots.txt: {url}")
        async with self._sem:
            for attempt in range(retries):
                await self._wait_turn()
                try:
                    r = await self.client.get(url)
                except httpx.TransportError:
                    await asyncio.sleep(min(60, 2 ** attempt))
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    self.delay = min(self.delay * 2, 30.0)  # back off for everyone
                    ra = r.headers.get("retry-after", "")
                    await asyncio.sleep(min(120, float(ra)) if ra.isdigit() else min(60, 2 ** attempt))
                    continue
                self.delay = max(self.cfg.crawl_delay_s, self.delay * 0.95)  # slowly recover
                return r
        raise httpx.HTTPError(f"gave up after {retries} attempts: {url}")

    async def aclose(self) -> None:
        await self.client.aclose()
