from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime

from .config import ROOT, Config


def load_prompt(name: str) -> tuple[str, str]:
    files = sorted((ROOT / "prompts").glob(f"{name}_v*.md"), key=lambda p: int(p.stem.rsplit("_v", 1)[1]))
    return files[-1].read_text(), files[-1].stem


class LLM:
    def __init__(self, cfg: Config, db: sqlite3.Connection) -> None:
        self.cfg, self.db, self._c = cfg, db, None
        if cfg.api_key:
            import anthropic
            self._c = anthropic.AsyncAnthropic(api_key=cfg.api_key, max_retries=4)

    @property
    def available(self) -> bool:
        return self._c is not None

    async def call(self, *, agent: str, model: str, prompt: str, user: str, schema: dict, max_tokens: int = 3000) -> dict:
        """Run one agent step. Forced tool use gives schema-valid JSON; one retry if the model returns an empty result."""
        system, ver = load_prompt(prompt)
        for attempt in range(2):
            t0 = time.perf_counter()
            r = await self._c.messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}],
                tools=[{"name": "respond", "description": "Return the result.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": "respond"})
            ms = int((time.perf_counter() - t0) * 1000)
            i, o = r.usage.input_tokens, r.usage.output_tokens
            pi, po = self.cfg.prices.get(model, (0.0, 0.0))
            self.db.execute("INSERT INTO llm_usage(ts,agent,model,prompt_version,input_tokens,output_tokens,cost_usd,latency_ms)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (datetime.now(UTC).isoformat(), agent, model, ver, i, o, (i * pi + o * po) / 1e6, ms))
            self.db.commit()
            block = next((b for b in r.content if b.type == "tool_use"), None)
            if block and block.input:
                return dict(block.input)
        raise RuntimeError(f"{agent}: model returned no structured output")
