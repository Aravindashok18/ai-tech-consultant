from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import ROOT, Config


def load_prompt(name: str) -> tuple[str, str]:
    files = sorted((ROOT / "prompts").glob(f"{name}_v*.md"), key=lambda p: int(p.stem.rsplit("_v", 1)[1]))
    return files[-1].read_text(), files[-1].stem


@dataclass
class LLMResult:
    data: dict
    model: str
    cost_usd: float
    latency_ms: int
    prompt_version: str


class LLM:
    def __init__(self, cfg: Config, db: sqlite3.Connection) -> None:
        self.cfg, self.db, self._c = cfg, db, None
        if cfg.api_key:
            import anthropic
            self._c = anthropic.AsyncAnthropic(api_key=cfg.api_key, max_retries=4)

    @property
    def available(self) -> bool:
        return self._c is not None

    async def structured(self, *, purpose: str, model: str, system: str, user: str, schema: dict,
                         prompt_version: str, max_tokens: int = 1200) -> LLMResult:
        if self._c is None:
            raise RuntimeError("no ANTHROPIC_API_KEY")
        t0 = time.perf_counter()
        r = await self._c.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": "respond", "description": "Return the result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": "respond"})
        ms = int((time.perf_counter() - t0) * 1000)
        i, o = r.usage.input_tokens, r.usage.output_tokens
        pi, po = self.cfg.prices.get(model, (0.0, 0.0))
        cost = (i * pi + o * po) / 1e6
        self.db.execute("INSERT INTO llm_usage(ts,purpose,model,prompt_version,input_tokens,output_tokens,cost_usd,latency_ms)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (datetime.now(UTC).isoformat(), purpose, model, prompt_version, i, o, cost, ms))
        self.db.commit()
        block = next(b for b in r.content if b.type == "tool_use")
        return LLMResult(dict(block.input), model, cost, ms, prompt_version)
