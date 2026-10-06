"""Numeric chart geometry for history documents.

Pytakumi renders no SVG ``<text>``, and a card background covers absolutely
positioned descendants, so templates lay axis labels out in normal flow
beside and below the SVG. This module only computes coordinates and paths.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from ..formatters import format_chart_value
from ..models import ChartPoint, ChartUnit, Color, HistoryChartCard

# The 520px card content box splits between a y-axis label column (sized per
# chart from its widest label) and the SVG; both widths travel in the geometry.
CONTENT_WIDTH = 520
CHART_HEIGHT = 148
# Small plot inset so stroked shapes never touch the SVG edge.
PLOT_LEFT = 6
PLOT_RIGHT_INSET = 6
PLOT_TOP = 12
PLOT_BOTTOM = 142

# Gap between the longest y label and the SVG, and column bounds; beyond the
# maximum, extreme labels clip via overflow:hidden instead of squeezing the plot.
Y_AXIS_GAP = 8
Y_AXIS_MIN = 24
Y_AXIS_MAX = 64

# Fixed tick label box width; must match .chart-tick-label in beszel.css.
TICK_LABEL_WIDTH = 32
# Minimum space between tick labels, matching the Hub's recharts ``minTickGap``.
TICK_MIN_GAP = 12
# d3 timeTicks intervals from one minute to two days: (unit, step, seconds).
TICK_INTERVALS = (
    ("minute", 1, 60),
    ("minute", 5, 5 * 60),
    ("minute", 15, 15 * 60),
    ("minute", 30, 30 * 60),
    ("hour", 1, 3600),
    ("hour", 3, 3 * 3600),
    ("hour", 6, 6 * 3600),
    ("hour", 12, 12 * 3600),
    ("day", 1, 86400),
    ("day", 2, 2 * 86400),
)
# Label steps per unit that stay aligned with the next larger calendar unit.
LABEL_STEPS = {
    "minute": (1, 5, 10, 15, 20, 30, 60),
    "hour": (1, 2, 3, 4, 6, 8, 12, 24),
    "day": (1, 2, 3, 4, 5, 7, 10, 15),
}
UNIT_SECONDS = {"minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True, slots=True)
class ChartGridLine:
    y: float
    label: str


@dataclass(frozen=True, slots=True)
class ChartMarker:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class ChartSegmentGeometry:
    color: Color
    area_path: str | None
    line_path: str | None
    markers: tuple[ChartMarker, ...]
    grad_id: str


@dataclass(frozen=True, slots=True)
class ChartTick:
    label: str
    margin_left: float


@dataclass(frozen=True, slots=True)
class ChartGeometry:
    width: int
    height: int
    y_axis_width: int
    plot_left: int
    plot_right: int
    plot_bottom: int
    grid_lines: tuple[ChartGridLine, ...]
    segments: tuple[ChartSegmentGeometry, ...]
    ticks: tuple[ChartTick, ...]
    tick_marks: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ChartTemplateView:
    card: HistoryChartCard
    current_label: str
    geometry: ChartGeometry | None


def build_chart_view(
    card: HistoryChartCard, *, timezone: tzinfo | None
) -> ChartTemplateView:
    """Prepare numeric SVG geometry without generating markup.

    The x axis spans the card's time window, so a series that starts late or
    stops early only occupies its part of the plot. A card without a window
    falls back to the span of its own points.

    Args:
        card: Presentation card to lay out.
        timezone: Display timezone for tick labels; ``None`` is host local time.

    Returns:
        The card with its geometry, or without geometry when it has no points.
    """
    current = card.series[0].current_value if card.series else None
    points = [point for series in card.series for point in series.points]
    if not points:
        return ChartTemplateView(
            card=card,
            current_label=format_chart_value(current, card.unit),
            geometry=None,
        )

    window_start = (
        _timestamp(card.time_start)
        if card.time_start is not None
        else min(_timestamp(point.created) for point in points)
    )
    window_end = (
        _timestamp(card.time_end)
        if card.time_end is not None
        else max(_timestamp(point.created) for point in points)
    )
    time_span = max(1.0, window_end - window_start)
    plot_height = PLOT_BOTTOM - PLOT_TOP

    grid_lines = tuple(
        ChartGridLine(
            y=PLOT_BOTTOM - plot_height * (index / 3),
            label=format_chart_value(
                card.axis_min + (card.axis_max - card.axis_min) * (index / 3),
                card.unit,
            ),
        )
        for index in range(4)
    )
    y_axis_width = _y_axis_width(line.label for line in grid_lines)
    width = CONTENT_WIDTH - y_axis_width
    plot_width = width - PLOT_LEFT - PLOT_RIGHT_INSET

    segments: list[ChartSegmentGeometry] = []
    for series_index, series in enumerate(card.series):
        for segment_index, segment in enumerate(series.segments):
            coords = [
                _point_xy(
                    point,
                    window_start,
                    time_span,
                    card.axis_min,
                    card.axis_max,
                    plot_width,
                    plot_height,
                )
                for point in segment
            ]
            if not coords:
                continue
            line_path = _path(coords) if len(coords) > 1 else None
            area_path = (
                f"M {coords[0][0]:.2f} {PLOT_BOTTOM:.2f} "
                f"L {line_path[2:]} "
                f"L {coords[-1][0]:.2f} {PLOT_BOTTOM:.2f} Z"
                if (line_path is not None and card.unit != ChartUnit.TEMPERATURE)
                else None
            )
            markers = (ChartMarker(*coords[0]),) if len(coords) == 1 else ()
            segments.append(
                ChartSegmentGeometry(
                    color=series.color,
                    area_path=area_path,
                    line_path=line_path,
                    markers=markers,
                    grad_id=f"grad-{series_index}-{segment_index}",
                )
            )

    ticks, tick_marks = _ticks(
        window_start=window_start,
        time_span=time_span,
        plot_width=plot_width,
        timezone=timezone,
    )

    return ChartTemplateView(
        card=card,
        current_label=format_chart_value(current, card.unit),
        geometry=ChartGeometry(
            width=width,
            height=CHART_HEIGHT,
            y_axis_width=y_axis_width,
            plot_left=PLOT_LEFT,
            plot_right=width - PLOT_RIGHT_INSET,
            plot_bottom=PLOT_BOTTOM,
            grid_lines=grid_lines,
            segments=tuple(segments),
            ticks=ticks,
            tick_marks=tick_marks,
        ),
    )


def _y_axis_width(labels: Iterable[str]) -> int:
    """Size the label column to its widest label."""
    widest = max((_estimate_width(label) for label in labels), default=0.0)
    return min(Y_AXIS_MAX, max(Y_AXIS_MIN, math.ceil(widest) + Y_AXIS_GAP))


def _estimate_width(text: str) -> float:
    """Estimate the 11px Noto Sans SC advance width without font metrics."""
    width = 0.0
    for char in text:
        if char.isascii() and char.isdigit():
            width += 6.6
        elif char == " ":
            width += 2.2
        elif char in ".-":
            width += 3.3
        elif char in "/°":
            width += 4.4
        elif char in "%W":
            width += 9.9
        elif char.isascii():
            width += 7.2
        else:
            width += 11.0
    return width


def _ticks(
    *,
    window_start: float,
    time_span: float,
    plot_width: int,
    timezone: tzinfo | None,
) -> tuple[tuple[ChartTick, ...], tuple[float, ...]]:
    """Place ticks on round local times, like the Hub's d3 ``timeTicks``.

    The Hub asks for 12 ticks on spans up to two days, 7 on a week and 30 on a
    month; the interval closest by ratio to ``span / count`` wins, and ticks
    fall where the local minute, hour, or day of month is a multiple of its
    step. Every tick gets a mark. A static card is narrower than the Hub chart,
    so labels use the smallest round multiple of the step that keeps
    ``TICK_MIN_GAP`` between them (e.g. a 10-minute label on every second
    5-minute mark); labels crossing the plot edges are dropped, and irregular
    gaps such as month ends drop the earlier of two crowded labels. Each
    fixed-width label box carries the ``margin_left`` gap from the previous
    box's right edge, reproducing absolute positions in normal flow.

    Returns:
        The labels in layout order and the x position of every tick mark.
    """
    window_end = window_start + time_span
    count = 30 if time_span > 14 * 86400 else 7 if time_span > 2 * 86400 else 12
    target = time_span / count
    index = min(
        max(bisect_right(TICK_INTERVALS, target, key=lambda item: item[2]), 1),
        len(TICK_INTERVALS) - 1,
    )
    if target / TICK_INTERVALS[index - 1][2] < TICK_INTERVALS[index][2] / target:
        index -= 1
    unit, step, _ = TICK_INTERVALS[index]
    min_spacing = (TICK_LABEL_WIDTH + TICK_MIN_GAP) * time_span / plot_width
    label_step = next(
        (
            candidate
            for candidate in LABEL_STEPS[unit]
            if candidate % step == 0 and candidate * UNIT_SECONDS[unit] >= min_spacing
        ),
        LABEL_STEPS[unit][-1],
    )

    wall = (
        datetime.fromtimestamp(window_start, UTC)
        .astimezone(timezone)
        .replace(tzinfo=None, second=0, microsecond=0)
    )
    if unit != "minute":
        wall = wall.replace(minute=0)
    if unit == "day":
        wall = wall.replace(hour=0)
    end_wall = (
        datetime.fromtimestamp(window_end, UTC)
        .astimezone(timezone)
        .replace(tzinfo=None)
    )
    increment = {
        "minute": timedelta(minutes=1),
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
    }[unit]
    plot_right = PLOT_LEFT + plot_width
    boxes: list[tuple[float, str]] = []
    marks: list[float] = []
    previous = -math.inf
    while wall <= end_wall:
        field = (
            wall.minute
            if unit == "minute"
            else wall.hour
            if unit == "hour"
            else wall.day - 1
        )
        # A naive wall time is host local time for timestamp(), so a None
        # timezone applies the host rules of each tick's own instant. Wall times
        # inside a DST gap resolve to instants that later wall times repeat.
        instant = wall.replace(tzinfo=timezone).timestamp()
        wall += increment
        if (
            field % step
            or not window_start <= instant <= window_end
            or instant <= previous
        ):
            continue
        previous = instant
        x = PLOT_LEFT + (instant - window_start) / time_span * plot_width
        marks.append(x)
        left = x - TICK_LABEL_WIDTH / 2
        if (
            field % label_step == 0
            and left >= PLOT_LEFT
            and left + TICK_LABEL_WIDTH <= plot_right
        ):
            boxes.append((left, _time_label(instant, timezone, time_span)))

    kept: list[tuple[float, str]] = []
    for box in reversed(boxes):
        if not kept or kept[-1][0] - box[0] >= TICK_LABEL_WIDTH + TICK_MIN_GAP:
            kept.append(box)
    kept.reverse()

    ticks: list[ChartTick] = []
    current_right = 0.0
    for left, label in kept:
        ticks.append(ChartTick(label=label, margin_left=left - current_right))
        current_right = left + TICK_LABEL_WIDTH
    return tuple(ticks), tuple(marks)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _time_label(timestamp: float, timezone: tzinfo | None, time_span: float) -> str:
    local_dt = datetime.fromtimestamp(timestamp, UTC).astimezone(timezone)
    return local_dt.strftime("%m-%d" if time_span > 86400 * 2 else "%H:%M")


def _point_xy(
    point: ChartPoint,
    window_start: float,
    time_span: float,
    axis_min: float,
    axis_max: float,
    plot_width: int,
    plot_height: float,
) -> tuple[float, float]:
    x = PLOT_LEFT + (_timestamp(point.created) - window_start) / time_span * plot_width
    ratio = (
        (point.value - axis_min) / (axis_max - axis_min) if axis_max > axis_min else 0.0
    )
    y = PLOT_BOTTOM - min(1.0, max(0.0, ratio)) * plot_height
    return x, y


def _path(coords: list[tuple[float, float]]) -> str:
    """Build a smooth SVG path through the points.

    Fritsch–Carlson monotone cubic: harmonic-mean tangents clamped to zero
    at extrema, so the curve never overshoots (Beszel Hub chart smoothing).
    The caller guarantees at least two coordinates.
    """
    n = len(coords)
    if n == 2:
        return f"M {coords[0][0]:.2f} {coords[0][1]:.2f} L {coords[1][0]:.2f} {coords[1][1]:.2f}"

    dxs: list[float] = []
    ms: list[float] = []
    for i in range(n - 1):
        dx = coords[i + 1][0] - coords[i][0]
        dy = coords[i + 1][1] - coords[i][1]
        dxs.append(dx)
        ms.append(dy / dx if dx != 0 else 0.0)

    tangents: list[float] = [ms[0]]
    for i in range(1, n - 1):
        m0 = ms[i - 1]
        m1 = ms[i]
        if m0 * m1 <= 0:
            tangents.append(0.0)
        else:
            tangents.append(2.0 * m0 * m1 / (m0 + m1))
    tangents.append(ms[-1])

    path_parts = [f"M {coords[0][0]:.2f} {coords[0][1]:.2f}"]
    for i in range(n - 1):
        x0, y0 = coords[i]
        x1, y1 = coords[i + 1]
        dx = dxs[i]
        t0 = tangents[i]
        t1 = tangents[i + 1]

        cp1_x = x0 + dx / 3.0
        cp1_y = y0 + t0 * dx / 3.0
        cp2_x = x1 - dx / 3.0
        cp2_y = y1 - t1 * dx / 3.0

        path_parts.append(
            f"C {cp1_x:.2f} {cp1_y:.2f}, {cp2_x:.2f} {cp2_y:.2f}, {x1:.2f} {y1:.2f}"
        )

    return " ".join(path_parts)
