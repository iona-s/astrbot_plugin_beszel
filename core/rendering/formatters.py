"""Human-readable value formatting for rendered views."""

from __future__ import annotations

import math

from .models import ChartUnit


def safe_float(value: object) -> float | None:
    """Convert an optional upstream metric to a finite float, or None.

    Beszel optional fields may arrive as strings, containers, NaN, or
    infinity; only convertible finite values are accepted. Booleans are
    rejected because they are never valid metrics.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def percent(value: object) -> str:
    numeric = safe_float(value)
    return "N/A" if numeric is None else f"{numeric:.1f}%"


def bytes_iec(value: object) -> str:
    current = safe_float(value)
    if current is None:
        return "N/A"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while abs(current) >= 1024 and index < len(units) - 1:
        current /= 1024
        index += 1
    return f"{current:.1f} {units[index]}"


def uptime_cn(value: object) -> str:
    try:
        seconds = max(0, int(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return "N/A"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        if hours > 0:
            return f"{days} 天 {hours} 小时"
        return f"{days} 天"
    if hours > 0:
        if mins > 0:
            return f"{hours} 小时 {mins} 分钟"
        return f"{hours} 小时"
    if mins > 0:
        return f"{mins} 分钟"
    return f"{secs} 秒"


def gb_to_bytes(value: float) -> float:
    """Convert a Beszel GiB capacity field to bytes."""
    return value * (1024**3)


def gb_iec(value: object) -> str:
    gb = safe_float(value)
    return "N/A" if gb is None else bytes_iec(gb_to_bytes(gb))


def mb_iec(value: object) -> str:
    mb = safe_float(value)
    return "N/A" if mb is None else bytes_iec(mb * (1024**2))


def format_bandwidth(value: object) -> str:
    scalar = safe_float(value)
    if scalar is not None:
        return f"{bytes_iec(scalar)}/s"
    return "N/A"


def format_bytes_clean(value: float) -> str:
    """Format byte amounts cleanly (e.g. 24 GB, 743.7 KB) matching Beszel Hub."""
    if value <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    current = float(value)
    index = 0
    while current >= 1000 and index < len(units) - 1:
        current /= 1024
        index += 1
    if abs(current - round(current)) < 0.05:
        return f"{round(current)} {units[index]}"
    if current >= 100:
        return f"{current:.0f} {units[index]}"
    return f"{current:.1f} {units[index]}"


def format_chart_value(value: object, unit: ChartUnit) -> str:
    """Format one normalized chart value for an axis label."""
    numeric = safe_float(value)
    if numeric is None:
        return "N/A"
    if unit is ChartUnit.PERCENT:
        if abs(numeric - round(numeric)) < 0.05:
            return f"{round(numeric)}%"
        return f"{numeric:.1f}%"
    if unit is ChartUnit.BYTES:
        return format_bytes_clean(numeric)
    if unit is ChartUnit.BYTES_PER_SECOND:
        return f"{format_bytes_clean(numeric)}/s"
    if unit is ChartUnit.WATTS:
        return f"{round(numeric)}W"
    if unit is ChartUnit.TEMPERATURE:
        if abs(numeric - round(numeric)) < 0.05:
            return f"{round(numeric)} °C"
        return f"{numeric:.1f} °C"
    if unit is ChartUnit.RPM:
        return f"{round(numeric)} RPM"
    # ChartUnit.LOAD is the only remaining unit.
    return f"{numeric:.2f}"
