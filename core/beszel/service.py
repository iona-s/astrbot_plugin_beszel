"""Query orchestration independent of AstrBot events and rendering."""

from __future__ import annotations

from astrbot.api import logger

from ..errors import AmbiguousSystemError, SystemNotFoundError
from ..formatters import status_state
from .client import BeszelClient
from .models import (
    HistoryRange,
    SystemDetailView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemSummary,
)


class QueryService:
    """Provide the four read-only query operations used by every entrypoint."""

    def __init__(
        self, client: BeszelClient, *, default_history_range: HistoryRange
    ) -> None:
        self.client = client
        self.default_history_range = default_history_range

    async def list_systems(self) -> list[SystemSummary]:
        systems = await self.client.list_systems()
        sorted_systems = sorted(systems, key=self._sort_key)
        logger.debug(
            "QueryService.list_systems: retrieved %d systems: %s",
            len(sorted_systems),
            [(s.name, s.id, s.status) for s in sorted_systems],
        )
        return sorted_systems

    async def get_overview(self) -> list[SystemSummary]:
        systems = await self.client.list_systems()
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

    async def get_system_detail(self, selector: str) -> SystemDetailView:
        logger.debug("QueryService.get_system_detail: selector=%s", selector)
        systems = await self.list_systems()
        system = self.select_system(systems, selector)
        logger.debug(
            "QueryService.get_system_detail: selected system %s (id=%s)",
            system.name,
            system.id,
        )
        details = await self.client.get_system_details(system.id)
        metrics = await self.client.get_latest_metrics(system.id)
        containers = await self.client.get_latest_containers(system.id)
        logger.debug(
            "QueryService.get_system_detail: id=%s details=%s metrics=%s containers=%d",
            system.id,
            details,
            metrics,
            len(containers),
        )
        return SystemDetailView(
            summary=system,
            details=details,
            metrics=metrics,
            containers=containers,
        )

    async def get_system_history(
        self,
        selector: str,
        history_range: HistoryRange | str | None = None,
    ) -> SystemHistoryView:
        logger.debug(
            "QueryService.get_system_history: selector=%s, range=%s",
            selector,
            history_range,
        )
        systems = await self.list_systems()
        system = self.select_system(systems, selector)
        parsed_range = (
            self.default_history_range
            if history_range is None
            else HistoryRange.parse(history_range)
        )
        logger.debug(
            "QueryService.get_system_history: selected system %s (id=%s), parsed_range=%s",
            system.name,
            system.id,
            parsed_range.value,
        )
        metrics = await self.client.get_history(system.id, parsed_range)
        points = [
            SystemHistoryPoint(created=point.created, stats=point.stats)
            for point in metrics
            if point.created is not None
        ]
        logger.debug(
            "QueryService.get_system_history: id=%s range=%s returned %d points",
            system.id,
            parsed_range.value,
            len(points),
        )
        system_details = None
        try:
            system_details = await self.client.get_system_details(system.id)
        except Exception:
            logger.debug(
                "QueryService.get_system_history: optional details query skipped for id=%s",
                system.id,
            )

        return SystemHistoryView(
            summary=system,
            range=parsed_range,
            points=points,
            details=system_details,
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
