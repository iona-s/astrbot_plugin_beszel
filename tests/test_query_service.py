from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    HistoryRange,
    SystemDetails,
    SystemMetrics,
    SystemSummary,
)
from astrbot_plugin_beszel.core.beszel.service import QueryService
from astrbot_plugin_beszel.core.errors import (
    AmbiguousSystemError,
    BeszelTransportError,
    InvalidHistoryRangeError,
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
            SystemMetrics.model_validate(item) for item in history_data["points"]
        ]
        self.container_history = (
            [
                ContainerHistoryMetrics.model_validate(item)
                for item in container_history_data["records"]
            ]
            if container_history_data
            else []
        )
        self.live_systems = {system.id: system for system in self.systems}
        self.calls: list[tuple[str, object]] = []

    async def list_systems(self):
        self.calls.append(("list_systems", None))
        return self.systems

    async def get_system(self, system_id: str):
        self.calls.append(("system", system_id))
        if system_id not in self.live_systems:
            raise SystemNotFoundError(system_id)
        return self.live_systems[system_id]

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
async def test_detail_and_history_use_live_summary_after_selection(
    fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)
    system_id = query_data["detail_system_id"]
    cached = next(item for item in fixture_client.systems if item.id == system_id)
    live_status = query_data["live_status"]
    assert cached.status != live_status
    fixture_client.live_systems[system_id] = cached.model_copy(
        update={"status": live_status}
    )

    detail = await service.get_system_detail(cached.name)
    history = await service.get_system_history(cached.name)

    assert detail.summary.status == live_status
    assert history.summary.status == live_status
    assert fixture_client.calls.count(("system", system_id)) == 2


@pytest.mark.asyncio
async def test_detail_and_history_report_system_deleted_after_selection(
    fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)
    system_id = query_data["detail_system_id"]
    del fixture_client.live_systems[system_id]

    with pytest.raises(SystemNotFoundError):
        await service.get_system_detail(system_id)
    with pytest.raises(SystemNotFoundError):
        await service.get_system_history(system_id)


@pytest.mark.asyncio
async def test_history_window_ends_at_query_time(fixture_client, query_data) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)

    before = datetime.now(UTC)
    history = await service.get_system_history(query_data["detail_system_id"])
    after = datetime.now(UTC)

    assert history.window_end is not None
    assert before <= history.window_end <= after


@pytest.mark.asyncio
async def test_blank_history_range_uses_default(fixture_client, query_data) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_DAY)
    system_id = query_data["detail_system_id"]

    for blank in query_data["blank_history_ranges"]:
        history = await service.get_system_history(system_id, blank)
        assert history.range is HistoryRange.ONE_DAY
    assert ("history", (system_id, HistoryRange.ONE_DAY)) in fixture_client.calls


@pytest.mark.asyncio
async def test_invalid_history_range_fails_before_hub_requests(
    fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)

    with pytest.raises(InvalidHistoryRangeError):
        await service.get_system_history(
            query_data["detail_system_id"], query_data["invalid_history_range"]
        )
    assert fixture_client.calls == []


@pytest.mark.asyncio
async def test_history_keeps_view_when_optional_details_fail(
    fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)

    async def failing_get_system_details(_system_id):
        raise BeszelTransportError("fixture failure", status_code=500)

    fixture_client.get_system_details = failing_get_system_details
    history = await service.get_system_history(query_data["detail_system_id"])

    assert history.details is None
    assert len(history.points) == len(fixture_client.history)
    assert len(history.container_points) == len(fixture_client.container_history)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "client_method"),
    [
        ("get_system_detail", "get_system"),
        ("get_system_detail", "get_system_details"),
        ("get_system_detail", "get_latest_metrics"),
        ("get_system_detail", "get_latest_containers"),
        ("get_system_history", "get_system"),
        ("get_system_history", "get_history"),
    ],
)
async def test_required_hub_failures_propagate(
    query: str, client_method: str, fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)

    async def failing_call(*_args):
        raise BeszelTransportError("fixture failure", status_code=500)

    setattr(fixture_client, client_method, failing_call)
    with pytest.raises(BeszelTransportError):
        await getattr(service, query)(query_data["detail_system_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "client_methods"),
    [
        (
            "get_system_detail",
            (
                "get_system",
                "get_system_details",
                "get_latest_metrics",
                "get_latest_containers",
            ),
        ),
        (
            "get_system_history",
            (
                "get_system",
                "get_history",
                "get_container_history",
                "get_system_details",
            ),
        ),
    ],
)
async def test_independent_hub_reads_run_concurrently(
    query: str, client_methods: tuple[str, ...], fixture_client, query_data
) -> None:
    service = QueryService(fixture_client, default_history_range=HistoryRange.ONE_HOUR)
    started: set[str] = set()
    all_started = asyncio.Event()

    def gated(name: str):
        original = getattr(fixture_client, name)

        async def call(*args):
            started.add(name)
            if len(started) == len(client_methods):
                all_started.set()
            await all_started.wait()
            return await original(*args)

        return call

    for name in client_methods:
        setattr(fixture_client, name, gated(name))

    # A sequential implementation would block on the first gate forever.
    view = await asyncio.wait_for(
        getattr(service, query)(query_data["detail_system_id"]), timeout=5
    )

    assert started == set(client_methods)
    assert view.summary.id == query_data["detail_system_id"]


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
