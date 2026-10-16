"""CLI: python -m scope demo <t1.txt> [t2.txt ...]   |   python -m scope serve"""
import asyncio
import sys

from .pipeline import Pipeline


async def demo(files: list[str]) -> None:
    p = Pipeline()
    pid = p.create_project("Demo client")
    for i, f in enumerate(files, 1):
        v = await p.analyze(pid, p.add_meeting(pid, f"Meeting {i}", open(f).read()))
        print(f"\n===== version {v} (DRAFT, needs approval) =====\n")
        print(p.db.execute("SELECT proposal_md FROM scope_versions WHERE project_id=? AND version=?", (pid, v)).fetchone()[0])
    await p.aclose()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        asyncio.run(demo(sys.argv[2:]))
    else:
        import uvicorn
        uvicorn.run("scope.api:create_app", factory=True, host="0.0.0.0", port=8002)
