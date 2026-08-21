"""Strict, bounded parsers for supported webhook payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import urlparse
from uuid import uuid4

from .models import (
    HistoryAttachmentRequest,
    NormalizedNotification,
    NotificationSeverity,
    NotificationSource,
    NotificationState,
)

MAX_TEXT_LENGTH = 4000
_LINK_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SYSTEM_LINK_RE = re.compile(r"/system/([^/?#\s]+)", re.IGNORECASE)


class WebhookPayloadError(ValueError):
    """The webhook body is unsupported or missing a safe message.

    ``status`` is the HTTP status the receiver should answer with (415 for
    unsupported media, 400 for invalid payloads).
    """

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def parse_payload(
    body: bytes,
    *,
    content_type: str,
    headers: Mapping[str, str] | None = None,
    known_systems: Mapping[str, str] | None = None,
    request_id: str | None = None,
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
        notification = _parse_json(data, headers=headers or {}, request_id=request_id)
    else:
        notification = NormalizedNotification(
            request_id=request_id or uuid4().hex,
            source=NotificationSource.SHOUTRRR,
            title="Webhook 通知",
            message=_truncate(text),
        )
    return _attach_history(notification, known_systems or {})


def _parse_json(
    data: dict, *, headers: Mapping[str, str], request_id: str | None
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
        request_id=request_id or uuid4().hex,
        source=source,
        title=_truncate(title or source.value),
        message=_truncate(message),
        severity=_severity(data.get("severity"), message),
        state=_state(data.get("state"), message),
        occurred_at=_scalar(data.get("occurred_at")),
        link=_safe_link(message),
        safe_metadata={
            **{
                key: _truncate(_scalar(data.get(key)), 80)
                for key in ("source", "type")
                if _scalar(data.get(key))
            },
            **(
                {"send_history": "true"}
                if _strict_true(data.get("send_history"))
                else {}
            ),
        },
    )


def _looks_like_uptime_kuma(data: dict) -> bool:
    return {"heartbeat", "monitor", "msg"}.issubset(data)


def _parse_uptime_kuma(data: dict, *, request_id: str | None) -> NormalizedNotification:
    heartbeat_value = data["heartbeat"]
    monitor_value = data["monitor"]
    if heartbeat_value is not None and not isinstance(heartbeat_value, dict):
        raise WebhookPayloadError("invalid Uptime Kuma heartbeat")
    if monitor_value is not None and not isinstance(monitor_value, dict):
        raise WebhookPayloadError("invalid Uptime Kuma monitor")
    heartbeat = heartbeat_value or {}
    monitor = monitor_value or {}
    status = heartbeat.get("status")
    message = _scalar(data["msg"])
    if not message:
        raise WebhookPayloadError("Uptime Kuma webhook requires msg")
    state = (
        NotificationState.UP
        if type(status) is int and status == 1
        else NotificationState.DOWN
        if type(status) is int and status == 0
        else NotificationState.UNKNOWN
    )
    title = _scalar(monitor.get("name")) or "Uptime Kuma"
    return NormalizedNotification(
        request_id=request_id or uuid4().hex,
        source=NotificationSource.UPTIME_KUMA,
        title=_truncate(title),
        message=_truncate(message),
        severity=NotificationSeverity.SUCCESS
        if state is NotificationState.UP
        else NotificationSeverity.CRITICAL
        if state is NotificationState.DOWN
        else NotificationSeverity.UNKNOWN,
        state=state,
        subject_name=_truncate(_scalar(monitor.get("name")), 120) or None,
        link=_safe_link(_scalar(monitor.get("url"))),
        safe_metadata={
            key: _truncate(_scalar(monitor.get(key)), 80)
            for key in ("type",)
            if _scalar(monitor.get(key))
        },
    )


def _attach_history(
    notification: NormalizedNotification, known_systems: Mapping[str, str]
) -> NormalizedNotification:
    send_history = notification.safe_metadata.get("send_history") == "true"
    if not send_history:
        return notification
    candidate = _system_candidate(
        notification.title,
        notification.message,
        notification.link,
        known_systems,
        allow_title=notification.source is NotificationSource.BESZEL,
    )
    if candidate is None:
        return notification
    if notification.source not in {
        NotificationSource.BESZEL,
        NotificationSource.GENERIC,
        NotificationSource.SHOUTRRR,
    }:
        return notification
    source = NotificationSource.BESZEL
    return NormalizedNotification(
        request_id=notification.request_id,
        source=source,
        title=notification.title,
        message=notification.message,
        severity=notification.severity,
        state=notification.state,
        occurred_at=notification.occurred_at,
        subject_name=notification.subject_name,
        link=notification.link,
        safe_metadata=notification.safe_metadata,
        history=HistoryAttachmentRequest(candidate),
    )


def _system_candidate(
    title: str,
    message: str,
    link: str | None,
    known_systems: Mapping[str, str],
    *,
    allow_title: bool,
) -> str | None:
    link_text = link or message
    if link_text:
        match = _SYSTEM_LINK_RE.search(link_text)
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
    return {item.value: item for item in NotificationSource}.get(
        normalized,
        NotificationSource.SHOUTRRR if normalized else NotificationSource.GENERIC,
    )


def _severity(value: object, message: str) -> NotificationSeverity:
    normalized = _scalar(value).casefold()
    if normalized in {item.value for item in NotificationSeverity}:
        return NotificationSeverity(normalized)
    lowered = message.casefold()
    return (
        NotificationSeverity.CRITICAL
        if any(word in lowered for word in ("down", "failed", "failure", "critical"))
        else NotificationSeverity.SUCCESS
        if any(word in lowered for word in ("resolved", "up", "recovered"))
        else NotificationSeverity.INFO
    )


def _state(value: object, message: str) -> NotificationState:
    normalized = _scalar(value).casefold()
    if normalized in {item.value for item in NotificationState}:
        return NotificationState(normalized)
    lowered = message.casefold()
    return (
        NotificationState.DOWN
        if "down" in lowered
        else NotificationState.UP
        if " up" in lowered
        else NotificationState.UNKNOWN
    )


def _safe_link(text: str) -> str | None:
    for candidate in _LINK_RE.findall(text or ""):
        parsed = urlparse(candidate.rstrip(".,)"))
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return candidate.rstrip(".,)")
    return None


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


def _truncate(value: str, limit: int = MAX_TEXT_LENGTH) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
