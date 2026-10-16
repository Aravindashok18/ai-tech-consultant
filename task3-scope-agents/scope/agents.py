"""One job per agent. Each has a Claude path (JSON-schema output) and a crude offline fallback so the app never hard-fails."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .config import Config
from .llm import LLM

TYPES = ["feature", "change", "bug", "content", "integration", "design", "other"]
STR = {"type": "string"}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def obj(props: dict, req: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": req or list(props)}


EXTRACT = obj({
    "requirements": arr(obj({"title": STR, "description": STR, "type": {"type": "string", "enum": TYPES},
                             "source_quote": STR, "assumption": STR})),
    "constraints": arr(STR), "out_of_scope": arr(STR)})
CLARIFY = obj({"questions": arr(obj({"requirement_id": STR, "question": STR}))})
ESTIMATE = obj({"tasks": arr(obj({
    "requirement_id": STR, "title": STR, "kind": {"type": "string", "enum": ["custom", "configure"]},
    "hours_low": {"type": "number", "minimum": 0.25}, "hours_high": {"type": "number", "minimum": 0.25},
    "rationale": STR, "assumptions": arr(STR), "via_article": STR, "would_cost_hours_if_custom": {"type": "number"}}))})
REVIEW = obj({"missing_work": arr(obj({"title": STR, "why": STR, "hours_low": {"type": "number"}, "hours_high": {"type": "number"}})),
              "estimate_concerns": arr(obj({"task_id": STR, "suggested_low": {"type": "number"}, "suggested_high": {"type": "number"}, "reason": STR})),
              "risks": arr(STR)})
RECONCILE = obj({"relations": arr(obj({"new_title": STR, "relation": {"type": "string", "enum": ["same", "changed", "new", "removes"]},
                                       "matches_id": STR, "change": STR})), "dropped_ids": arr(STR)})
PROPOSAL = obj({"summary": STR, "deliverables": arr(STR), "savings_note": STR, "assumptions": arr(STR), "next_steps": arr(STR)})

WANT = re.compile(r"\b(we need|i need|we want|i want|would like|can you|could you|should be able|please add|make (the|it|our)|has to|needs? to|must)\b", re.I)
DROP = re.compile(r"\b(don'?t need|do not need|drop|cancel|remove|forget about|no longer|scrap)\b", re.I)


def _norm(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3}


def similar(a: str, b: str) -> float:
    A, B = _norm(a), _norm(b)
    jac = len(A & B) / max(1, len(A | B))
    return max(jac, SequenceMatcher(None, a.lower(), b.lower()).ratio() * 0.9)


def merge_requirements(items: list[dict], threshold: float = 0.6) -> list[dict]:
    """Chunks overlap, so the same ask appears twice; collapse near-duplicates, keep the first quote."""
    out: list[dict] = []
    for it in items:
        dup = next((o for o in out if similar(o["title"], it["title"]) >= threshold), None)
        if dup:
            if len(it.get("description", "")) > len(dup.get("description", "")):
                dup["description"] = it["description"]
        else:
            out.append(dict(it))
    return out


async def extract(llm: LLM, cfg: Config, chunk: str) -> dict:
    if llm.available:
        return await llm.call(agent="extractor", model=cfg.smart_model, prompt="extract", user=chunk, schema=EXTRACT)
    reqs = []
    for line in chunk.splitlines():
        for s in re.split(r"(?<=[.!?])\s+", line):
            if WANT.search(s) and not DROP.search(s):
                title = re.sub(r"^\w+:\s*", "", s).strip()
                reqs.append({"title": title[:90], "description": title, "type": "feature",
                             "source_quote": " ".join(title.split()[:24]), "assumption": "offline heuristic: unreviewed"})
    return {"requirements": reqs, "constraints": [], "out_of_scope": []}


async def clarify(llm: LLM, cfg: Config, reqs: list[dict]) -> list[dict]:
    if not llm.available:
        return []
    listing = "\n".join(f"{r['id']}: {r['title']} - {r['description']}" for r in reqs)
    return (await llm.call(agent="clarifier", model=cfg.smart_model, prompt="clarify", user=listing, schema=CLARIFY, max_tokens=1500))["questions"]


HOURS = {"feature": (8, 20), "change": (2, 6), "bug": (1, 4), "content": (2, 6), "integration": (10, 30), "design": (6, 16), "other": (4, 10)}


async def estimate(llm: LLM, cfg: Config, reqs: list[dict], notes: dict[str, dict]) -> list[dict]:
    """notes: requirement id -> {'found','summary','url','title'} from the Knowledge Brain."""
    if llm.available:
        blocks = []
        for r in reqs:
            n = notes.get(r["id"], {})
            kb = (f"KNOWLEDGE NOTE (from product help center, article '{n.get('title')}' {n.get('url')}): {n.get('summary')}"
                  if n.get("found") else "KNOWLEDGE NOTE: nothing found / help center unreachable.")
            blocks.append(f"{r['id']} [{r['type']}] {r['title']}\n{r['description']}\nAssumption: {r.get('assumption','')}\n{kb}")
        return (await llm.call(agent="estimator", model=cfg.smart_model, prompt="estimate", user="\n\n".join(blocks),
                               schema=ESTIMATE, max_tokens=4000))["tasks"]
    out = []
    for r in reqs:
        n = notes.get(r["id"], {})
        if n.get("found"):
            lo, hi = cfg.setting_hours
            out.append({"requirement_id": r["id"], "title": f"Configure: {r['title']}", "kind": "configure", "hours_low": lo,
                        "hours_high": hi, "rationale": "Existing product setting (offline: not verified by a model).",
                        "assumptions": [], "via_article": n.get("url", ""), "would_cost_hours_if_custom": float(sum(HOURS[r['type']]) / 2)})
        else:
            lo, hi = HOURS.get(r["type"], (4, 10))
            out.append({"requirement_id": r["id"], "title": r["title"], "kind": "custom", "hours_low": lo, "hours_high": hi,
                        "rationale": f"Default range for a '{r['type']}' (offline).", "assumptions": [], "via_article": "",
                        "would_cost_hours_if_custom": 0})
    return out


async def review(llm: LLM, cfg: Config, reqs: list[dict], tasks: list[dict], questions: list[dict]) -> dict:
    if not llm.available:
        return {"missing_work": [], "estimate_concerns": [], "risks": ["offline mode: no senior review ran"]}
    txt = ("REQUIREMENTS\n" + "\n".join(f"{r['id']}: {r['title']}" for r in reqs) + "\n\nTASKS\n" +
           "\n".join(f"{t['id']} ({t['requirement_id']}) {t['title']}: {t['hours_low']}-{t['hours_high']}h" for t in tasks) +
           "\n\nQUESTIONS\n" + "\n".join(q["question"] for q in questions))
    return await llm.call(agent="reviewer", model=cfg.smart_model, prompt="review", user=txt, schema=REVIEW, max_tokens=2000)


async def reconcile(llm: LLM, cfg: Config, agreed: list[dict], new: list[dict]) -> dict:
    if llm.available:
        txt = ("AGREED\n" + "\n".join(f"{r['id']}: {r['title']} - {r['description']}" for r in agreed) +
               "\n\nNEW FROM LATEST MEETING\n" + "\n".join(f"- {r['title']}: {r['description']} (quote: {r['source_quote']})" for r in new))
        return await llm.call(agent="reconciler", model=cfg.fast_model, prompt="reconcile", user=txt, schema=RECONCILE, max_tokens=2500)
    rel, dropped = [], []
    for r in new:
        best = max(agreed, key=lambda a: similar(a["title"], r["title"]), default=None)
        score = similar(best["title"], r["title"]) if best else 0
        if DROP.search(r["description"] + " " + r["source_quote"]) and best and score > 0.3:
            rel.append({"new_title": r["title"], "relation": "removes", "matches_id": best["id"], "change": ""})
            dropped.append(best["id"])
        elif best and score >= 0.75:
            rel.append({"new_title": r["title"], "relation": "same", "matches_id": best["id"], "change": ""})
        elif best and score >= 0.45:
            rel.append({"new_title": r["title"], "relation": "changed", "matches_id": best["id"], "change": r["description"]})
        else:
            rel.append({"new_title": r["title"], "relation": "new", "matches_id": "", "change": ""})
    return {"relations": rel, "dropped_ids": dropped}


async def narrative(llm: LLM, cfg: Config, scope: dict) -> dict:
    reqs = [r for r in scope["requirements"] if r["status"] != "dropped"]
    if llm.available:
        txt = "\n".join(f"- {r['title']}: {r['description']} ({'existing setting' if any(t['via']=='setting' for t in scope['tasks'] if t['req_id']==r['id']) else 'custom work'})" for r in reqs)
        txt += f"\nConstraints: {scope.get('constraints')}\nOpen questions: {[q['question'] for q in scope['questions']]}"
        return await llm.call(agent="proposal", model=cfg.smart_model, prompt="proposal", user=txt, schema=PROPOSAL, max_tokens=1800)
    return {"summary": f"We will deliver {len(reqs)} items you asked for.", "deliverables": [r["title"] for r in reqs],
            "savings_note": "", "assumptions": [r["assumption"] for r in reqs if r.get("assumption")][:5],
            "next_steps": ["Review this proposal", "Answer our open questions", "Approve to start"]}
