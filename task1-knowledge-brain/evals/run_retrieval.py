"""Retrieval eval (no API key needed). For each case: is an expected article in the top-k?
Also reports the cosine gap between in-domain and off-topic questions, which is what the 'I don't know' gate depends on.
Usage: python evals/run_retrieval.py"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brain.answer import Chatbot  # noqa: E402
from brain.core import Brain  # noqa: E402


async def main() -> None:
    cases = json.loads((Path(__file__).parent / "retrieval_cases.json").read_text())
    b = Brain()
    bot = Chatbot(b)
    hit1 = hit3 = hit6 = 0
    miss, lat, in_cos, ood_cos = [], [], [], []
    ind = [c for c in cases if not c.get("ood")]
    for c in cases:
        t0 = time.perf_counter()
        hits = await bot.retriever.search(c["q"], k=6)
        lat.append((time.perf_counter() - t0) * 1000)
        best = max((h.cosine for h in hits), default=0.0)
        if c.get("ood"):
            ood_cos.append(best)
            continue
        in_cos.append(best)
        pos = next((i for i, h in enumerate(hits) if any(k in (h.title + " " + h.text).lower() for k in c["expect"])), None)
        hit1 += pos == 0
        hit3 += pos is not None and pos < 3
        hit6 += pos is not None
        if pos is None:
            miss.append(c["q"])
    n = len(ind)
    thr = (min(in_cos) + max(ood_cos)) / 2 if ood_cos else None
    print(json.dumps({
        "articles": b.db.execute("select count(*) from pages where status='active' and kind='article'").fetchone()[0],
        "in_domain_cases": n, "hit@1": round(hit1 / n, 3), "hit@3": round(hit3 / n, 3), "hit@6": round(hit6 / n, 3),
        "median_ms": round(statistics.median(lat)), "p95_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1]),
        "cosine_in_domain_min_median": [round(min(in_cos), 2), round(statistics.median(in_cos), 2)],
        "off_topic_cases": len(ood_cos), "cosine_off_topic_max": round(max(ood_cos), 2) if ood_cos else None,
        "off_topic_blocked_at_gate": sum(c < b.cfg.min_cosine for c in ood_cos), "gate": b.cfg.min_cosine,
        "misses": miss}, indent=1))
    await b.aclose()


asyncio.run(main())
