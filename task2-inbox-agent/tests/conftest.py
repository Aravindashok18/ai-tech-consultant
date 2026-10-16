from __future__ import annotations

import pytest

from inbox.config import Config
from inbox.db import connect
from inbox.kb import Facts
from inbox.service import Inbox


class FakeKB:
    def __init__(self) -> None:
        self.facts = Facts("Go to Admin > Memberships > Pricing and edit the plan price.", False,
                           [{"title": "Membership prices", "url": "https://help.example.com/a/1", "kind": "text"}], None, True)
        self.calls: list[str] = []

    async def lookup(self, q: str) -> Facts:
        self.calls.append(q)
        return self.facts

    async def aclose(self) -> None: ...


@pytest.fixture
def kb() -> FakeKB:
    return FakeKB()


@pytest.fixture
def inbox(kb) -> Inbox:
    cfg = Config(api_key="", auto_streak=3, spot_check_rate=0.0, auto_min_conf=0.5, signoff="Sam")
    return Inbox(cfg, kb=kb, db=connect(":memory:"))
