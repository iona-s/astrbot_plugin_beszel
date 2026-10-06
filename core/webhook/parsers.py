"""Strict, bounded parsers for supported webhook payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace

from .models import NormalizedNotification, NotificationSource

MAX_TEXT_LENGTH = 4000
MAX_BODY_BYTES = 64 * 1024
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
    unsupported media, 413 for oversized bodies, 400 for invalid payloads).
    """

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def history_requested(notification: NormalizedNotification) -> bool:
    """Return whether a notification may request Beszel history lookup."""
    return notification.send_history and notification.source in _HISTORY_SOURCES


def analysis_requested(notification: NormalizedNotification) -> bool:
    """Return whether a notification may request Beszel LLM analysis."""
    return notification.send_analysis and notification.source in _HISTORY_SOURCES


def parse_payload(
    body: bytes,
    *,
    content_type: str,
    headers: Mapping[str, str],
    request_id: str,
) -> NormalizedNotification:
    """Normalize one request without retaining or serializing its raw body.

    The size limit applies to raw bytes so ASCII-escaped JSON is not rejected
    for its escaped length; individual fields are truncated afterwards. JSON
    that cannot be decoded, including oversized integers and deep nesting, is
    handled as plain text.

    Args:
        body: Raw request body.
        content_type: Request ``Content-Type`` header value.
        headers: Request headers used for the explicit source override.
        request_id: Correlation ID carried by the notification.

    Returns:
        The normalized notification.

    Raises:
        WebhookPayloadError: The body is empty, too large, not UTF-8, uses an
            unsupported media type, or lacks a message.
    """
    if not body:
        raise WebhookPayloadError("empty webhook payload")
    if len(body) > MAX_BODY_BYTES:
        raise WebhookPayloadError("webhook body is too large", status=413)
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        raise WebhookPayloadError("unsupported webhook media type", status=415)
    if media_type not in {"application/json", "text/json", "text/plain", ""}:
        raise WebhookPayloadError("unsupported webhook media type", status=415)
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WebhookPayloadError("webhook body is not UTF-8 text", status=415) from exc

    data: object = None
    if media_type in {"application/json", "text/json"}:
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
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
        send_analysis=_strict_true(data.get("send_analysis")),
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
    """Attach a verified Beszel system match without reparsing the webhook body."""
    if not (history_requested(notification) or analysis_requested(notification)):
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
    """Resolve the Beszel system an alert refers to.

    A ``/system/<id>`` link is Beszel's authoritative identifier. When links
    exist but none is known (the account cannot see the system or the list is
    stale), the title is not consulted, because it could bind another system
    with a similar name.

    Args:
        title: Notification title.
        message: Notification message that may contain system links.
        known_systems: Visible system names keyed by system ID.
        allow_title: Whether Beszel title formats may identify the system.

    Returns:
        The matched system ID, or ``None`` when no unambiguous match exists.
    """
    link_ids = [match.group(1) for match in _SYSTEM_LINK_RE.finditer(message)]
    if link_ids or not allow_title:
        return next((item for item in link_ids if item in known_systems), None)
    # Beszel titles are "<name> <metric> above|below threshold" and
    # "Connection to <name> is <up|down> <emoji>"; the trailing space stops a
    # name from matching a longer name that shares its prefix.
    title_folded = title.casefold()
    candidates: dict[str, int] = {}
    for system_id, name in known_systems.items():
        name_folded = name.casefold()
        threshold_prefix = f"{name_folded} "
        threshold_title = (
            title_folded.startswith(threshold_prefix)
            and "threshold" in title_folded[len(threshold_prefix) :]
        )
        connection_title = title_folded.startswith(f"connection to {name_folded} is ")
        if threshold_title or connection_title:
            candidates[system_id] = len(name_folded)
    if not candidates:
        return None
    longest = max(candidates.values())
    best = [system_id for system_id, size in candidates.items() if size == longest]
    return best[0] if len(best) == 1 else None


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
