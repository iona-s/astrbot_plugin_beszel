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
    if state in {"up", "online"}:
        return "up"
    if state in {"down", "offline"}:
        return "down"
    return "unknown"


def _status_marker(status: str) -> str:
    return {"up": "🟢", "down": "🔴", "unknown": "🟠"}[status_state(status)]


def resolve_timezone(name: str) -> tzinfo:
    """Resolve an IANA timezone, falling back to the host local timezone."""
    return ZoneInfo(name) if name else datetime.now().astimezone().tzinfo or UTC


def format_datetime(value: datetime, timezone: tzinfo, *, seconds: bool = False) -> str:
    """Format an instant in the configured display timezone without an offset."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=UTC)
    pattern = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
    return value.astimezone(timezone).strftime(pattern)


def format_clock(value: datetime, timezone: tzinfo) -> str:
    """Format an instant as a local ``HH:MM`` clock value."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).strftime("%H:%M")


def format_system_list(
    systems: list[SystemSummary], *, timezone: tzinfo | None = None
) -> str:
    if not systems:
        return "Beszel 当前没有可见探针"
    timezone = timezone or resolve_timezone("")
    lines = ["Beszel 探针列表"]
    for system in systems:
        line = f"{_status_marker(system.status)} {system.name}"
        if status_state(system.status) == "down":
            updated = (
                format_datetime(system.updated, timezone) if system.updated else "未知"
            )
            line += f" 上次在线时间：{updated}"
        lines.append(line)
    return "\n".join(lines)
