"""Safe webhook control and normalized notification models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NotificationSource(StrEnum):
    BESZEL = "beszel"
    WATCHTOWER = "watchtower"
    UPTIME_KUMA = "uptime_kuma"
    SHOUTRRR = "shoutrrr"
    GENERIC = "generic"


class NotificationSeverity(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class NotificationState(StrEnum):
    UP = "up"
    DOWN = "down"
    RESOLVED = "resolved"
    FIRING = "firing"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HistoryAttachmentRequest:
    system_id: str
    range: str = "1h"


@dataclass(frozen=True, slots=True)
class NormalizedNotification:
    request_id: str
    source: NotificationSource
    title: str
    message: str
    severity: NotificationSeverity = NotificationSeverity.UNKNOWN
    state: NotificationState = NotificationState.UNKNOWN
    occurred_at: str | None = None
    subject_name: str | None = None
    link: str | None = None
    safe_metadata: dict[str, str] = field(default_factory=dict)
    history: HistoryAttachmentRequest | None = None
