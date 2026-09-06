"""Numeric chart geometry for history documents.

Pytakumi renders no SVG ``<text>``, and a card background covers absolutely
positioned descendants, so templates lay axis labels out in normal flow
beside and below the SVG. This module only computes coordinates and paths.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

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
TICK_LABEL_WIDTH = 60


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
    anchor: str
    margin_left: float


@dataclass(frozen=True, slots=True)
class ChartGeometry:
    width: int
    height: int
    y_axis_width: int
    plot_left: int
    plot_right: int
    grid_lines: tuple[ChartGridLine, ...]
    segments: tuple[ChartSegmentGeometry, ...]
    ticks: tuple[ChartTick, ...]


@dataclass(frozen=True, slots=True)
class ChartTemplateView:
    card: HistoryChartCard
    current_label: str
    geometry: ChartGeometry | None


def build_chart_view(card: HistoryChartCard, *, timezone: tzinfo) -> ChartTemplateView:
    """Prepare numeric SVG geometry without generating markup."""
    current = card.series[0].current_value if card.series else None
    points = [point for series in card.series for point in series.points]
    if not points:
        return ChartTemplateView(
            card=card,
            current_label=format_chart_value(current, card.unit),
            geometry=None,
        )

    first_timestamp = min(_timestamp(point.created) for point in points)
    last_timestamp = max(_timestamp(point.created) for point in points)
    time_span = max(1.0, last_timestamp - first_timestamp)
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
                    first_timestamp,
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

    tick_points = tuple(sorted(points, key=lambda point: point.created))
    ticks = _ticks(
        tick_points,
        first_timestamp=first_timestamp,
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
            grid_lines=grid_lines,
            segments=tuple(segments),
            ticks=ticks,
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
    points: tuple[ChartPoint, ...],
    *,
    first_timestamp: float,
    time_span: float,
    plot_width: int,
    timezone: tzinfo,
) -> tuple[ChartTick, ...]:
    """Place up to five tick labels at their data x positions.

    Each fixed-width box carries the ``margin_left`` gap from the previous
    box's right edge, reproducing absolute positions in normal flow.
    """
    if not points:
        return ()

    # Deduplicate points by timestamp to avoid collision on duplicate sample times
    unique_points: list[ChartPoint] = []
    seen_ts: set[float] = set()
    for pt in points:
        ts = _timestamp(pt.created)
        if ts not in seen_ts:
            seen_ts.add(ts)
            unique_points.append(pt)

    tick_count = min(5, len(unique_points))
    if tick_count == 0:
        return ()

    if tick_count == 1:
        pt = unique_points[0]
        x = PLOT_LEFT + (
            (_timestamp(pt.created) - first_timestamp) / time_span * plot_width
        )
        return (
            ChartTick(
                label=_time_label(pt.created, timezone, time_span=time_span),
                anchor="start",
                margin_left=x,
            ),
        )

    # First tick: left-aligned to first data point
    pt_first = unique_points[0]
    x_first = PLOT_LEFT + (
        (_timestamp(pt_first.created) - first_timestamp) / time_span * plot_width
    )
    first_tick = ChartTick(
        label=_time_label(pt_first.created, timezone, time_span=time_span),
        anchor="start",
        margin_left=x_first,
    )
    first_right = x_first + TICK_LABEL_WIDTH

    # Last tick: right-aligned to last data point
    pt_last = unique_points[-1]
    x_last = PLOT_LEFT + (
        (_timestamp(pt_last.created) - first_timestamp) / time_span * plot_width
    )
    last_left = x_last - TICK_LABEL_WIDTH
    last_right = x_last
    last_label = _time_label(pt_last.created, timezone, time_span=time_span)

    accepted: list[tuple[ChartTick, float, float]] = [
        (first_tick, x_first, first_right)
    ]
    current_right = first_right

    # Intermediate ticks: center-aligned, added only if they fit between
    # the preceding accepted tick and the last tick without overlap
    if tick_count > 2 and last_left >= first_right:
        for index in range(1, tick_count - 1):
            point_index = round(
                index * (len(unique_points) - 1) / max(1, tick_count - 1)
            )
            pt = unique_points[point_index]
            x = PLOT_LEFT + (
                (_timestamp(pt.created) - first_timestamp) / time_span * plot_width
            )
            cand_left = x - TICK_LABEL_WIDTH / 2
            cand_right = cand_left + TICK_LABEL_WIDTH
            if (
                cand_left >= current_right
                and cand_right <= last_left
                and cand_left >= PLOT_LEFT
            ):
                tick = ChartTick(
                    label=_time_label(pt.created, timezone, time_span=time_span),
                    anchor="middle",
                    margin_left=max(0.0, cand_left - current_right),
                )
                accepted.append((tick, cand_left, cand_right))
                current_right = cand_right

    # Always include the last tick if it does not overlap the preceding tick
    if last_left >= current_right:
        last_tick = ChartTick(
            label=last_label,
            anchor="end",
            margin_left=max(0.0, last_left - current_right),
        )
        accepted.append((last_tick, last_left, last_right))

    return tuple(t[0] for t in accepted)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _time_label(value: datetime, timezone: tzinfo, time_span: float) -> str:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=UTC)
    local_dt = value.astimezone(timezone)
    if time_span > 86400 * 2:
        return local_dt.strftime("%m-%d")
    return local_dt.strftime("%H:%M")


def _point_xy(
    point: ChartPoint,
    first_timestamp: float,
    time_span: float,
    axis_min: float,
    axis_max: float,
    plot_width: int,
    plot_height: float,
) -> tuple[float, float]:
    x = (
        PLOT_LEFT
        + (_timestamp(point.created) - first_timestamp) / time_span * plot_width
    )
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
