"""Safe webhook control and normalized notification models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NotificationSource(StrEnum):
    BESZEL = "beszel"
    WATCHTOWER = "watchtower"
    UPTIME_KUMA = "uptime_kuma"
    SHOUTRRR = "shoutrrr"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class NormalizedNotification:
    request_id: str
    source: NotificationSource
    title: str
    message: str
    send_history: bool = False
    history_system_id: str | None = None
