from __future__ import annotations

import asyncio

import pytest
from astrbot_plugin_beszel.core.beszel.hub_data import HubData
from astrbot_plugin_beszel.core.beszel.models import HistoryRange
from astrbot_plugin_beszel.core.errors import BeszelTransportError


class _Client:
    """Fake Hub client; every answer names the call that produced it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.failures = 0

    async def _answer(self, *call) -> list[str]:
        self.calls.append(call)
        answer = [f"{call[0]}#{len(self.calls)}"]
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.failures:
            self.failures -= 1
            raise BeszelTransportError("fixture failure", status_code=500)
        return answer

    async def get_system(self, system_id: str):
        return await self._answer("system", system_id)

    async def get_history(self, system_id: str, history_range: HistoryRange):
        return await self._answer("history", system_id, history_range)


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    return now


@pytest.mark.asyncio
async def test_results_are_reused_only_within_the_callers_ttl(clock) -> None:
    client = _Client()
    data = HubData(client)

    first = await data.get_system("s1", ttl=10)
    clock[0] += 5
    assert await data.get_system("s1", ttl=10) == first
    clock[0] += 10
    # 15 seconds old: fine for a caller accepting 30, too old for one accepting 10.
    assert await data.get_system("s1", ttl=30) == first
    assert await data.get_system("s1", ttl=10) == ["system#2"]
    assert client.calls == [("system", "s1"), ("system", "s1")]


@pytest.mark.asyncio
async def test_zero_ttl_always_queries_and_stores_nothing(clock) -> None:
    client = _Client()
    data = HubData(client)

    await data.get_system("s1", ttl=0)
    await data.get_system("s1", ttl=0)
    assert await data.get_system("s1", ttl=10) == ["system#3"]
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_concurrent_identical_requests_share_one_fetch(clock) -> None:
    client = _Client()
    client.gate = asyncio.Event()
    data = HubData(client)

    first = asyncio.create_task(data.get_system("s1", ttl=10))
    second = asyncio.create_task(data.get_system("s1", ttl=10))
    other = asyncio.create_task(data.get_system("s2", ttl=10))
    await client.started.wait()
    for _ in range(5):
        await asyncio.sleep(0)
    client.gate.set()

    assert await first == await second == ["system#1"]
    assert await other == ["system#2"]
    assert client.calls == [("system", "s1"), ("system", "s2")]


@pytest.mark.asyncio
async def test_history_ranges_are_cached_separately(clock) -> None:
    client = _Client()
    data = HubData(client)

    await data.get_history("s1", HistoryRange.ONE_HOUR, ttl=10)
    await data.get_history("s1", HistoryRange.ONE_DAY, ttl=10)
    await data.get_history("s1", HistoryRange.ONE_HOUR, ttl=10)

    assert client.calls == [
        ("history", "s1", HistoryRange.ONE_HOUR),
        ("history", "s1", HistoryRange.ONE_DAY),
    ]


@pytest.mark.asyncio
async def test_failures_are_not_stored(clock) -> None:
    client = _Client()
    client.failures = 1
    data = HubData(client)

    with pytest.raises(BeszelTransportError):
        await data.get_system("s1", ttl=10)
    assert await data.get_system("s1", ttl=10) == ["system#2"]


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_cancel_the_shared_request(clock) -> None:
    client = _Client()
    client.gate = asyncio.Event()
    data = HubData(client)

    cancelled = asyncio.create_task(data.get_system("s1", ttl=10))
    waiting = asyncio.create_task(data.get_system("s1", ttl=10))
    await client.started.wait()
    cancelled.cancel()
    client.gate.set()

    assert await waiting == ["system#1"]
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_close_cancels_requests_in_flight(clock) -> None:
    client = _Client()
    client.gate = asyncio.Event()
    data = HubData(client)

    pending = asyncio.create_task(data.get_system("s1", ttl=10))
    await client.started.wait()
    await data.close()

    with pytest.raises(asyncio.CancelledError):
        await pending


@pytest.mark.asyncio
async def test_list_results_are_handed_out_as_copies(clock) -> None:
    data = HubData(_Client())

    first = await data.get_system("s1", ttl=10)
    first.append("changed by caller")

    assert await data.get_system("s1", ttl=10) == ["system#1"]
