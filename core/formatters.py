"""Shared status classification, timezone, and system-list text formatting."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo

from .beszel.models import SystemSummary

StatusState = Literal["up", "down", "unknown"]


def status_state(status: str | None) -> StatusState:
    """Classify any Beszel status string into the shared three-state semantics.

    Every view (list, overview, status, history) must use this single
    classification: ``up``/``online`` are online, ``down``/``offline`` are
    offline, and empty or unrecognized values are unknown.
    """
    state = (status or "").casefold()
    if state == "up":
        return "up"
    if state == "down":
        return "down"
    return "unknown"


def _status_marker(status: str) -> str:
    return {"up": "🟢", "down": "🔴", "unknown": "🟠"}[status_state(status)]


def resolve_timezone(name: str) -> tzinfo | None:
    """Resolve the configured IANA display timezone.

    ``None`` stands for the host local timezone. ``astimezone(None)`` applies
    the local rules for each instant, whereas a fixed offset captured at
    startup would be off by an hour after a daylight saving change.

    Args:
        name: IANA timezone name, or an empty string for the host timezone.

    Returns:
        The named zone, or ``None`` for host local time.
    """
    return ZoneInfo(name) if name else None


def format_datetime(value: datetime, timezone: tzinfo | None) -> str:
    """Format an instant in the configured display timezone without an offset."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M")


def format_system_list(systems: list[SystemSummary], *, timezone: tzinfo | None) -> str:
    if not systems:
        return "📋 Beszel 当前未发现任何受监控的探针节点"
    lines = [f"📋 Beszel 探针列表（共 {len(systems)} 台）："]
    for system in systems:
        line = f"{_status_marker(system.status)} {system.name}"
        if status_state(system.status) == "down":
            updated = (
                format_datetime(system.updated, timezone) if system.updated else "未知"
            )
            line += f"（上次在线：{updated}）"
        lines.append(line)
    return "\n".join(lines)
