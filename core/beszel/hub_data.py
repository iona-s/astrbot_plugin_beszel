"""Hub read layer with request coalescing and caller-chosen freshness."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from functools import partial
from typing import Any

from .client import BeszelClient
from .models import (
    ContainerHistoryMetrics,
    ContainerState,
    ContainerStats,
    HistoryRange,
    ServiceState,
    SmartDeviceState,
    StoragePoolState,
    SystemDetails,
    SystemMetrics,
    SystemSummary,
)


class HubData:
    """Read-only Hub queries that may reuse recent results.

    Every read takes ``ttl``, the oldest result in seconds the caller accepts.
    With ``ttl > 0`` a stored result no older than ``ttl`` is returned, and an
    identical request still in flight is joined instead of sent again. With
    ``ttl == 0`` the Hub is always queried and nothing is stored. Failures are
    never stored. Results are shared between callers and must be treated as
    read-only; lists are handed out as shallow copies.
    """

    def __init__(self, client: BeszelClient) -> None:
        self.client = client
        self._results: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Task] = {}
        # Results older than the longest TTL any caller has used are dropped.
        self._max_ttl = 0.0

    async def _read(
        self, key: Hashable, ttl: float, fetch: Callable[[], Awaitable[Any]]
    ) -> Any:
        if ttl <= 0:
            return await fetch()
        self._max_ttl = max(self._max_ttl, ttl)
        stored = self._results.get(key)
        if stored is not None and time.monotonic() - stored[0] <= ttl:
            value = stored[1]
        else:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._fetch(key, fetch))
                # Retrieve the error even when every waiter was cancelled.
                task.add_done_callback(
                    lambda done: done.cancelled() or done.exception()
                )
                self._inflight[key] = task
            # A cancelled caller must not cancel the request others share.
            value = await asyncio.shield(task)
        return list(value) if isinstance(value, list) else value

    async def _fetch(self, key: Hashable, fetch: Callable[[], Awaitable[Any]]) -> Any:
        try:
            value = await fetch()
        finally:
            self._inflight.pop(key, None)
        now = time.monotonic()
        for stale in [
            stored_key
            for stored_key, (stored_at, _) in self._results.items()
            if now - stored_at > self._max_ttl
        ]:
            del self._results[stale]
        self._results[key] = (now, value)
        return value

    async def close(self) -> None:
        """Cancel requests still in flight and drop every stored result."""
        pending = list(self._inflight.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._inflight.clear()
        self._results.clear()

    async def list_systems(self, *, ttl: float) -> list[SystemSummary]:
        return await self._read(("systems",), ttl, self.client.list_systems)

    async def get_system(self, system_id: str, *, ttl: float) -> SystemSummary:
        return await self._read(
            ("system", system_id), ttl, partial(self.client.get_system, system_id)
        )

    async def get_system_details(
        self, system_id: str, *, ttl: float
    ) -> SystemDetails | None:
        return await self._read(
            ("details", system_id),
            ttl,
            partial(self.client.get_system_details, system_id),
        )

    async def get_latest_metrics(
        self, system_id: str, *, ttl: float
    ) -> SystemMetrics | None:
        return await self._read(
            ("latest_metrics", system_id),
            ttl,
            partial(self.client.get_latest_metrics, system_id),
        )

    async def get_latest_containers(
        self, system_id: str, *, ttl: float
    ) -> list[ContainerStats]:
        return await self._read(
            ("latest_containers", system_id),
            ttl,
            partial(self.client.get_latest_containers, system_id),
        )

    async def get_history(
        self, system_id: str, history_range: HistoryRange, *, ttl: float
    ) -> list[SystemMetrics]:
        return await self._read(
            ("history", system_id, history_range),
            ttl,
            partial(self.client.get_history, system_id, history_range),
        )

    async def get_container_history(
        self, system_id: str, history_range: HistoryRange, *, ttl: float
    ) -> list[ContainerHistoryMetrics]:
        return await self._read(
            ("container_history", system_id, history_range),
            ttl,
            partial(self.client.get_container_history, system_id, history_range),
        )

    async def list_container_states(
        self, system_id: str, *, ttl: float
    ) -> list[ContainerState]:
        return await self._read(
            ("container_states", system_id),
            ttl,
            partial(self.client.list_container_states, system_id),
        )

    async def list_failed_services(
        self, system_id: str, *, ttl: float
    ) -> list[ServiceState]:
        return await self._read(
            ("failed_services", system_id),
            ttl,
            partial(self.client.list_failed_services, system_id),
        )

    async def list_failed_smart_devices(
        self, system_id: str, *, ttl: float
    ) -> list[SmartDeviceState]:
        return await self._read(
            ("failed_smart_devices", system_id),
            ttl,
            partial(self.client.list_failed_smart_devices, system_id),
        )

    async def list_storage_pools(
        self, system_id: str, *, ttl: float
    ) -> list[StoragePoolState]:
        return await self._read(
            ("storage_pools", system_id),
            ttl,
            partial(self.client.list_storage_pools, system_id),
        )
