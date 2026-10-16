"""Chatbot: retrieve -> gate on relevance -> Claude answers with citations -> abstain and open a task if unsure."""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import Brain
from .llm import load_prompt
from .retrieval import Hit, Retriever
from .tasks import open_task

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "can_answer": {"type": "boolean"},
        "answer": {"type": "string"},
        "used_sources": {"type": "array", "items": {"type": "integer"}},
        "missing_info": {"type": "string"},
    },
    "required": ["can_answer", "answer", "used_sources", "missing_info"],
}
REWRITE_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
IDK = "I couldn't find this in the help center, so I won't guess. I've sent your question to a human expert."


@dataclass
class Source:
    title: str
    url: str
    snippet: str
    kind: str
    image_url: str | None = None


@dataclass
class Answer:
    answer: str
    confidence: str                 # high | low | none
    abstained: bool
    sources: list[Source] = field(default_factory=list)
    task_id: int | None = None
    mode: str = "claude"            # claude | extractive
    model: str | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    retrieved: int = 0


def _src(h: Hit) -> Source:
    url = h.page_url
    return Source(h.title, url, h.text[:300].replace("\n", " "), "expert" if h.page_kind == "expert" else h.kind, h.image_src)


class Chatbot:
    def __init__(self, brain: Brain) -> None:
        self.b = brain
        self.retriever = Retriever(brain)

    async def _rewrite(self, question: str, history: list[dict]) -> str:
        if not history or not self.b.llm.available:
            return question
        system, ver = load_prompt("rewrite")
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
        try:
            r = await self.b.llm.structured(purpose="rewrite", model=self.b.cfg.fast_model, system=system,
                                            prompt_version=ver, user=f"{convo}\nuser: {question}",
                                            schema=REWRITE_SCHEMA, max_tokens=100)
            return r.data["query"] or question
        except Exception:  # noqa: BLE001
            return question

    async def ask(self, question: str, history: list[dict] | None = None, top_k: int = 6) -> Answer:
        import time
        t0 = time.perf_counter()
        query = await self._rewrite(question, history or [])
        hits = await self.retriever.search(query, k=top_k)
        best = max((h.cosine for h in hits), default=0.0)

        if not hits or best < self.b.cfg.min_cosine:
            tid = await open_task(self.b, question, f"low retrieval relevance (best cosine {best:.2f})")
            return Answer(IDK, "none", True, task_id=tid, retrieved=len(hits),
                          latency_ms=int((time.perf_counter() - t0) * 1000))

        if not self.b.llm.available:     # no API key: show the best passages instead of generating
            top = hits[0]
            return Answer(f"(extractive mode, no LLM key) Closest passage:\n\n{top.text}", "low", False,
                          [_src(h) for h in hits[:3]], mode="extractive", retrieved=len(hits),
                          latency_ms=int((time.perf_counter() - t0) * 1000))

        system, ver = load_prompt("answer")
        ctx = "\n\n".join(
            f"[{i}] {'EXPERT ANSWER' if h.page_kind == 'expert' else ('Screenshot' if h.kind == 'image' else 'Article')}: "
            f"{h.title}\n{h.text}" for i, h in enumerate(hits, 1))
        res = await self.b.llm.structured(
            purpose="answer", model=self.b.cfg.answer_model, system=system, prompt_version=ver,
            user=f"Sources:\n{ctx}\n\nQuestion: {query}", schema=ANSWER_SCHEMA, max_tokens=900)
        d = res.data
        ms = int((time.perf_counter() - t0) * 1000)
        if not d["can_answer"]:
            tid = await open_task(self.b, question, f"model said sources insufficient: {d['missing_info'][:200]}")
            return Answer(IDK, "none", True, task_id=tid, model=res.model, cost_usd=res.cost_usd,
                          latency_ms=ms, retrieved=len(hits))
        used = [hits[i - 1] for i in dict.fromkeys(d["used_sources"]) if 1 <= i <= len(hits)] or hits[:1]
        seen, sources = set(), []
        for h in used:                      # one citation per article, screenshots attach to it
            if h.page_url in seen and h.kind != "image":
                continue
            seen.add(h.page_url)
            sources.append(_src(h))
        return Answer(d["answer"], "high" if best >= self.b.cfg.min_cosine + 0.1 else "low", False, sources,
                      model=res.model, cost_usd=res.cost_usd, latency_ms=ms, retrieved=len(hits))
