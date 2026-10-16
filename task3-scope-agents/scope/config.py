from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dotenv() -> None:
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_dotenv()


def _prices() -> dict:
    out = {}
    for k, v in os.environ.items():
        if k.startswith("PRICE_"):
            try:
                a, b = v.split(",")
                out[k[6:]] = (float(a), float(b))
            except ValueError:
                pass
    return out


@dataclass(frozen=True)
class Config:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("SCOPE_DB", ROOT / "data" / "scope.db")))
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    smart_model: str = os.getenv("SMART_MODEL", "claude-sonnet-5-5")
    fast_model: str = os.getenv("FAST_MODEL", "claude-haiku-5-5")
    kb_url: str = os.getenv("KB_URL", "http://localhost:8000")
    kb_api_key: str = os.getenv("KB_API_KEY", "")
    hourly_rate: float = float(os.getenv("HOURLY_RATE", "50"))
    currency: str = os.getenv("CURRENCY", "USD")
    hours_per_day: float = float(os.getenv("HOURS_PER_DAY", "6"))
    chunk_chars: int = int(os.getenv("CHUNK_CHARS", "6000"))
    setting_hours: tuple[float, float] = (0.5, 2.0)     # a task solved by an existing setting
    pm_qa_fraction: float = 0.15                          # added by code, not by the model
    prices: dict = field(default_factory=_prices)
