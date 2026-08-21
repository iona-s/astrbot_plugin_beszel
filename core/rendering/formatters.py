"""Human-readable value formatting for rendered views."""

from __future__ import annotations

import math


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


def number(value: object, suffix: str = "") -> str:
    if value is None or isinstance(value, bool):
        return "N/A"
    try:
        value_float = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(value_float):
        return "N/A"
    return f"{value_float:.1f}{suffix}"


def percent(value: object) -> str:
    return number(value, "%")


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


def uptime(value: object) -> str:
    try:
        seconds = max(0, int(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return "N/A"
    units = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
    parts: list[str] = []
    for label, size in units:
        count, seconds = divmod(seconds, size)
        if count and len(parts) < 2:
            parts.append(f"{count}{label}")
    return " ".join(parts) or "0s"


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


def gb_iec(value: object) -> str:
    if value is None or isinstance(value, bool):
        return "N/A"
    try:
        gb = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if gb >= 100_000:
        return bytes_iec(gb)
    return bytes_iec(gb * (1024**3))


def mb_iec(value: object) -> str:
    if value is None or isinstance(value, bool):
        return "N/A"
    try:
        mb = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return bytes_iec(mb * (1024**2))


def format_bandwidth(value: object) -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        sent = safe_float(value[0])
        recv = safe_float(value[1])
        if sent is not None and recv is not None:
            return f"↓ {bytes_iec(recv)}/s  ↑ {bytes_iec(sent)}/s"
        return "N/A"
    scalar = safe_float(value)
    if scalar is not None:
        return f"{bytes_iec(scalar)}/s"
    return "N/A"
