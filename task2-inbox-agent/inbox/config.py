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


def _prices() -> dict[str, tuple[float, float]]:
    out = {}
    for k, v in os.environ.items():
        if k.startswith("PRICE_"):
            try:
                a, b = v.split(",")
                out[k[6:]] = (float(a), float(b))
            except ValueError:
                pass
    return out


# Kinds of email. Risky kinds never auto-send, whatever the streak: a wrong refund or
# complaint reply costs more than a slow one.
KINDS = ["how_to", "new_project", "quote", "followup", "billing", "refund", "cancellation",
         "complaint", "feature_request", "legal", "spam", "other"]
RISKY_KINDS = {"refund", "cancellation", "complaint", "legal", "billing", "quote"}


@dataclass(frozen=True)
class Config:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("INBOX_DB", ROOT / "data" / "inbox.db")))
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    draft_model: str = os.getenv("DRAFT_MODEL", "claude-sonnet-5-5")
    fast_model: str = os.getenv("FAST_MODEL", "claude-haiku-5-5")
    kb_url: str = os.getenv("KB_URL", "http://localhost:8000")
    kb_api_key: str = os.getenv("KB_API_KEY", "")
    company: str = os.getenv("COMPANY_NAME", "Support")
    signoff: str = os.getenv("AGENT_SIGNOFF", "The Support Team")
    auto_streak: int = int(os.getenv("AUTO_STREAK", "20"))
    same_threshold: float = float(os.getenv("SAME_THRESHOLD", "0.97"))
    auto_min_conf: float = float(os.getenv("AUTO_MIN_CONFIDENCE", "0.8"))
    spot_check_rate: float = float(os.getenv("SPOT_CHECK_RATE", "0.1"))
    prices: dict = field(default_factory=_prices)
