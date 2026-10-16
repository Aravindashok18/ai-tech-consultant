from __future__ import annotations

from pathlib import Path

import pytest

from scope.chunk import chunk_transcript
from scope.pipeline import NotApproved

T = Path(__file__).parent.parent / "transcripts"
A1, A2 = (T / "A1_kickoff.txt").read_text(), (T / "A2_followup.txt").read_text()


def test_chunker_overlaps_and_covers_everything():
    turns = [f"Speaker: turn number {i} " + "x" * 200 for i in range(100)]
    chunks = chunk_transcript("\n".join(turns), max_chars=2000, overlap_turns=2)
    assert len(chunks) > 5 and all(len(c) <= 2400 for c in chunks)
    joined = "\n".join(chunks)
    assert all(t in joined for t in turns)                       # nothing lost
    assert chunks[0].splitlines()[-1] in chunks[1]               # overlap carries context


def test_chunker_splits_one_giant_monologue():
    assert len(chunk_transcript("A: " + "word " * 10000, max_chars=6000)) >= 8


async def test_first_meeting_makes_scope_with_hours(pipe):
    pid = pipe.create_project("Paws")
    v = await pipe.analyze(pid, pipe.add_meeting(pid, "m1", A1))
    s = pipe.latest_scope(pid)
    assert v == 1 and s["requirements"] and s["tasks"]
    assert all(t["hours_low"] > 0 and t["hours_high"] >= t["hours_low"] for t in s["tasks"])
    assert s["totals"]["agreed"]["price_high"] >= s["totals"]["agreed"]["price_low"] > 0


async def test_existing_setting_found_in_kb_is_cheap_and_cited(pipe, kb):
    pid = pipe.create_project("Paws")
    await pipe.analyze(pid, pipe.add_meeting(pid, "m1", A1))
    s = pipe.latest_scope(pid)
    cfg_tasks = [t for t in s["tasks"] if t["via"] == "setting"]
    assert cfg_tasks and cfg_tasks[0]["article"] == "https://help.example.com/domain"
    assert cfg_tasks[0]["hours_high"] <= 2 and kb.calls


async def test_kb_down_is_flagged_in_risks(pipe, kb):
    kb.up = False
    pid = pipe.create_project("Paws")
    await pipe.analyze(pid, pipe.add_meeting(pid, "m1", A1))
    assert any("unreachable" in r for r in pipe.latest_scope(pid)["review"]["risks"])


async def test_second_meeting_updates_not_restarts(pipe, kb):
    pid = pipe.create_project("Paws")
    await pipe.analyze(pid, pipe.add_meeting(pid, "m1", A1))
    calls_after_first = len(kb.calls)
    before = {r["id"]: r["title"] for r in pipe.latest_scope(pid)["requirements"]}
    v2 = await pipe.analyze(pid, pipe.add_meeting(pid, "m2", A2))
    s = pipe.latest_scope(pid)
    assert v2 == 2
    assert set(before) <= {r["id"] for r in s["requirements"]}   # ids stable
    statuses = {r["status"] for r in s["requirements"]}
    assert "change_request" in statuses                          # booking calendar etc. is extra
    assert len(kb.calls) - calls_after_first < calls_after_first # only touched items re-checked


async def test_nothing_reaches_client_without_approval(pipe):
    pid = pipe.create_project("Paws")
    v = await pipe.analyze(pid, pipe.add_meeting(pid, "m1", A1))
    with pytest.raises(NotApproved):
        pipe.export_for_client(pid, v)
    pipe.approve(pid, v, "Dana's consultant")
    md = pipe.export_for_client(pid, v)
    assert "DRAFT" not in md and "Proposal for" in md
    pipe.reject(pid, v)
    with pytest.raises(NotApproved):
        pipe.export_for_client(pid, v)


async def test_extra_work_is_priced_separately(pipe):
    pid = pipe.create_project("Paws")
    await pipe.analyze(pid, pipe.add_meeting(pid, "m1", A1))
    await pipe.analyze(pid, pipe.add_meeting(pid, "m2", A2))
    s = pipe.latest_scope(pid)
    extra = [r for r in s["requirements"] if r["status"] == "change_request"]
    assert extra and s["totals"]["change_requests"]["hours_high"] > 0
