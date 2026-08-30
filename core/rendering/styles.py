"""Deterministic presentation colors and rendering dimensions."""

from __future__ import annotations

from .models import Color

RENDER_WIDTH = 1200
STATUS_RENDER_WIDTH = 800

# Beszel Hub chart palette matching --chart-1 through --chart-5 and secondary accents
SERIES_PALETTE: tuple[Color, ...] = (
    (37, 99, 235),  # Chart 1: Blue (hsl(220, 70%, 50%))
    (16, 185, 129),  # Chart 2: Emerald (hsl(160, 60%, 45%))
    (245, 158, 11),  # Chart 3: Amber (hsl(30, 80%, 55%))
    (168, 85, 247),  # Chart 4: Purple (hsl(280, 65%, 60%))
    (244, 63, 94),  # Chart 5: Rose (hsl(340, 75%, 55%))
    (6, 182, 212),  # Cyan
    (20, 184, 166),  # Teal
    (249, 115, 22),  # Orange
)

# Semantic colors matching Beszel Hub dashboard metrics
METRIC_COLORS: dict[str, Color] = {
    "cpu": (37, 99, 235),  # Blue
    "mem": (16, 185, 129),  # Emerald
    "disk": (168, 85, 247),  # Purple
    "net": (16, 185, 129),  # Emerald (Rx) / Rose (Tx)
    "temp": (244, 63, 94),  # Rose
    "gpu": (37, 99, 235),  # Blue
    "fan": (20, 184, 166),  # Teal
    "battery": (132, 204, 22),  # Lime
    "swap": (16, 185, 129),  # Emerald
    "load": (249, 115, 22),  # Orange
}

# Progress meter threshold colors (from Beszel getMeterStateByThresholds)
COLOR_GOOD: Color = (34, 197, 94)  # < 65% : Green-500
COLOR_WARN: Color = (245, 158, 11)  # 65% - 90% : Amber-500
COLOR_CRIT: Color = (239, 68, 68)  # >= 90% : Red-500
COLOR_PAUSED: Color = (148, 163, 184)  # Offline / Paused : Slate-400


def threshold_color(value: float | None, status: str) -> Color:
    """Return Beszel threshold status color based on usage percent."""
    if status.casefold() in {"down", "offline", "paused", "maintenance"}:
        return COLOR_PAUSED
    if value is None:
        return COLOR_GOOD
    if value >= 90.0:
        return COLOR_CRIT
    if value >= 65.0:
        return COLOR_WARN
    return COLOR_GOOD


def metric_color(metric_key: str) -> Color:
    normalized = metric_key.casefold()
    return METRIC_COLORS.get(normalized, SERIES_PALETTE[0])


def series_color(index: int, *, metric_key: str) -> Color:
    """Return a stable color for an ordered series set."""
    normalized = metric_key.casefold()
    # These paired palettes preserve the semantic distinction between
    # related series (for example Rx/Tx and read/write) across charts.
    if normalized == "mem":
        mem_palette = ((16, 185, 129), (13, 148, 136), (110, 231, 183))
        return mem_palette[index % len(mem_palette)]
    if normalized == "net":
        return (16, 185, 129) if index == 0 else (244, 63, 94)
    if normalized == "disk_io":
        return (37, 99, 235) if index == 0 else (245, 158, 11)
    if normalized == "load":
        load_palette = ((249, 115, 22), (37, 99, 235), (168, 85, 247))
        return load_palette[index % len(load_palette)]
    if normalized in METRIC_COLORS and index == 0:
        return METRIC_COLORS[normalized]
    return SERIES_PALETTE[index % len(SERIES_PALETTE)]
