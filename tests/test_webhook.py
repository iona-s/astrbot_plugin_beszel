from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    ContainerHistoryPoint,
    SystemHistoryView,
    SystemSummary,
)
from astrbot_plugin_beszel.core.config import WebhookConfig
from astrbot_plugin_beszel.core.webhook.delivery import WebhookDelivery
from astrbot_plugin_beszel.core.webhook.models import (
    NormalizedNotification,
    NotificationSource,
)
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


class _FakeContext:
    def __init__(self, outcomes: dict[str, list[Any]]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, Any]] = []

    async def send_message(self, target: str, message_chain) -> bool:
        self.calls.append((target, message_chain))
        queue = self.outcomes.get(target)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return bool(item)
        return True


class _FakeRenderer:
    def __init__(self, image_bytes: bytes = b"\x89PNG\r\n\x1a\nfake") -> None:
        self.image_bytes = image_bytes
        self.rendered_views: list[Any] = []

    async def render_history(self, view) -> bytes:
        self.rendered_views.append(view)
        return self.image_bytes


class _FakeHistoryService:
    def __init__(self, view=None) -> None:
        self.view = view

    async def get_system_history(self, _system_id, _range):
        return self.view


@pytest.mark.asyncio
async def test_delivery_accounting_handles_success_failure_and_mixed_targets(
    webhook_data,
    caplog,
    monkeypatch,
) -> None:
    from astrbot.api import logger

    monkeypatch.setattr(logger, "propagate", True)
    caplog.set_level(logging.DEBUG)
    targets = (
        "target:success",
        "target:text_false",
        "target:text_exception",
        "target:image_false",
        "target:image_exception",
    )
    config = WebhookConfig(
        enabled=True,
        path=webhook_data["server"]["path"],
        token=webhook_data["auth"]["token"],
        target_umos=targets,
    )
    outcomes = {
        "target:success": [True, True],
        "target:text_false": [False, True],
        "target:text_exception": [RuntimeError("text network fail"), True],
        "target:image_false": [True, False],
        "target:image_exception": [True, RuntimeError("image render fail")],
    }
    context = _FakeContext(outcomes)
    delivery = WebhookDelivery(
        config=config,
        context=context,
        service=_FakeHistoryService(),
        renderer=_FakeRenderer(),
    )
    notification_data = webhook_data["delivery_notification"]
    notification = NormalizedNotification(
        source=NotificationSource.GENERIC,
        title=notification_data["title"],
        message=notification_data["message"],
        history_system_id=notification_data["history_system_id"],
        request_id=notification_data["request_id"],
    )

    successes = await delivery.deliver(notification)
    assert successes == 3
    assert (
        f"completed delivery for request_id={notification.request_id}: text=3/2 image=1/2"
        in caplog.text
    )
    assert (
        "Webhook image delivery rejected or unhandled for target=target:image_false"
        in caplog.text
    )
    assert (
        "Webhook image delivery failed for target=target:image_exception: RuntimeError"
        in caplog.text
    )

    targets_called = [t for t, _ in context.calls]
    assert targets_called.count("target:text_false") == 1
    assert targets_called.count("target:text_exception") == 1
    assert targets_called.count("target:success") == 2
    assert targets_called.count("target:image_false") == 2
    assert targets_called.count("target:image_exception") == 2

    seen_targets = []
    for t, _ in context.calls:
        if not seen_targets or seen_targets[-1] != t:
            seen_targets.append(t)
    assert tuple(seen_targets) == targets


@pytest.mark.asyncio
async def test_server_rejects_non_ascii_and_malformed_auth_before_reading_body(
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

    # 1. Non-ASCII header returns 401 without reading body
    request_non_ascii = _FakeRequest(
        {"Authorization": webhook_data["auth"]["non_ascii"]}, fail_read=True
    )
    response_non_ascii = await server.notify(request_non_ascii)
    assert response_non_ascii.status == 401
    assert request_non_ascii.read_called is False
    assert delivery.notifications == []

    # 2. Server with directly constructed non-ASCII token returns 401 without TypeError
    invalid_config = WebhookConfig(
        enabled=True,
        path=webhook_data["server"]["path"],
        token=webhook_data["auth"]["non_ascii_token"],
        target_umos=target_umos,
    )
    server_invalid_token = WebhookServer(
        invalid_config, delivery, _FakeService(overview_data)
    )
    request_valid_ascii = _FakeRequest(
        {"Authorization": webhook_data["auth"]["valid"]}, fail_read=True
    )
    response_invalid_config = await server_invalid_token.notify(request_valid_ascii)
    assert response_invalid_config.status == 401
    assert request_valid_ascii.read_called is False


@pytest.mark.asyncio
async def test_delivery_with_real_context_missing_platform_returns_502(
    webhook_data, overview_data
) -> None:
    from unittest.mock import MagicMock

    from astrbot.core.star.context import Context

    real_context = Context.__new__(Context)
    real_context.platform_manager = MagicMock()
    real_context.platform_manager.platform_insts = []

    config = WebhookConfig(
        enabled=True,
        path="/notify",
        token=webhook_data["auth"]["token"],
        target_umos=("aiocqhttp:GroupMessage:123456",),
    )
    delivery = WebhookDelivery(
        config=config,
        context=real_context,
        service=_FakeHistoryService(),
        renderer=_FakeRenderer(),
    )
    server = WebhookServer(config, delivery, _FakeService(overview_data))
    case = webhook_data["generic_json"]
    request = _FakeRequest(
        {
            "Authorization": webhook_data["auth"]["valid"],
            "Content-Type": case["content_type"],
            **case["headers"],
        },
        _encoded_body(case),
    )
    response = await server.notify(request)
    assert response.status == 502


@pytest.mark.asyncio
async def test_loopback_server_handles_non_ascii_auth_over_http(webhook_data) -> None:
    import aiohttp

    config = WebhookConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,
        path=webhook_data["server"]["path"],
        token=webhook_data["auth"]["token"],
        target_umos=("aiocqhttp:GroupMessage:123456",),
    )
    server = WebhookServer(config, _FakeDelivery(), _FakeHistoryService())
    await server.start()
    try:
        assert server._site is not None and server._site._server is not None
        port = server._site._server.sockets[0].getsockname()[1]
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{port}{config.path}",
                headers={"Authorization": webhook_data["auth"]["non_ascii"]},
            ) as response,
        ):
            assert response.status == 401
            body = await response.json()
            assert body["status"] == "unauthorized"
            assert "request_id" in body
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_webhook_history_attachment_passes_container_points_to_renderer(
    history_data, container_history_data, webhook_data
) -> None:
    view = SystemHistoryView.model_validate(history_data)
    view.container_points = [
        ContainerHistoryPoint(created=m.created, stats=m.stats)
        for raw in container_history_data["records"]
        if (m := ContainerHistoryMetrics.model_validate(raw)).created is not None
    ]
    renderer = _FakeRenderer()
    service = _FakeHistoryService(view=view)
    context = _FakeContext({"target:success": [True, True]})
    config = WebhookConfig(
        enabled=True,
        path=webhook_data["server"]["path"],
        token=webhook_data["auth"]["token"],
        target_umos=("target:success",),
    )
    delivery = WebhookDelivery(
        config=config,
        context=context,
        service=service,
        renderer=renderer,
    )
    notification_data = webhook_data["delivery_notification"]
    notification = NormalizedNotification(
        source=NotificationSource.GENERIC,
        title=notification_data["title"],
        message=notification_data["message"],
        history_system_id=notification_data["history_system_id"],
        request_id=notification_data["request_id"],
    )
    successes = await delivery.deliver(notification)
    assert successes == 1
    assert len(renderer.rendered_views) == 1
    passed_view = renderer.rendered_views[0]
    assert passed_view is view
    assert len(passed_view.container_points) > 0
