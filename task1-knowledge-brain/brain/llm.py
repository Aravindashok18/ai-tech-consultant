"""Thin Claude wrapper: structured output via forced tool-use, usage/cost logging, versioned prompts."""
from __future__ import annotations

import base64
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import ROOT, Config

PROMPT_DIR = ROOT / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    """Prompts live in prompts/<name>_v<N>.md. Returns (text, 'name_vN') for the highest N."""
    files = sorted(PROMPT_DIR.glob(f"{name}_v*.md"), key=lambda p: int(p.stem.rsplit("_v", 1)[1]))
    if not files:
        raise FileNotFoundError(name)
    return files[-1].read_text(), files[-1].stem


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    prompt_version: str


class LLM:
    def __init__(self, cfg: Config, db: sqlite3.Connection) -> None:
        self.cfg, self.db = cfg, db
        self._client = None
        if cfg.api_key:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=cfg.api_key, max_retries=4)

    @property
    def available(self) -> bool:
        return self._client is not None

    def _cost(self, model: str, i: int, o: int) -> float:
        pi, po = self.cfg.prices.get(model, (0.0, 0.0))
        return (i * pi + o * po) / 1_000_000

    async def structured(self, *, purpose: str, model: str, system: str, user: str, schema: dict,
                         prompt_version: str, max_tokens: int = 1024,
                         image: tuple[bytes, str] | None = None) -> LLMResult:
        if self._client is None:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        content: list[dict] = []
        if image:
            data, media = image
            content.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                        "data": base64.b64encode(data).decode()}})
        content.append({"type": "text", "text": user})
        t0 = time.perf_counter()
        resp = await self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
            tools=[{"name": "respond", "description": "Return the result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": "respond"},
        )
        ms = int((time.perf_counter() - t0) * 1000)
        block = next(b for b in resp.content if b.type == "tool_use")
        i, o = resp.usage.input_tokens, resp.usage.output_tokens
        cost = self._cost(model, i, o)
        self.db.execute(
            "INSERT INTO llm_usage(ts,purpose,model,prompt_version,input_tokens,output_tokens,cost_usd,latency_ms)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(UTC).isoformat(), purpose, model, prompt_version, i, o, cost, ms))
        self.db.commit()
        return LLMResult(dict(block.input), model, i, o, cost, ms, prompt_version)
