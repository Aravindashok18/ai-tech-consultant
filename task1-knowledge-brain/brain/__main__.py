"""CLI: python -m brain crawl|sync|serve|ask"""
from __future__ import annotations

import argparse
import asyncio
import json

from .core import Brain
from .crawl import daily_sync, initial_crawl, recaption


async def _run(args: argparse.Namespace) -> None:
    b = Brain()
    try:
        if args.cmd == "crawl":
            print(json.dumps(await initial_crawl(b, args.limit)))
        elif args.cmd == "sync":
            print(json.dumps(await daily_sync(b)))
        elif args.cmd == "recaption":
            print(json.dumps(await recaption(b)))
        elif args.cmd == "ask":
            from .answer import Chatbot
            a = await Chatbot(b).ask(args.question)
            print(a.answer, "\n", [s.url for s in a.sources])
    finally:
        await b.aclose()


def main() -> None:
    p = argparse.ArgumentParser(prog="brain")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="first full read (resumable)")
    c.add_argument("--limit", type=int)
    sub.add_parser("sync", help="daily diff against the sitemap")
    sub.add_parser("recaption", help="caption screenshots with Claude after adding an API key")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    a = sub.add_parser("ask")
    a.add_argument("question")
    args = p.parse_args()
    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("brain.api:create_app", factory=True, host="0.0.0.0", port=args.port)
    else:
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
