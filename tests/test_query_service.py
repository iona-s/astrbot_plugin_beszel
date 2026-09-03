from __future__ import annotations

import asyncio

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    HistoryRange,
    SystemDetails,
    SystemHistoryMetrics,
    SystemMetrics,
    SystemSummary,
)
from astrbot_plugin_beszel.core.beszel.service import QueryService
from astrbot_plugin_beszel.core.errors import (
    AmbiguousSystemError,
    SystemNotFoundError,
)


class FixtureClient:
    def __init__(self, overview_data, status_data, history_data) -> None:
        self.systems = [SystemSummary.model_validate(item) for item in overview_data]
        self.details = SystemDetails.model_validate(status_data["details"])
        self.metrics = SystemMetrics.model_validate(status_data["metrics"])
        self.containers = status_data["containers"]
        self.history = [
            SystemHistoryMetrics.model_validate(item) for item in history_data["points"]
        ]
        self.calls: list[tuple[str, object]] = []

    async def list_systems(self):
        self.calls.append(("list_systems", None))
        return self.systems

    async def get_system_details(self, system_id: str):
        self.calls.append(("details", system_id))
        return self.details

    async def get_latest_metrics(self, system_id: str):
        self.calls.append(("metrics", system_id))
        return self.metrics

    async def get_latest_containers(self, system_id: str):
        self.calls.append(("containers", system_id))
        return list(self.containers)

    async def get_history(self, system_id: str, history_range: HistoryRange):
        self.calls.append(("history", (system_id, history_range)))
        return self.history


@pytest.fixture()
def fixture_client(overview_data, status_data, history_data):
    return FixtureClient(overview_data, status_data, history_data)


@pytest.mark.asyncio
async def test_list_and_overview_sorting(fixture_client) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)

    listed = await service.list_systems()
    overview = await service.get_overview()

    assert [item.status for item in listed[:3]] == ["down", "paused", "up"]
    assert all(item.status == "up" for item in overview[:7])
    assert overview[-2].status == "down"
    assert overview[-1].status == "paused"


def test_selector_prefers_id_then_exact_name_and_rejects_ambiguous(
    fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)
    systems = fixture_client.systems
    selectors = query_data["selectors"]

    selected_by_id = service.select_system(systems, selectors["id_case_insensitive"])
    selected_by_name = service.select_system(systems, selectors["name_with_whitespace"])
    assert selected_by_id.id == systems[0].id
    assert selected_by_name.id == systems[0].id

    with pytest.raises(SystemNotFoundError):
        service.select_system(systems, selectors["missing"])

    duplicated = [*systems, systems[0]]
    with pytest.raises(AmbiguousSystemError):
        service.select_system(duplicated, selectors["ambiguous"])


@pytest.mark.asyncio
async def test_detail_and_history_use_selected_id_and_default_range(
    fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)
    system_id = query_data["detail_system_id"]

    target_name = next(
        item.name for item in fixture_client.systems if item.id == system_id
    )
    detail = await service.get_system_detail(target_name)
    history = await service.get_system_history(target_name)

    assert detail.summary.id == system_id
    assert detail.metrics is not None
    assert len(detail.containers) == len(fixture_client.containers)
    assert history.range is HistoryRange.ONE_HOUR
    assert len(history.points) == len(fixture_client.history)
    assert (
        "history",
        (system_id, HistoryRange.ONE_HOUR),
    ) in fixture_client.calls


@pytest.mark.asyncio
async def test_history_accepts_string_range(fixture_client, query_data) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_DAY)
    history = await service.get_system_history(
        fixture_client.systems[3].id, query_data["history_range_input"]
    )
    assert history.range is HistoryRange.TWELVE_HOURS


@pytest.mark.asyncio
async def test_list_systems_cache_hit(fixture_client) -> None:
    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=15.0
    )
    calls_before = len(fixture_client.calls)

    first = await service.list_systems()
    second = await service.list_systems()

    assert first == second
    client_list_calls = [
        c for c in fixture_client.calls[calls_before:] if c[0] == "list_systems"
    ]
    assert len(client_list_calls) == 1


@pytest.mark.asyncio
async def test_list_systems_cache_expiry_and_force_refresh(fixture_client) -> None:
    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=15.0
    )

    def count_list_calls() -> int:
        return sum(1 for c in fixture_client.calls if c[0] == "list_systems")

    initial_calls = count_list_calls()

    # 1. Initial query triggers 1 network call
    await service.list_systems()
    assert count_list_calls() == initial_calls + 1
    assert service._cached_systems is not None

    # 2. Repeated query hits cache
    await service.list_systems()
    assert count_list_calls() == initial_calls + 1

    # 3. Simulate expired cache triggers 1 new network call
    service._cache_expires_at = 0.0
    await service.list_systems()
    assert count_list_calls() == initial_calls + 2

    # 4. Force refresh bypasses cache triggers 1 new network call
    await service.list_systems(force_refresh=True)
    assert count_list_calls() == initial_calls + 3

    # 5. Invalidate cache triggers 1 new network call
    service.invalidate_cache()
    assert service._cached_systems is None
    await service.list_systems()
    assert count_list_calls() == initial_calls + 4


@pytest.mark.asyncio
async def test_concurrent_requests_trigger_single_hub_query(fixture_client) -> None:
    original_list = fixture_client.list_systems

    async def slow_list():
        await asyncio.sleep(0.02)
        return await original_list()

    fixture_client.list_systems = slow_list
    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=15.0
    )

    calls_before = sum(1 for c in fixture_client.calls if c[0] == "list_systems")

    # Fire 5 concurrent requests when cache is empty
    results = await asyncio.gather(*[service.list_systems() for _ in range(5)])

    assert len(results) == 5
    assert all(r == results[0] for r in results)
    # Lock ensures exactly 1 Hub request is made
    assert (
        sum(1 for c in fixture_client.calls if c[0] == "list_systems")
        == calls_before + 1
    )


@pytest.mark.asyncio
async def test_list_systems_cache_disabled(fixture_client) -> None:
    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=0
    )
    calls_before = len(fixture_client.calls)

    await service.list_systems()
    await service.list_systems()

    client_list_calls = [
        c for c in fixture_client.calls[calls_before:] if c[0] == "list_systems"
    ]
    assert len(client_list_calls) == 2


@pytest.mark.asyncio
async def test_list_systems_cache_disabled_concurrent(fixture_client) -> None:
    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=0
    )
    calls_before = sum(1 for c in fixture_client.calls if c[0] == "list_systems")

    results = await asyncio.gather(*[service.list_systems() for _ in range(3)])

    assert len(results) == 3
    assert (
        sum(1 for c in fixture_client.calls if c[0] == "list_systems")
        == calls_before + 3
    )


@pytest.mark.asyncio
async def test_list_systems_mutation_isolation(fixture_client) -> None:
    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=15.0
    )

    first = await service.list_systems()
    original_len = len(first)
    first.pop()

    second = await service.list_systems()
    assert len(second) == original_len
