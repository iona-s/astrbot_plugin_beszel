"""Pydantic DTOs and internal models for the Beszel REST API."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..errors import InvalidHistoryRangeError


class HistoryRange(StrEnum):
    ONE_HOUR = "1h"
    TWELVE_HOURS = "12h"
    ONE_DAY = "24h"
    ONE_WEEK = "1w"
    THIRTY_DAYS = "30d"

    @classmethod
    def parse(cls, value: str) -> HistoryRange:
        try:
            return cls(value.strip().casefold())
        except ValueError as exc:
            raise InvalidHistoryRangeError(
                "⚠️ 历史时间跨度不支持此参数，仅支持：1h（1小时）、12h（12小时）、24h（24小时）、1w（1周）、30d（30天）"
            ) from exc

    @property
    def stats_type(self) -> str:
        return {
            self.ONE_HOUR: "1m",
            self.TWELVE_HOURS: "10m",
            self.ONE_DAY: "20m",
            self.ONE_WEEK: "120m",
            self.THIRTY_DAYS: "480m",
        }[self]

    @property
    def duration(self) -> timedelta:
        return {
            self.ONE_HOUR: timedelta(hours=1),
            self.TWELVE_HOURS: timedelta(hours=12),
            self.ONE_DAY: timedelta(days=1),
            self.ONE_WEEK: timedelta(days=7),
            self.THIRTY_DAYS: timedelta(days=30),
        }[self]

    @property
    def expected_interval(self) -> timedelta:
        return {
            self.ONE_HOUR: timedelta(minutes=1),
            self.TWELVE_HOURS: timedelta(minutes=10),
            self.ONE_DAY: timedelta(minutes=20),
            self.ONE_WEEK: timedelta(hours=2),
            self.THIRTY_DAYS: timedelta(hours=8),
        }[self]


class BeszelModel(BaseModel):
    """Common compatibility settings for upstream records."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PocketBaseListResult[T](BeszelModel):
    page: int = 1
    per_page: int = Field(default=0, alias="perPage")
    total_items: int = Field(default=0, alias="totalItems")
    total_pages: int = Field(default=1, alias="totalPages")
    items: list[T] = Field(default_factory=list)


class SystemSummary(BeszelModel):
    id: str
    name: str
    status: str
    updated: datetime | None = None
    created: datetime | None = None
    info: dict[str, Any] = Field(default_factory=dict)
    host: str | None = None
    port: int | None = None

    @model_validator(mode="before")
    @classmethod
    def require_core_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not value.get("id") or not value.get("name"):
            raise ValueError("systems record requires id and name")
        if "status" not in value or value.get("status") is None:
            raise ValueError("systems record requires status")
        return value


class SystemDetails(BeszelModel):
    id: str | None = None
    system: str | None = None
    hostname: str | None = None
    os: str | None = None
    kernel: str | None = None
    arch: str | None = None
    cpu: str | None = None
    cores: int | None = None
    threads: int | None = None
    memory: float | None = None

    @field_validator("os", mode="before")
    @classmethod
    def normalize_os(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, int):
            os_map = {
                0: "Linux",
                1: "macOS",
                2: "Windows",
                3: "FreeBSD",
            }
            return os_map.get(value, f"OS ({value})")
        return str(value)

    @field_validator("hostname", "kernel", "arch", "cpu", mode="before")
    @classmethod
    def normalize_str_fields(cls, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    @field_validator("cores", "threads", mode="before")
    @classmethod
    def normalize_int_fields(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @field_validator("memory", mode="before")
    @classmethod
    def normalize_memory(cls, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None


class SystemMetrics(BeszelModel):
    system: str | None = None
    type: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_stats(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("system_stats record must be an object")
        result = dict(value)
        if "stats" not in result:
            result["stats"] = {
                key: val
                for key, val in result.items()
                if key not in {"id", "system", "type", "created", "updated"}
            }
        return result


class SystemHistoryMetrics(SystemMetrics):
    created: datetime | None = None


class ContainerStats(BeszelModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=False,
        validate_by_name=False,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )

    name: str = Field(alias="n", min_length=1)
    cpu: float | None = Field(default=None, alias="c")
    memory: float | None = Field(default=None, alias="m")


class SystemDetailView(BeszelModel):
    summary: SystemSummary
    details: SystemDetails | None = None
    metrics: SystemMetrics | None = None
    containers: list[ContainerStats] = Field(default_factory=list)


class SystemHistoryPoint(BeszelModel):
    created: datetime
    stats: dict[str, Any] = Field(default_factory=dict)


class SystemHistoryView(BeszelModel):
    summary: SystemSummary
    range: HistoryRange
    points: list[SystemHistoryPoint] = Field(default_factory=list)
    has_gaps: bool = False
    details: SystemDetails | None = None
