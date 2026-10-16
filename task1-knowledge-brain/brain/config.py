from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(ROOT / ".env")


def _prices() -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
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
    base_url: str = "https://support.brilliantdirectories.com"
    sitemap_url: str = "https://support.brilliantdirectories.com/support/sitemap.xml"
    user_agent: str = "KnowledgeBrainBot/0.1 (+student capstone; polite crawler)"
    db_path: Path = field(default_factory=lambda: Path(os.getenv("BRAIN_DB", ROOT / "data" / "brain.db")))
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    answer_model: str = os.getenv("ANSWER_MODEL", "claude-sonnet-5-5")
    fast_model: str = os.getenv("FAST_MODEL", "claude-haiku-5-5")
    embedder: str = os.getenv("EMBEDDER", "fastembed")
    brain_api_key: str = os.getenv("BRAIN_API_KEY", "")
    sync_hour_utc: int = int(os.getenv("SYNC_HOUR_UTC", "3"))
    crawl_delay_s: float = float(os.getenv("CRAWL_DELAY_S", "1.0"))
    crawl_concurrency: int = int(os.getenv("CRAWL_CONCURRENCY", "2"))
    min_cosine: float = float(os.getenv("MIN_COSINE", "0.5"))
    max_images_per_page: int = 10
    max_image_bytes: int = 5_000_000
    prices: dict[str, tuple[float, float]] = field(default_factory=_prices)
    # sync refuses to mass-delete if the sitemap looks broken
    max_removal_fraction: float = 0.25
    removal_guard_floor: int = 5       # small removals are always fine
