"""Immutable backend-neutral documents produced by the presentation layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..formatters import StatusState

Color = tuple[int, int, int]


class ChartUnit(StrEnum):
    """Units understood by chart templates and axis formatters."""

    PERCENT = "percent"
    BYTES = "bytes"
    BYTES_PER_SECOND = "bytes_sec"
    WATTS = "watts"
    TEMPERATURE = "temp"
    RPM = "rpm"
    LOAD = "load"


@dataclass(frozen=True, slots=True)
class StatusBadge:
    state: StatusState
    label: str


@dataclass(frozen=True, slots=True)
class MetadataItem:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class DocumentHeader:
    title: str
    subtitle: str = ""
    status: StatusBadge | None = None
    metadata: tuple[MetadataItem, ...] = ()
    range_label: str = ""
    connection_address: str = ""
    uptime_text: str = ""
    os_text: str = ""
    cpu_text: str = ""
    memory_text: str = ""


@dataclass(frozen=True, slots=True)
class DocumentFooter:
    label: str


@dataclass(frozen=True, slots=True)
class ProgressMetric:
    label: str
    value: float | None
    value_text: str
    secondary_text: str = ""
    metric_key: str = "default"
    color: Color = (59, 130, 246)
    maximum: float = 100.0


@dataclass(frozen=True, slots=True)
class OverviewCard:
    name: str
    status: StatusBadge
    updated_text: str
    metadata: tuple[MetadataItem, ...]
    metrics: tuple[ProgressMetric, ...]
    agent_version: str | None = None


@dataclass(frozen=True, slots=True)
class OverviewDocument:
    header: DocumentHeader
    cards: tuple[OverviewCard, ...]
    online_count: int
    offline_count: int
    page_number: int
    page_count: int
    footer: DocumentFooter


@dataclass(frozen=True, slots=True)
class DetailRow:
    label: str
    value: str
    percent: float | None = None
    color: Color | None = None


@dataclass(frozen=True, slots=True)
class DetailSection:
    title: str
    rows: tuple[DetailRow, ...]


@dataclass(frozen=True, slots=True)
class ContainerRow:
    name: str
    cpu_text: str
    memory_text: str


@dataclass(frozen=True, slots=True)
class StatusDocument:
    header: DocumentHeader
    metric_cards: tuple[ProgressMetric, ...]
    sections: tuple[DetailSection, ...]
    containers: tuple[ContainerRow, ...]
    footer: DocumentFooter


@dataclass(frozen=True, slots=True)
class ChartPoint:
    created: datetime
    value: float


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str
    unit: ChartUnit
    color: Color
    points: tuple[ChartPoint, ...]
    segments: tuple[tuple[ChartPoint, ...], ...]
    current_value: float | None
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class HistoryChartCard:
    title: str
    subtitle: str
    unit: ChartUnit
    series: tuple[ChartSeries, ...]
    axis_min: float
    axis_max: float
    maximum_override: float | None = None
    extra_series_count: int = 0


@dataclass(frozen=True, slots=True)
class HistoryDocument:
    header: DocumentHeader
    cards: tuple[HistoryChartCard, ...]
    sample_count: int
    has_gaps: bool
    footer: DocumentFooter
