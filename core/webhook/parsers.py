"""Strict, bounded parsers for supported webhook payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace

from .models import NormalizedNotification, NotificationSource

MAX_TEXT_LENGTH = 4000
_SYSTEM_LINK_RE = re.compile(r"/system/([^/?#\s]+)", re.IGNORECASE)
_HISTORY_SOURCES = frozenset(
    {
        NotificationSource.BESZEL,
        NotificationSource.GENERIC,
        NotificationSource.SHOUTRRR,
    }
)


class WebhookPayloadError(ValueError):
    """The webhook body is unsupported or missing a safe message.

    ``status`` is the HTTP status the receiver should answer with (415 for
    unsupported media, 400 for invalid payloads).
    """

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def history_requested(notification: NormalizedNotification) -> bool:
    """Return whether a notification may request Beszel history lookup."""
    return notification.send_history and notification.source in _HISTORY_SOURCES


def parse_payload(
    body: bytes,
    *,
    content_type: str,
    headers: Mapping[str, str],
    request_id: str,
) -> NormalizedNotification:
    """Normalize one request without retaining or serializing its raw body."""
    if not body:
        raise WebhookPayloadError("empty webhook payload")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        raise WebhookPayloadError("unsupported webhook media type", status=415)
    if media_type not in {"application/json", "text/json", "text/plain", ""}:
        raise WebhookPayloadError("unsupported webhook media type", status=415)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WebhookPayloadError("webhook body is not UTF-8 text", status=415) from exc
    if len(text) > MAX_TEXT_LENGTH * 4:
        raise WebhookPayloadError("webhook body is too large")

    data: object = None
    if media_type in {"application/json", "text/json"}:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict) and _looks_like_uptime_kuma(data):
        notification = _parse_uptime_kuma(data, request_id=request_id)
    elif isinstance(data, dict):
        notification = _parse_json(data, headers=headers, request_id=request_id)
    else:
        notification = NormalizedNotification(
            request_id=request_id,
            source=NotificationSource.SHOUTRRR,
            title="Webhook 通知",
            message=_truncate(text),
        )
    return notification


def _parse_json(
    data: dict, *, headers: Mapping[str, str], request_id: str
) -> NormalizedNotification:
    source_value = headers.get("X-Webhook-Source") or data.get("source")
    source = _source(source_value)
    title = (
        _scalar(data.get("title"))
        or _scalar(data.get("topic"))
        or _scalar(data.get("subject"))
    )
    message = (
        _scalar(data.get("message"))
        or _scalar(data.get("msg"))
        or _scalar(data.get("text"))
        or _scalar(data.get("content"))
    )
    if not message:
        raise WebhookPayloadError("webhook JSON requires a message")
    return NormalizedNotification(
        request_id=request_id,
        source=source,
        title=_truncate(title or source.value),
        message=_truncate(message),
        send_history=_strict_true(data.get("send_history")),
    )


def _looks_like_uptime_kuma(data: dict) -> bool:
    return {"heartbeat", "monitor", "msg"}.issubset(data)


def _parse_uptime_kuma(data: dict, *, request_id: str) -> NormalizedNotification:
    heartbeat_value = data["heartbeat"]
    monitor_value = data["monitor"]
    if heartbeat_value is not None and not isinstance(heartbeat_value, dict):
        raise WebhookPayloadError("invalid Uptime Kuma heartbeat")
    if monitor_value is not None and not isinstance(monitor_value, dict):
        raise WebhookPayloadError("invalid Uptime Kuma monitor")
    monitor = monitor_value or {}
    message = _scalar(data["msg"])
    if not message:
        raise WebhookPayloadError("Uptime Kuma webhook requires msg")
    title = _scalar(monitor.get("name")) or "Uptime Kuma"
    return NormalizedNotification(
        request_id=request_id,
        source=NotificationSource.UPTIME_KUMA,
        title=_truncate(title),
        message=_truncate(message),
    )


def attach_history(
    notification: NormalizedNotification, known_systems: Mapping[str, str]
) -> NormalizedNotification:
    """Attach a Beszel history request without reparsing the webhook body."""
    if not history_requested(notification):
        return notification
    candidate = _system_candidate(
        notification.title,
        notification.message,
        known_systems,
        allow_title=notification.source is NotificationSource.BESZEL,
    )
    if candidate is None:
        return notification
    return replace(
        notification,
        source=NotificationSource.BESZEL,
        history_system_id=candidate,
    )


def _system_candidate(
    title: str,
    message: str,
    known_systems: Mapping[str, str],
    *,
    allow_title: bool,
) -> str | None:
    match = _SYSTEM_LINK_RE.search(message)
    if match and match.group(1) in known_systems:
        return match.group(1)
    if not allow_title:
        return None
    title_folded = title.casefold()
    candidates = []
    for system_id, name in known_systems.items():
        name_folded = name.casefold()
        threshold_title = (
            title_folded.startswith(name_folded)
            and "threshold" in title_folded[len(name_folded) :]
        )
        connection_title = title_folded in {
            f"connection to {name_folded} is up",
            f"connection to {name_folded} is down",
        }
        if threshold_title or connection_title:
            candidates.append(system_id)
    return candidates[0] if len(candidates) == 1 else None


def _source(value: object) -> NotificationSource:
    normalized = _scalar(value).casefold()
    if not normalized:
        return NotificationSource.GENERIC
    try:
        return NotificationSource(normalized)
    except ValueError:
        return NotificationSource.SHOUTRRR


def _strict_true(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.casefold() == "true")


def _scalar(value: object) -> str:
    return (
        value.strip()
        if isinstance(value, str)
        else str(value).strip()
        if isinstance(value, (int, float, bool))
        else ""
    )


def _truncate(value: str) -> str:
    return (
        value if len(value) <= MAX_TEXT_LENGTH else value[: MAX_TEXT_LENGTH - 1] + "…"
    )
