from __future__ import annotations

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    HistoryRange,
    SystemDetails,
    SystemHistoryMetrics,
    SystemMetrics,
    SystemSummary,
)
from astrbot_plugin_beszel.core.beszel.service import QueryService
from astrbot_plugin_beszel.core.errors import (
    AmbiguousSystemError,
    BeszelTransportError,
    SystemNotFoundError,
)


class FixtureClient:
    def __init__(
        self, overview_data, status_data, history_data, container_history_data=None
    ) -> None:
        self.systems = [SystemSummary.model_validate(item) for item in overview_data]
        self.details = SystemDetails.model_validate(status_data["details"])
        self.metrics = SystemMetrics.model_validate(status_data["metrics"])
        self.containers = status_data["containers"]
        self.history = [
            SystemHistoryMetrics.model_validate(item) for item in history_data["points"]
        ]
        self.container_history = (
            [
                ContainerHistoryMetrics.model_validate(item)
                for item in container_history_data["records"]
            ]
            if container_history_data
            else []
        )
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

    async def get_container_history(self, system_id: str, history_range: HistoryRange):
        self.calls.append(("container_history", (system_id, history_range)))
        return self.container_history


@pytest.fixture()
def fixture_client(overview_data, status_data, history_data, container_history_data):
    return FixtureClient(
        overview_data, status_data, history_data, container_history_data
    )


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
    assert len(history.container_points) == len(fixture_client.container_history)
    assert (
        "history",
        (system_id, HistoryRange.ONE_HOUR),
    ) in fixture_client.calls
    assert (
        "container_history",
        (system_id, HistoryRange.ONE_HOUR),
    ) in fixture_client.calls


@pytest.mark.asyncio
async def test_history_with_empty_and_failing_container_data(fixture_client) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)
    system_id = fixture_client.systems[0].id

    # 1. Valid empty container history produces empty container_points
    fixture_client.container_history = []
    view = await service.get_system_history(system_id)
    assert view.container_points == []
    assert len(view.points) == len(fixture_client.history)

    # 2. Container history failure propagates rather than being masked as complete
    async def failing_get_container_history(_sys_id, _range):
        raise BeszelTransportError("Transport failed", status_code=500)

    fixture_client.get_container_history = failing_get_container_history
    with pytest.raises(BeszelTransportError):
        await service.get_system_history(system_id)


@pytest.mark.asyncio
async def test_history_accepts_string_range(fixture_client, query_data) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_DAY)
    history = await service.get_system_history(
        fixture_client.systems[3].id, query_data["history_range_input"]
    )
    assert history.range is HistoryRange.TWELVE_HOURS


@pytest.mark.asyncio
async def test_list_systems_cache_lifecycle(
    fixture_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = 1000.0
    monkeypatch.setattr("time.monotonic", lambda: current_time)

    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=15.0
    )

    def count_list_calls() -> int:
        return sum(1 for c in fixture_client.calls if c[0] == "list_systems")

    initial_calls = count_list_calls()

    await service.list_systems()
    assert count_list_calls() == initial_calls + 1

    await service.list_systems()
    assert count_list_calls() == initial_calls + 1

    current_time += 16.0
    await service.list_systems()
    assert count_list_calls() == initial_calls + 2

    await service.list_systems(force_refresh=True)
    assert count_list_calls() == initial_calls + 3

    service.invalidate_cache()
    await service.list_systems()
    assert count_list_calls() == initial_calls + 4


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
async def test_list_systems_mutation_isolation(fixture_client) -> None:
    service = QueryService(
        fixture_client, default_history_range=HistoryRange.ONE_HOUR, cache_ttl=15.0
    )

    first = await service.list_systems()
    original_len = len(first)
    first.pop()

    second = await service.list_systems()
    assert len(second) == original_len
