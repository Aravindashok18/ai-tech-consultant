from __future__ import annotations

import re

from .config import KINDS, Config
from .llm import LLM, load_prompt

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": KINDS},
        "urgency": {"type": "string", "enum": ["low", "normal", "high"]},
        "sentiment": {"type": "string", "enum": ["happy", "neutral", "frustrated", "angry"]},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string", "description": "The customer's product question(s) rewritten as one standalone question for the help center; empty if none."},
    },
    "required": ["kind", "urgency", "sentiment", "risk_flags", "question"],
}
INJECTION = re.compile(r"ignore (all |your |previous |the )+(rules|instructions)|system prompt|you are now", re.I)
RULES = [  # offline fallback so the app still triages without a key
    ("legal", r"lawyer|legal action|gdpr|chargeback|sue\b|attorney"),
    ("refund", r"refund|money back"),
    ("cancellation", r"cancel|close my account|terminate"),
    ("complaint", r"unacceptable|terrible|furious|worst|disappointed|angry"),
    ("billing", r"invoice|charged|billing|payment"),
    ("quote", r"quote|how much|pricing|estimate|cost of"),
    ("new_project", r"build me|new (site|website|project)|redesign|custom (development|work)"),
    ("followup", r"following up|any update|still waiting|haven't heard"),
    ("feature_request", r"can you add|feature request|would be great if|wish"),
    ("spam", r"seo services|guaranteed rankings|crypto|click here to claim"),
    ("how_to", r"how (do|can) i|where (do|can) i|how to|\?"),
]


async def classify(llm: LLM, cfg: Config, subject: str, body: str) -> dict:
    text = f"Subject: {subject}\n\n{body[:6000]}"
    flags_extra = ["prompt_injection"] if INJECTION.search(text) else []
    if llm.available:
        system, ver = load_prompt("classify")
        r = await llm.structured(purpose="classify", model=cfg.fast_model, system=system, prompt_version=ver,
                                 user=text, schema=SCHEMA, max_tokens=400)
        d = r.data
    else:
        low = text.lower()
        kind = next((k for k, p in RULES if re.search(p, low)), "other")
        d = {"kind": kind, "urgency": "high" if re.search(r"urgent|asap|down", low) else "normal",
             "sentiment": "angry" if kind in ("complaint", "legal") else "neutral",
             "risk_flags": [], "question": f"{subject}. {body[:300]}" if kind in ("how_to", "feature_request", "other") else ""}
    d["risk_flags"] = sorted(set(d["risk_flags"]) | set(flags_extra))
    return d
