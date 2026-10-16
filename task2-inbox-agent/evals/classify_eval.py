"""Accuracy of the triage step on the 50 seed emails. Uses Claude if ANTHROPIC_API_KEY is set, else the keyword fallback.
Usage: python evals/classify_eval.py [--model claude-haiku-5-5]"""
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dataclasses import replace  # noqa: E402

from inbox.classify import classify  # noqa: E402
from inbox.config import Config  # noqa: E402
from inbox.db import connect  # noqa: E402
from inbox.llm import LLM  # noqa: E402


async def main() -> None:
    cfg = Config()
    if "--model" in sys.argv:
        cfg = replace(cfg, fast_model=sys.argv[sys.argv.index("--model") + 1])
    db = connect(":memory:")
    llm = LLM(cfg, db)
    seed = json.loads((Path(__file__).parent.parent / "emails" / "seed_emails.json").read_text())
    ok, wrong, t0 = 0, Counter(), time.perf_counter()
    for e in seed:
        c = await classify(llm, cfg, e["subject"], e["body"])
        if c["kind"] == e["expected_kind"]:
            ok += 1
        else:
            wrong[(e["expected_kind"], c["kind"])] += 1
    dt = time.perf_counter() - t0
    cost = db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage").fetchone()[0]
    print(json.dumps({"mode": cfg.fast_model if llm.available else "offline keyword fallback", "n": len(seed),
                      "accuracy": round(ok / len(seed), 3), "seconds": round(dt, 1), "cost_usd": round(cost, 4),
                      "confusions": {f"{a}->{b}": n for (a, b), n in wrong.most_common()}}, indent=1))


asyncio.run(main())
