"""Query orchestration independent of AstrBot events and rendering."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from astrbot.api import logger

from ..errors import AmbiguousSystemError, SystemNotFoundError
from ..formatters import status_state
from .client import BeszelClient
from .hub_data import HubData
from .models import (
    ContainerHistoryPoint,
    HistoryRange,
    SystemDetails,
    SystemDetailView,
    SystemHealthView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemSummary,
)


class QueryService:
    """Provide the four read-only query operations used by every entrypoint."""

    def __init__(
        self,
        client: BeszelClient,
        *,
        default_history_range: HistoryRange,
        system_list_cache_ttl: float = 60.0,
        hardware_cache_ttl: float = 600.0,
    ) -> None:
        """Create the service over one Hub client.

        Args:
            client: Hub client wrapped by the shared ``HubData`` layer.
            default_history_range: Range used when a query names none.
            system_list_cache_ttl: Seconds a system list may be reused to resolve selectors.
            hardware_cache_ttl: Seconds hardware details and SMART state may be
                reused by queries that allow caching (``ttl > 0``).
        """
        self.data = HubData(client)
        self.default_history_range = default_history_range
        self.system_list_cache_ttl = max(0.0, float(system_list_cache_ttl))
        self.hardware_cache_ttl = max(0.0, float(hardware_cache_ttl))

    async def close(self) -> None:
        await self.data.close()

    def _hardware_ttl(self, ttl: float) -> float:
        return self.hardware_cache_ttl if ttl > 0 else 0.0

    async def list_systems(self) -> list[SystemSummary]:
        """Return the system list used to resolve selectors, reused for ``system_list_cache_ttl``."""
        systems = sorted(
            await self.data.list_systems(ttl=self.system_list_cache_ttl),
            key=self._sort_key,
        )
        logger.debug(
            "QueryService.list_systems: %d systems (ttl=%.1fs): %s",
            len(systems),
            self.system_list_cache_ttl,
            [(s.name, s.id, s.status) for s in systems],
        )
        return systems

    async def get_overview(self, *, ttl: float = 0) -> list[SystemSummary]:
        systems = await self.data.list_systems(ttl=ttl)
        sorted_systems = sorted(
            systems,
            key=lambda system: (
                0 if status_state(system.status) == "up" else 1,
                system.name.casefold(),
            ),
        )
        logger.debug(
            "QueryService.get_overview: retrieved %d systems", len(sorted_systems)
        )
        return sorted_systems

    async def get_system_detail(
        self, selector: str, *, ttl: float = 0
    ) -> SystemDetailView:
        """Build the current status view for one system.

        The cached system list only resolves the selector; the summary is read
        with the caller's ``ttl``, so its status is never older than that.

        Args:
            selector: System ID, exact name, or unique partial name.
            ttl: Seconds Hub results may be reused; 0 always queries the Hub.

        Returns:
            The live summary with details, latest metrics, and containers.
        """
        logger.debug("QueryService.get_system_detail: selector=%s", selector)
        systems = await self.list_systems()
        system = self.select_system(systems, selector)
        logger.debug(
            "QueryService.get_system_detail: selected system %s (id=%s)",
            system.name,
            system.id,
        )
        summary, details, metrics, containers = await asyncio.gather(
            self.data.get_system(system.id, ttl=ttl),
            self.data.get_system_details(system.id, ttl=self._hardware_ttl(ttl)),
            self.data.get_latest_metrics(system.id, ttl=ttl),
            self.data.get_latest_containers(system.id, ttl=ttl),
        )
        logger.debug(
            "QueryService.get_system_detail: id=%s status=%s details=%s metrics=%s containers=%d",
            system.id,
            summary.status,
            details,
            metrics,
            len(containers),
        )
        return SystemDetailView(
            summary=summary,
            details=details,
            metrics=metrics,
            containers=containers,
        )

    async def get_system_health(
        self, system_id: str, *, ttl: float = 0
    ) -> SystemHealthView:
        """Read container states, failed services, and failed SMART disks.

        The three reads are independent: a failed read is logged and leaves its
        part ``None`` while the others are still returned.

        Args:
            system_id: Beszel system record ID.
            ttl: Seconds Hub results may be reused; 0 always queries the Hub.

        Returns:
            The health view of the system.
        """
        results = await asyncio.gather(
            self.data.list_container_states(system_id, ttl=ttl),
            self.data.list_failed_services(system_id, ttl=ttl),
            self.data.list_failed_smart_devices(system_id, ttl=self._hardware_ttl(ttl)),
            return_exceptions=True,
        )
        parts: list[list | None] = []
        for collection, result in zip(
            ("containers", "systemd_services", "smart_devices"), results, strict=True
        ):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                logger.warning(
                    "Beszel %s query failed for system_id=%s: %s",
                    collection,
                    system_id,
                    type(result).__name__,
                )
                parts.append(None)
            else:
                parts.append(result)
        return SystemHealthView(
            containers=parts[0],
            failed_services=parts[1],
            failed_smart_devices=parts[2],
        )

    async def get_system_history(
        self,
        selector: str,
        history_range: HistoryRange | str | None = None,
        *,
        ttl: float = 0,
    ) -> SystemHistoryView:
        """Build the history view for one system.

        The range is validated before any Hub request, and the cached system
        list only resolves the selector. Hardware details are optional
        enrichment, so their failure leaves ``details`` empty.

        Args:
            selector: System ID, exact name, or unique partial name.
            history_range: Requested range; ``None`` or blank uses the default.
            ttl: Seconds Hub results may be reused; 0 always queries the Hub.

        Returns:
            The live summary with system and container history points, and the
            query time that ends the chart window.

        Raises:
            InvalidHistoryRangeError: The range is not supported.
        """
        logger.debug(
            "QueryService.get_system_history: selector=%s, range=%s",
            selector,
            history_range,
        )
        if history_range is None or not history_range.strip():
            parsed_range = self.default_history_range
        else:
            parsed_range = HistoryRange.parse(history_range)
        systems = await self.list_systems()
        system = self.select_system(systems, selector)
        logger.debug(
            "QueryService.get_system_history: selected system %s (id=%s), parsed_range=%s",
            system.name,
            system.id,
            parsed_range.value,
        )

        async def optional_details() -> SystemDetails | None:
            try:
                return await self.data.get_system_details(
                    system.id, ttl=self._hardware_ttl(ttl)
                )
            except Exception:
                logger.debug(
                    "QueryService.get_system_history: optional details query skipped for id=%s",
                    system.id,
                )
                return None

        window_end = datetime.now(UTC)
        summary, metrics, container_metrics, system_details = await asyncio.gather(
            self.data.get_system(system.id, ttl=ttl),
            self.data.get_history(system.id, parsed_range, ttl=ttl),
            self.data.get_container_history(system.id, parsed_range, ttl=ttl),
            optional_details(),
        )
        points = [
            SystemHistoryPoint(created=point.created, stats=point.stats)
            for point in metrics
            if point.created is not None
        ]
        container_points = [
            ContainerHistoryPoint(created=point.created, stats=point.stats)
            for point in container_metrics
            if point.created is not None
        ]
        logger.debug(
            "QueryService.get_system_history: id=%s status=%s range=%s returned %d points and %d container points",
            system.id,
            summary.status,
            parsed_range.value,
            len(points),
            len(container_points),
        )
        return SystemHistoryView(
            summary=summary,
            range=parsed_range,
            points=points,
            container_points=container_points,
            details=system_details,
            window_end=window_end,
        )

    @staticmethod
    def select_system(systems: list[SystemSummary], selector: str) -> SystemSummary:
        needle = selector.strip().casefold()
        logger.debug(
            "QueryService.select_system: searching selector='%s' among %d systems",
            selector,
            len(systems),
        )
        exact_id = [system for system in systems if system.id.casefold() == needle]
        if exact_id:
            logger.debug(
                "QueryService.select_system: matched exact ID for selector '%s' -> %s (%s)",
                selector,
                exact_id[0].name,
                exact_id[0].id,
            )
            return exact_id[0]
        exact_name = [system for system in systems if system.name.casefold() == needle]
        if exact_name:
            chosen = QueryService._one_or_error(exact_name, selector)
            logger.debug(
                "QueryService.select_system: matched exact name for selector '%s' -> %s (%s)",
                selector,
                chosen.name,
                chosen.id,
            )
            return chosen
        partial = [
            system for system in systems if needle and needle in system.name.casefold()
        ]
        chosen = QueryService._one_or_error(partial, selector)
        logger.debug(
            "QueryService.select_system: matched partial name for selector '%s' -> %s (%s)",
            selector,
            chosen.name,
            chosen.id,
        )
        return chosen

    @staticmethod
    def _one_or_error(candidates: list[SystemSummary], selector: str) -> SystemSummary:
        if not candidates:
            raise SystemNotFoundError(
                f"🔍 未找到名为「{selector}」的探针节点，可发送 /beszel list 查看所有可用探针"
            )
        if len(candidates) > 1:
            labels = "、".join(f"{item.name} ({item.id[:8]})" for item in candidates)
            raise AmbiguousSystemError(
                f"⚠️ 匹配到多个名称相近的探针节点，请使用更详细名称或具体 ID 精准查询：\n{labels}"
            )
        return candidates[0]

    @staticmethod
    def _sort_key(system: SystemSummary) -> tuple[int, str]:
        priority = {"down": 0, "unknown": 1, "up": 2}[status_state(system.status)]
        return priority, system.name.casefold()
