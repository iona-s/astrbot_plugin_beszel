from __future__ import annotations

import json

import pytest
from astrbot_plugin_beszel.core.beszel.models import SystemSummary
from astrbot_plugin_beszel.core.config import WebhookConfig
from astrbot_plugin_beszel.core.webhook.models import NotificationSource
from astrbot_plugin_beszel.core.webhook.parsers import (
    WebhookPayloadError,
    attach_history,
    history_requested,
    parse_payload,
)
from astrbot_plugin_beszel.core.webhook.server import WebhookServer


def _encoded_body(case: dict) -> bytes:
    body = case["body"]
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body).encode("utf-8")


def test_supported_webhook_payloads_are_normalized(webhook_data) -> None:
    generic = webhook_data["generic_json"]
    notification = parse_payload(
        _encoded_body(generic),
        content_type=generic["content_type"],
        headers=generic["headers"],
        request_id=webhook_data["request_ids"]["generic"],
    )
    assert notification.source is NotificationSource.GENERIC
    assert notification.title == generic["body"]["title"]
    assert notification.message == generic["body"]["message"]
    assert notification.send_history is True

    uptime = webhook_data["uptime_kuma"]
    uptime_notification = parse_payload(
        _encoded_body(uptime),
        content_type=uptime["content_type"],
        headers=uptime["headers"],
        request_id=webhook_data["request_ids"]["uptime"],
    )
    assert uptime_notification.source is NotificationSource.UPTIME_KUMA
    assert uptime_notification.title == uptime["body"]["monitor"]["name"]

    plain = webhook_data["plain_text"]
    plain_notification = parse_payload(
        _encoded_body(plain),
        content_type=plain["content_type"],
        headers=plain["headers"],
        request_id=webhook_data["request_ids"]["plain"],
    )
    assert plain_notification.source is NotificationSource.SHOUTRRR
    assert plain_notification.message == plain["body"]


def test_beszel_history_notification_attaches_known_system(
    webhook_data, overview_data
) -> None:
    case = webhook_data["beszel_history"]
    notification = parse_payload(
        _encoded_body(case),
        content_type=case["content_type"],
        headers=case["headers"],
        request_id=webhook_data["request_ids"]["history"],
    )
    known_systems = {item["id"]: item["name"] for item in overview_data}
    attached = attach_history(notification, known_systems)
    assert history_requested(notification) is True
    assert attached.source is NotificationSource.BESZEL
    assert attached.history_system_id == overview_data[0]["id"]


@pytest.mark.parametrize(
    ("case_name", "expected_status"),
    [("malformed_json", 400), ("unsupported_media", 415)],
)
def test_invalid_webhook_payloads_return_explicit_status(
    webhook_data, case_name: str, expected_status: int
) -> None:
    case = webhook_data[case_name]
    with pytest.raises(WebhookPayloadError) as exc_info:
        parse_payload(
            _encoded_body(case),
            content_type=case["content_type"],
            headers=case["headers"],
            request_id=webhook_data["request_ids"]["invalid"],
        )
    assert exc_info.value.status == expected_status


class _FakeRequest:
    def __init__(self, headers: dict[str, str], body: bytes = b"", *, fail_read=False):
        self.headers = headers
        self.method = "POST"
        self.path = "/notify"
        self._body = body
        self.fail_read = fail_read
        self.read_called = False

    async def read(self) -> bytes:
        self.read_called = True
        if self.fail_read:
            raise AssertionError("body must not be read before authentication")
        return self._body


class _FakeDelivery:
    def __init__(self, successes: int = 1) -> None:
        self.successes = successes
        self.notifications = []

    async def deliver(self, notification) -> int:
        self.notifications.append(notification)
        return self.successes


class _FakeService:
    def __init__(self, overview_data) -> None:
        self.systems = [SystemSummary.model_validate(item) for item in overview_data]

    async def list_systems(self):
        return self.systems


@pytest.mark.asyncio
async def test_server_authenticates_before_reading_body(
    webhook_data, overview_data, config_data
) -> None:
    target_umos = tuple(config_data["complete"]["webhook"]["target_umos"][:1])
    config = WebhookConfig(
        enabled=True,
        path=webhook_data["server"]["path"],
        token=webhook_data["auth"]["token"],
        target_umos=target_umos,
    )
    delivery = _FakeDelivery()
    server = WebhookServer(config, delivery, _FakeService(overview_data))
    request = _FakeRequest(
        {"Authorization": webhook_data["auth"]["invalid"]}, fail_read=True
    )

    response = await server.notify(request)

    assert response.status == 401
    assert request.read_called is False
    assert delivery.notifications == []
    assert webhook_data["auth"]["token"] not in response.text


@pytest.mark.asyncio
async def test_server_delivers_valid_payload_and_handles_delivery_failure(
    webhook_data, overview_data, config_data
) -> None:
    target_umos = tuple(config_data["complete"]["webhook"]["target_umos"][:1])
    config = WebhookConfig(
        enabled=True,
        path=webhook_data["server"]["path"],
        token=webhook_data["auth"]["token"],
        target_umos=target_umos,
    )
    case = webhook_data["generic_json"]
    request = _FakeRequest(
        {
            "Authorization": webhook_data["auth"]["valid"],
            "Content-Type": case["content_type"],
            **case["headers"],
        },
        _encoded_body(case),
    )
    delivery = _FakeDelivery(successes=1)
    server = WebhookServer(config, delivery, _FakeService(overview_data))
    response = await server.notify(request)
    assert response.status == 200
    assert delivery.notifications[0].request_id

    failed_delivery = _FakeDelivery(successes=0)
    failed_server = WebhookServer(config, failed_delivery, _FakeService(overview_data))
    failed_response = await failed_server.notify(
        _FakeRequest(
            {
                "Authorization": webhook_data["auth"]["valid"],
                "Content-Type": case["content_type"],
                **case["headers"],
            },
            _encoded_body(case),
        )
    )
    assert failed_response.status == 502
