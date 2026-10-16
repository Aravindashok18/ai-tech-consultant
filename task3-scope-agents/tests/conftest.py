from __future__ import annotations

import pytest

from scope.config import Config
from scope.db import connect
from scope.kb import SettingMatch
from scope.pipeline import Pipeline


class FakeKB:
    """Pretend the help center knows about custom domains and social images only."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.up = True

    async def check(self, text: str) -> SettingMatch:
        self.calls.append(text)
        if not self.up:
            return SettingMatch(reachable=False)
        if "domain" in text.lower():
            return SettingMatch(True, "Point your CNAME record to the host in Admin > Domain.", "https://help.example.com/domain", "Custom domain")
        return SettingMatch()

    async def aclose(self) -> None: ...


@pytest.fixture
def kb() -> FakeKB:
    return FakeKB()


@pytest.fixture
def pipe(kb) -> Pipeline:
    return Pipeline(Config(api_key="", hourly_rate=50), kb=kb, db=connect(":memory:"))
