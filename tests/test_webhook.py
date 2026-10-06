from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import UTC
from typing import Any

import pytest
from astrbot.core.message.components import Image, Plain
from astrbot_plugin_beszel.core.analysis import (
    DEFAULT_ANALYSIS_PROMPT,
    DIAGNOSIS_PREFIX,
)
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    ContainerHistoryPoint,
    HistoryRange,
    SystemDetailView,
    SystemHistoryView,
    SystemMetrics,
    SystemSummary,
)
from astrbot_plugin_beszel.core.config import WebhookConfig
from astrbot_plugin_beszel.core.rendering.renderer import BeszelRenderer
from astrbot_plugin_beszel.core.webhook.delivery import WebhookDelivery
from astrbot_plugin_beszel.core.webhook.models import (
    NormalizedNotification,
    NotificationSource,
)
from astrbot_plugin_beszel.core.webhook.parsers import (
    MAX_BODY_BYTES,
    MAX_TEXT_LENGTH,
    WebhookPayloadError,
    analysis_requested,
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
    ("case_name", "expected_history", "expected_analysis"),
    [("beszel_analysis", False, True), ("generic_both", True, True)],
)
def test_analysis_notification_binds_system_without_changing_switches(
    webhook_data,
    overview_data,
    case_name: str,
    expected_history: bool,
    expected_analysis: bool,
) -> None:
    case = webhook_data[case_name]
    notification = parse_payload(
        _encoded_body(case),
        content_type=case["content_type"],
        headers=case["headers"],
        request_id=webhook_data["request_ids"]["history"],
    )
    known_systems = {item["id"]: item["name"] for item in overview_data}

    attached = attach_history(notification, known_systems)

    assert attached.source is NotificationSource.BESZEL
    assert attached.history_system_id == overview_data[0]["id"]
    assert attached.send_history is expected_history
    assert attached.send_analysis is expected_analysis


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


def test_attach_history_binds_beszel_systems_by_link_or_title(webhook_data) -> None:
    for case in webhook_data["system_matching"]:
        notification = NormalizedNotification(
            request_id=webhook_data["request_ids"]["history"],
            source=NotificationSource(case["source"]),
            title=case["title"],
            message=case["message"],
            send_history=True,
        )

        attached = attach_history(notification, case["systems"])

        assert attached.history_system_id == case["expected"], case["case"]
        expected_source = (
            NotificationSource.BESZEL if case["expected"] else notification.source
        )
        assert attached.source is expected_source, case["case"]


def test_json_payload_with_utf8_bom_is_parsed_as_json(webhook_data) -> None:
    case = webhook_data["bom_json"]

    notification = parse_payload(
        b"\xef\xbb\xbf" + _encoded_body(case),
        content_type=case["content_type"],
        headers=case["headers"],
        request_id=webhook_data["request_ids"]["generic"],
    )

    assert notification.source is NotificationSource.BESZEL
    assert notification.title == case["body"]["title"]
    assert notification.message == case["body"]["message"]
    assert notification.send_history is True
    assert notification.send_analysis is True


def test_undecodable_json_falls_back_to_plain_text(webhook_data) -> None:
    limits = webhook_data["hostile_json"]
    depth = limits["nesting_depth"]
    bodies = (
        b'{"message": ' + b"9" * limits["integer_digits"] + b"}",
        b"[" * depth + b"]" * depth,
    )

    for body in bodies:
        notification = parse_payload(
            body,
            content_type="application/json",
            headers={},
            request_id=webhook_data["request_ids"]["plain"],
        )

        assert notification.source is NotificationSource.SHOUTRRR
        assert notification.message == body.decode("ascii")[: MAX_TEXT_LENGTH - 1] + "…"


def test_payload_size_limit_counts_raw_bytes(webhook_data) -> None:
    with pytest.raises(WebhookPayloadError) as exc_info:
        parse_payload(
            b"x" * (MAX_BODY_BYTES + 1),
            content_type="text/plain",
            headers={},
            request_id=webhook_data["request_ids"]["invalid"],
        )
    assert exc_info.value.status == 413

    case = webhook_data["escaped_cjk"]
    message = case["message_unit"] * case["message_repeat"]
    body = json.dumps({"title": case["title"], "message": message}).encode("ascii")
    assert len(body) <= MAX_BODY_BYTES

    notification = parse_payload(
        body,
        content_type=case["content_type"],
        headers=case["headers"],
        request_id=webhook_data["request_ids"]["generic"],
    )

    assert notification.title == case["title"]
    assert notification.message == message[: MAX_TEXT_LENGTH - 1] + "…"


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
        self.closed = False

    async def deliver(self, notification) -> int:
        self.notifications.append(notification)
        return self.successes

    async def close(self) -> None:
        self.closed = True


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


def _message_kind(message_chain) -> str:
    if isinstance(message_chain.chain[0], Image):
        return "image"
    if message_chain.get_plain_text().startswith(DIAGNOSIS_PREFIX):
        return "analysis"
    return "text"


class _FakeContext:
    """Fake platform whose send results are keyed by ``(target, kind)``.

    ``kind`` is ``"image"``, ``"analysis"``, or ``"text"`` so results never
    depend on the arrival order of independent branches.
    """

    def __init__(
        self,
        outcomes: dict[tuple[str, str], list[Any]] | None = None,
        providers: dict[str, Any] | None = None,
        on_send: Any | None = None,
    ) -> None:
        self.outcomes = outcomes or {}
        self.providers = providers if providers is not None else {}
        self.calls: list[tuple[str, Any]] = []
        self.provider_lookups: list[str] = []
        self.on_send = on_send

    def get_using_provider(self, target: str):
        self.provider_lookups.append(target)
        provider = self.providers.get(target)
        if isinstance(provider, Exception):
            raise provider
        return provider

    async def send_message(self, target: str, message_chain) -> bool:
        self.calls.append((target, message_chain))
        if self.on_send is not None:
            self.on_send(target, message_chain)
        queue = self.outcomes.get((target, _message_kind(message_chain)))
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return bool(item)
        return True


class _FakeAsyncProviderContext(_FakeContext):
    """Fake newer AstrBot context whose provider lookup must be awaited."""

    def get_using_provider(self, target: str):
        raise AssertionError("deprecated synchronous provider lookup was used")

    async def get_using_provider_async(self, target: str):
        return super().get_using_provider(target)


class _FakeRenderer:
    def __init__(self, image_bytes: bytes = b"\x89PNG\r\n\x1a\nfake") -> None:
        self.image_bytes = image_bytes
        self.rendered_views: list[Any] = []
        self.hang_event: asyncio.Event | None = None
        self.wait_idle_called = False

    async def render_history(self, view) -> bytes:
        if self.hang_event is not None:
            await self.hang_event.wait()
        self.rendered_views.append(view)
        return self.image_bytes

    async def wait_idle(self) -> None:
        self.wait_idle_called = True


class _FakeHistoryService:
    def __init__(self, view=None, detail=None, systems=None, history_exc=None) -> None:
        self.view = view
        self.detail = detail
        self.systems = systems or []
        self.history_exc = history_exc
        self.hang_event: asyncio.Event | None = None
        self.calls: list[str] = []

    async def list_systems(self):
        self.calls.append("list_systems")
        if self.hang_event is not None:
            await self.hang_event.wait()
        return self.systems

    async def get_system_history(self, _system_id, _range):
        self.calls.append("get_system_history")
        if self.hang_event is not None:
            await self.hang_event.wait()
        if self.history_exc is not None:
            raise self.history_exc
        return self.view

    async def get_system_detail(self, _system_id):
        self.calls.append("get_system_detail")
        if self.hang_event is not None:
            await self.hang_event.wait()
        return self.detail


class _FakeProvider:
    def __init__(
        self,
        completion_text: str = "这是诊断建议",
        role: str = "assistant",
        hang_event: asyncio.Event | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.completion_text = completion_text
        self.role = role
        self.hang_event = hang_event
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def text_chat(
        self,
        prompt: str | None = None,
        system_prompt: str | None = None,
        contexts=None,
        func_tool=None,
        **kwargs,
    ):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if self.hang_event is not None:
            await self.hang_event.wait()
        if self.exc is not None:
            raise self.exc
        from astrbot.core.provider.entities import LLMResponse

        return LLMResponse(role=self.role, completion_text=self.completion_text)


@pytest.mark.asyncio
async def test_delivery_accounting_handles_success_failure_and_mixed_targets(
    webhook_data,
    caplog,
    monkeypatch,
) -> None:
    astrbot_logger = logging.getLogger("astrbot")
    monkeypatch.setattr(astrbot_logger, "propagate", True)
    caplog.set_level(logging.DEBUG, logger="astrbot")
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
        ("target:text_false", "text"): [False],
        ("target:text_exception", "text"): [RuntimeError("text network fail")],
        ("target:image_false", "image"): [False],
        ("target:image_exception", "image"): [RuntimeError("image render fail")],
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
        send_history=True,
    )

    successes = await delivery.deliver(notification)
    assert successes == 3
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    assert (
        f"completed raw text delivery for request_id={notification.request_id}: 3/5 successes"
        in caplog.text
    )
    assert (
        "Webhook image delivery rejected or unhandled for target=target:image_false "
        f"(request_id={notification.request_id})" in caplog.text
    )
    assert (
        "Webhook image delivery failed for target=target:image_exception "
        f"(request_id={notification.request_id}): RuntimeError" in caplog.text
    )

    # Each branch attempts every target, regardless of the other branch's results.
    for kind in ("text", "image"):
        attempted = [t for t, chain in context.calls if _message_kind(chain) == kind]
        assert attempted == list(targets)


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
async def test_loopback_server_rejects_oversized_body_with_413(webhook_data) -> None:
    import aiohttp

    config = WebhookConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,
        path=webhook_data["server"]["path"],
        token=webhook_data["auth"]["token"],
        target_umos=("aiocqhttp:GroupMessage:123456",),
    )
    delivery = _FakeDelivery()
    server = WebhookServer(config, delivery, _FakeHistoryService())
    await server.start()
    try:
        assert server._site is not None and server._site._server is not None
        port = server._site._server.sockets[0].getsockname()[1]
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{port}{config.path}",
                headers={
                    "Authorization": webhook_data["auth"]["valid"],
                    "Content-Type": "text/plain",
                },
                data=b"x" * (MAX_BODY_BYTES + 1),
            ) as response,
        ):
            assert response.status == 413
            body = await response.json()
            assert body["status"] == "invalid"
            assert body["request_id"]
        assert delivery.notifications == []
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
    context = _FakeContext()
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
        send_history=True,
    )
    successes = await delivery.deliver(notification)
    assert successes == 1
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()
    assert len(renderer.rendered_views) == 1
    passed_view = renderer.rendered_views[0]
    assert passed_view is view
    assert len(passed_view.container_points) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("send_history", "send_analysis", "expect_image", "expect_analysis"),
    [
        (False, False, False, False),
        (True, False, True, False),
        (False, True, False, True),
        (True, True, True, True),
    ],
)
async def test_webhook_four_switch_combinations(
    send_history: bool,
    send_analysis: bool,
    expect_image: bool,
    expect_analysis: bool,
) -> None:
    provider = _FakeProvider(completion_text="AI分析排查建议")
    context = _FakeContext(providers={"target:umo1": provider})
    renderer = _FakeRenderer()
    detail = SystemDetailView(
        summary=SystemSummary(id="sys-1", name="Atlas", status="up"),
        metrics=SystemMetrics(stats={"cpu": 80.0, "mp": 60.0}),
    )
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="sys-1", name="Atlas", status="up"),
            range=HistoryRange.ONE_HOUR,
        ),
        detail=detail,
    )
    config = WebhookConfig(enabled=True, target_umos=("target:umo1",))
    delivery = WebhookDelivery(
        context=context, config=config, service=service, renderer=renderer
    )

    notification = NormalizedNotification(
        request_id="req-combo",
        source=NotificationSource.BESZEL,
        title="Combo alert",
        message="Checking switches",
        history_system_id="sys-1",
        send_history=send_history,
        send_analysis=send_analysis,
    )

    successes = await delivery.deliver(notification)
    assert successes == 1

    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    sent_types = [type(chain.chain[0]) for _, chain in context.calls]
    assert Plain in sent_types
    if expect_image:
        assert Image in sent_types
        assert len(renderer.rendered_views) == 1
    else:
        assert Image not in sent_types
        assert len(renderer.rendered_views) == 0

    if expect_analysis:
        assert len(provider.calls) == 1
        analysis_msgs = [
            chain.get_plain_text()
            for _, chain in context.calls
            if "[Beszel AI 诊断]" in chain.get_plain_text()
        ]
        assert len(analysis_msgs) == 1
    else:
        assert len(provider.calls) == 0

    if not send_history and not send_analysis:
        assert service.calls == []


def test_webhook_analysis_trigger_parsing_rules() -> None:
    # 1. Truth values in JSON body
    for val in (True, "true", "True", "TRUE"):
        n = parse_payload(
            json.dumps({"message": "test", "send_analysis": val}).encode("utf-8"),
            content_type="application/json",
            headers={},
            request_id="r",
        )
        assert n.send_analysis is True

    for val in (False, "false", "1", 1, None, " true ", "yes", "on"):
        n = parse_payload(
            json.dumps({"message": "test", "send_analysis": val}).encode("utf-8"),
            content_type="application/json",
            headers={},
            request_id="r",
        )
        assert n.send_analysis is False

    # The ``analyze`` alias is not part of the protocol.
    alias = parse_payload(
        json.dumps({"message": "test", "analyze": True}).encode("utf-8"),
        content_type="application/json",
        headers={},
        request_id="r",
    )
    assert alias.send_analysis is False

    # 2. Source eligibility
    beszel_n = NormalizedNotification(
        request_id="r1",
        source=NotificationSource.BESZEL,
        title="",
        message="",
        send_analysis=True,
    )
    generic_n = NormalizedNotification(
        request_id="r2",
        source=NotificationSource.GENERIC,
        title="",
        message="",
        send_analysis=True,
    )
    kuma_n = NormalizedNotification(
        request_id="r3",
        source=NotificationSource.UPTIME_KUMA,
        title="",
        message="",
        send_analysis=True,
    )
    watchtower_n = NormalizedNotification(
        request_id="r4",
        source=NotificationSource.WATCHTOWER,
        title="",
        message="",
        send_analysis=True,
    )

    assert analysis_requested(beszel_n) is True
    assert analysis_requested(generic_n) is True
    assert analysis_requested(kuma_n) is False
    assert analysis_requested(watchtower_n) is False


@pytest.mark.asyncio
async def test_webhook_http_response_does_not_wait_for_history_or_analysis(
    webhook_data, overview_data
) -> None:
    hang_render = asyncio.Event()
    hang_llm = asyncio.Event()

    renderer = _FakeRenderer()
    renderer.hang_event = hang_render

    provider = _FakeProvider(hang_event=hang_llm)
    context = _FakeContext(providers={"target:umo1": provider})

    detail = SystemDetailView(
        summary=SystemSummary(id="pubatlas0000001", name="Atlas", status="up"),
        metrics=SystemMetrics(stats={"cpu": 90.0, "mp": 80.0}),
    )
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="pubatlas0000001", name="Atlas", status="up"),
            range=HistoryRange.ONE_HOUR,
        ),
        detail=detail,
        systems=[
            SystemSummary(id="pubatlas0000001", name="Atlas Gateway", status="up")
        ],
    )

    config = WebhookConfig(
        enabled=True,
        path="/notify",
        token=webhook_data["auth"]["token"],
        target_umos=("target:umo1",),
    )
    delivery = WebhookDelivery(
        context=context, config=config, service=service, renderer=renderer
    )
    server = WebhookServer(config, delivery, service)

    payload = {
        "source": "beszel",
        "title": "Atlas Gateway threshold exceeded",
        "message": "Open /system/pubatlas0000001",
        "send_history": True,
        "send_analysis": True,
    }
    request = _FakeRequest(
        {
            "Authorization": webhook_data["auth"]["valid"],
            "Content-Type": "application/json",
        },
        json.dumps(payload).encode("utf-8"),
    )

    response = await server.notify(request)
    assert response.status == 200
    body = json.loads(response.text)
    assert body["status"] == "delivered"

    # Only raw text should be sent so far
    assert len(context.calls) == 1
    assert isinstance(context.calls[0][1].chain[0], Plain)

    # Release background tasks
    hang_render.set()
    hang_llm.set()
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    sent_kinds = [type(c.chain[0]) for _, c in context.calls]
    assert sent_kinds.count(Plain) == 2
    assert sent_kinds.count(Image) == 1


@pytest.mark.asyncio
async def test_webhook_raw_text_failure_returns_502_but_background_continues(
    webhook_data, overview_data
) -> None:
    provider = _FakeProvider()
    renderer = _FakeRenderer()
    context = _FakeContext(
        outcomes={("target:umo1", "text"): [False]},
        providers={"target:umo1": provider},
    )
    detail = SystemDetailView(
        summary=SystemSummary(id="pubatlas0000001", name="Atlas", status="up"),
        metrics=SystemMetrics(stats={"cpu": 85.0}),
    )
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="pubatlas0000001", name="Atlas", status="up"),
            range=HistoryRange.ONE_HOUR,
        ),
        detail=detail,
        systems=[
            SystemSummary(id="pubatlas0000001", name="Atlas Gateway", status="up")
        ],
    )
    config = WebhookConfig(
        enabled=True,
        path="/notify",
        token=webhook_data["auth"]["token"],
        target_umos=("target:umo1",),
    )
    delivery = WebhookDelivery(
        context=context, config=config, service=service, renderer=renderer
    )
    server = WebhookServer(config, delivery, service)

    payload = {
        "source": "beszel",
        "title": "Atlas Gateway threshold exceeded",
        "message": "Open /system/pubatlas0000001",
        "send_history": True,
        "send_analysis": True,
    }
    request = _FakeRequest(
        {
            "Authorization": webhook_data["auth"]["valid"],
            "Content-Type": "application/json",
        },
        json.dumps(payload).encode("utf-8"),
    )

    response = await server.notify(request)
    assert response.status == 502

    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    # The failed raw-text target still receives both optional messages.
    sent_kinds = sorted(_message_kind(chain) for _, chain in context.calls)
    assert sent_kinds == ["analysis", "image", "text"]
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_webhook_independent_completion_order_analysis_first() -> None:
    ev_render = asyncio.Event()
    ev_llm = asyncio.Event()
    ev_analysis_sent = asyncio.Event()

    renderer = _FakeRenderer()
    renderer.hang_event = ev_render
    provider = _FakeProvider(hang_event=ev_llm)

    def on_send(_t, chain):
        if "[Beszel AI 诊断]" in chain.get_plain_text():
            ev_analysis_sent.set()

    context = _FakeContext(providers={"target:umo1": provider}, on_send=on_send)
    detail = SystemDetailView(
        summary=SystemSummary(id="s1", name="Node", status="up"),
        metrics=SystemMetrics(stats={"cpu": 80.0}),
    )
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="s1", name="Node", status="up"),
            range=HistoryRange.ONE_HOUR,
        ),
        detail=detail,
    )
    config = WebhookConfig(enabled=True, target_umos=("target:umo1",))
    delivery = WebhookDelivery(
        context=context, config=config, service=service, renderer=renderer
    )

    notification = NormalizedNotification(
        request_id="order-analysis-first",
        source=NotificationSource.BESZEL,
        title="Title",
        message="Msg",
        history_system_id="s1",
        send_history=True,
        send_analysis=True,
    )

    await delivery.deliver(notification)
    # Raw text sent immediately
    assert len(context.calls) == 1
    assert isinstance(context.calls[0][1].chain[0], Plain)

    # Release LLM and await analysis delivery
    ev_llm.set()
    await asyncio.wait_for(ev_analysis_sent.wait(), timeout=5.0)

    assert len(context.calls) == 2
    assert "[Beszel AI 诊断]" in context.calls[1][1].get_plain_text()

    # Release render now and await completion
    ev_render.set()
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    assert len(context.calls) == 3
    assert isinstance(context.calls[2][1].chain[0], Image)


@pytest.mark.asyncio
async def test_webhook_independent_completion_order_history_first() -> None:
    ev_render = asyncio.Event()
    ev_llm = asyncio.Event()
    ev_history_sent = asyncio.Event()

    renderer = _FakeRenderer()
    renderer.hang_event = ev_render
    provider = _FakeProvider(hang_event=ev_llm)

    def on_send(_t, chain):
        if chain.chain and isinstance(chain.chain[0], Image):
            ev_history_sent.set()

    context = _FakeContext(providers={"target:umo1": provider}, on_send=on_send)
    detail = SystemDetailView(
        summary=SystemSummary(id="s1", name="Node", status="up"),
        metrics=SystemMetrics(stats={"cpu": 80.0}),
    )
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="s1", name="Node", status="up"),
            range=HistoryRange.ONE_HOUR,
        ),
        detail=detail,
    )
    config = WebhookConfig(enabled=True, target_umos=("target:umo1",))
    delivery = WebhookDelivery(
        context=context, config=config, service=service, renderer=renderer
    )

    notification = NormalizedNotification(
        request_id="order-history-first",
        source=NotificationSource.BESZEL,
        title="Title",
        message="Msg",
        history_system_id="s1",
        send_history=True,
        send_analysis=True,
    )

    await delivery.deliver(notification)
    assert len(context.calls) == 1
    assert isinstance(context.calls[0][1].chain[0], Plain)

    # Release render and await image delivery
    ev_render.set()
    await asyncio.wait_for(ev_history_sent.wait(), timeout=5.0)

    assert len(context.calls) == 2
    assert isinstance(context.calls[1][1].chain[0], Image)

    # Release LLM now and await completion
    ev_llm.set()
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    assert len(context.calls) == 3
    assert "[Beszel AI 诊断]" in context.calls[2][1].get_plain_text()


@pytest.mark.asyncio
async def test_webhook_one_branch_failure_does_not_cancel_or_block_the_other() -> None:
    renderer = _FakeRenderer()
    # History service raises error on get_system_history
    failing_service = _FakeHistoryService(
        history_exc=RuntimeError("Hub history query failed"),
        detail=SystemDetailView(
            summary=SystemSummary(id="s1", name="Node", status="up"),
            metrics=SystemMetrics(stats={"cpu": 80.0}),
        ),
    )
    provider = _FakeProvider(completion_text="AI诊断成功")
    context = _FakeContext(providers={"target:umo1": provider})

    config = WebhookConfig(enabled=True, target_umos=("target:umo1",))
    delivery = WebhookDelivery(
        context=context, config=config, service=failing_service, renderer=renderer
    )
    notification = NormalizedNotification(
        request_id="fail-isol",
        source=NotificationSource.BESZEL,
        title="Title",
        message="Msg",
        history_system_id="s1",
        send_history=True,
        send_analysis=True,
    )

    await delivery.deliver(notification)
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    # History failed, but raw text and analysis were neither blocked nor cancelled.
    sent_kinds = sorted(_message_kind(chain) for _, chain in context.calls)
    assert sent_kinds == ["analysis", "text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "systems"),
    [
        # A Beszel alert whose title and link match no known system.
        (
            NotificationSource.BESZEL,
            [SystemSummary(id="sys-other", name="Other Host", status="up")],
        ),
        # An unsupported source is never bound, even when its link is known.
        (
            NotificationSource.UPTIME_KUMA,
            [SystemSummary(id="sys-1", name="Atlas", status="up")],
        ),
    ],
)
async def test_webhook_optional_branches_skip_unbound_or_unsupported_alerts(
    source: NotificationSource, systems: list[SystemSummary]
) -> None:
    provider = _FakeProvider()
    context = _FakeContext(providers={"target:umo1": provider})
    renderer = _FakeRenderer()
    service = _FakeHistoryService(systems=systems)
    delivery = WebhookDelivery(
        context=context,
        config=WebhookConfig(enabled=True, target_umos=("target:umo1",)),
        service=service,
        renderer=renderer,
    )
    notification = NormalizedNotification(
        request_id="req-unbound",
        source=source,
        title="Atlas",
        message="Open /system/sys-1",
        send_history=True,
        send_analysis=True,
    )

    assert await delivery.deliver(notification) == 1
    await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    assert [type(chain.chain[0]) for _, chain in context.calls] == [Plain]
    assert provider.calls == []
    assert renderer.rendered_views == []
    assert "get_system_detail" not in service.calls
    assert "get_system_history" not in service.calls
    if source is NotificationSource.UPTIME_KUMA:
        # Unsupported sources never schedule optional work or query the Hub.
        assert service.calls == []


@pytest.mark.asyncio
async def test_webhook_cancelled_history_branch_does_not_cancel_analysis() -> None:
    renderer = _FakeRenderer()
    renderer.hang_event = asyncio.Event()
    release_llm = asyncio.Event()
    provider = _FakeProvider(completion_text="诊断结果", hang_event=release_llm)
    context = _FakeContext(providers={"target:umo1": provider})
    summary = SystemSummary(id="s1", name="Node", status="up")
    service = _FakeHistoryService(
        view=SystemHistoryView(summary=summary, range=HistoryRange.ONE_HOUR),
        detail=SystemDetailView(
            summary=summary, metrics=SystemMetrics(stats={"cpu": 80.0})
        ),
    )
    delivery = WebhookDelivery(
        context=context,
        config=WebhookConfig(enabled=True, target_umos=("target:umo1",)),
        service=service,
        renderer=renderer,
    )
    notification = NormalizedNotification(
        request_id="cancel-history",
        source=NotificationSource.BESZEL,
        title="T",
        message="M",
        history_system_id="s1",
        send_history=True,
        send_analysis=True,
    )

    await delivery.deliver(notification)
    tasks = {task.get_name(): task for task in delivery._background_tasks}
    history_task = tasks["history-cancel-history"]
    analysis_task = tasks["analysis-cancel-history"]

    history_task.cancel()
    await asyncio.gather(history_task, return_exceptions=True)
    assert history_task.cancelled()
    assert not analysis_task.done()

    release_llm.set()
    await analysis_task
    await delivery.close()

    assert renderer.rendered_views == []
    analysis_targets = [
        target
        for target, chain in context.calls
        if "[Beszel AI 诊断]" in chain.get_plain_text()
    ]
    assert analysis_targets == ["target:umo1"]


@pytest.mark.asyncio
async def test_webhook_analysis_timeout_does_not_cancel_history_branch() -> None:
    release_render = asyncio.Event()
    renderer = _FakeRenderer()
    renderer.hang_event = release_render
    # The provider never answers, so the analysis branch can only time out.
    provider = _FakeProvider(hang_event=asyncio.Event())
    context = _FakeContext(providers={"target:umo1": provider})
    summary = SystemSummary(id="s1", name="Node", status="up")
    service = _FakeHistoryService(
        view=SystemHistoryView(summary=summary, range=HistoryRange.ONE_HOUR),
        detail=SystemDetailView(
            summary=summary, metrics=SystemMetrics(stats={"cpu": 80.0})
        ),
    )
    delivery = WebhookDelivery(
        context=context,
        config=WebhookConfig(
            enabled=True,
            target_umos=("target:umo1",),
            analysis_timeout_seconds=0.1,
        ),
        service=service,
        renderer=renderer,
    )
    notification = NormalizedNotification(
        request_id="timeout-history",
        source=NotificationSource.BESZEL,
        title="T",
        message="M",
        history_system_id="s1",
        send_history=True,
        send_analysis=True,
    )

    await delivery.deliver(notification)
    tasks = {task.get_name(): task for task in delivery._background_tasks}
    history_task = tasks["history-timeout-history"]

    await tasks["analysis-timeout-history"]
    assert not history_task.done()

    release_render.set()
    await history_task
    await delivery.close()

    sent_kinds = [type(chain.chain[0]) for _, chain in context.calls]
    assert sent_kinds.count(Image) == 1
    assert not any(
        "[Beszel AI 诊断]" in chain.get_plain_text() for _, chain in context.calls
    )


@pytest.mark.asyncio
async def test_webhook_history_branch_isolates_target_failures() -> None:
    def fail_first_image(target: str, chain) -> None:
        if target == "target:a" and isinstance(chain.chain[0], Image):
            raise RuntimeError("platform unavailable")

    context = _FakeContext(on_send=fail_first_image)
    renderer = _FakeRenderer()
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="s1", name="Node", status="up"),
            range=HistoryRange.ONE_HOUR,
        )
    )
    delivery = WebhookDelivery(
        context=context,
        config=WebhookConfig(
            enabled=True, target_umos=("target:a", "target:b", "target:c")
        ),
        service=service,
        renderer=renderer,
    )
    notification = NormalizedNotification(
        request_id="image-isolation",
        source=NotificationSource.BESZEL,
        title="T",
        message="M",
        history_system_id="s1",
        send_history=True,
    )

    assert await delivery.deliver(notification) == 3
    await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    image_targets = [
        target for target, chain in context.calls if isinstance(chain.chain[0], Image)
    ]
    assert image_targets == ["target:a", "target:b", "target:c"]
    assert len(renderer.rendered_views) == 1
    assert service.calls.count("get_system_history") == 1


@pytest.mark.asyncio
async def test_webhook_analysis_provider_degradations(caplog, monkeypatch) -> None:
    astrbot_logger = logging.getLogger("astrbot")
    monkeypatch.setattr(astrbot_logger, "propagate", True)
    caplog.set_level(logging.DEBUG, logger="astrbot")

    providers = {
        "target:no_prov": None,
        "target:select_exc": ValueError("invalid provider type"),
        "target:exc": _FakeProvider(exc=RuntimeError("LLM API error")),
        "target:err_role": _FakeProvider(role="err", completion_text="Some error"),
        "target:empty": _FakeProvider(completion_text="   "),
        "target:send_false": _FakeProvider(completion_text="诊断结果"),
        "target:send_exc": _FakeProvider(completion_text="诊断结果"),
        "target:good": _FakeProvider(completion_text="正常诊断结果"),
    }
    targets = tuple(providers.keys())
    context = _FakeContext(
        outcomes={
            ("target:send_false", "analysis"): [False],
            ("target:send_exc", "analysis"): [RuntimeError("platform down")],
        },
        providers=providers,
    )

    detail = SystemDetailView(
        summary=SystemSummary(id="s1", name="Node", status="up"),
        metrics=SystemMetrics(stats={"cpu": 80.0}),
    )
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="s1", name="Node", status="up"),
            range=HistoryRange.ONE_HOUR,
        ),
        detail=detail,
    )
    config = WebhookConfig(enabled=True, target_umos=targets)
    delivery = WebhookDelivery(
        context=context,
        config=config,
        service=service,
        renderer=_FakeRenderer(),
    )

    notification = NormalizedNotification(
        request_id="deg-1",
        source=NotificationSource.BESZEL,
        title="Title",
        message="Msg",
        history_system_id="s1",
        send_analysis=True,
    )

    successes = await delivery.deliver(notification)
    assert successes == len(targets)

    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    # Only targets with a usable reply attempt delivery; failed sends stay isolated.
    analysis_attempts = [
        t for t, chain in context.calls if _message_kind(chain) == "analysis"
    ]
    assert analysis_attempts == ["target:send_false", "target:send_exc", "target:good"]
    assert (
        "No chat completion provider available for target=target:no_prov" in caplog.text
    )
    assert (
        "provider selection failed for target=target:select_exc (request_id=deg-1): "
        "ValueError" in caplog.text
    )
    assert "LLM text_chat failed for target=target:exc" in caplog.text
    assert "had non-assistant role: err" in caplog.text
    assert "completion_text was empty for target=target:empty" in caplog.text
    assert (
        "analysis message delivery rejected or unhandled for target=target:send_false"
        in caplog.text
    )
    assert (
        "analysis delivery failed for target=target:send_exc (request_id=deg-1): "
        "RuntimeError" in caplog.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("context_cls", [_FakeContext, _FakeAsyncProviderContext])
async def test_webhook_analysis_provider_lookup_supports_sync_and_async_contexts(
    context_cls, caplog, monkeypatch
) -> None:
    astrbot_logger = logging.getLogger("astrbot")
    monkeypatch.setattr(astrbot_logger, "propagate", True)
    caplog.set_level(logging.DEBUG, logger="astrbot")

    provider = _FakeProvider(completion_text="诊断结果")
    providers = {
        "target:no_prov": None,
        "target:select_exc": ValueError("invalid provider type"),
        "target:good": provider,
    }
    targets = tuple(providers)
    context = context_cls(providers=providers)
    detail = SystemDetailView(
        summary=SystemSummary(id="s1", name="Node", status="up"),
        metrics=SystemMetrics(stats={"cpu": 80.0}),
    )
    delivery = WebhookDelivery(
        context=context,
        config=WebhookConfig(enabled=True, target_umos=targets),
        service=_FakeHistoryService(detail=detail),
        renderer=_FakeRenderer(),
    )
    notification = NormalizedNotification(
        request_id="lookup-1",
        source=NotificationSource.BESZEL,
        title="Title",
        message="Msg",
        history_system_id="s1",
        send_analysis=True,
    )

    assert await delivery.deliver(notification) == len(targets)
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    assert context.provider_lookups == list(targets)
    assert len(provider.calls) == 1
    analysis_targets = [
        t for t, chain in context.calls if _message_kind(chain) == "analysis"
    ]
    assert analysis_targets == ["target:good"]
    assert (
        "No chat completion provider available for target=target:no_prov" in caplog.text
    )
    assert (
        "provider selection failed for target=target:select_exc (request_id=lookup-1): "
        "ValueError" in caplog.text
    )


@pytest.mark.asyncio
async def test_webhook_analysis_timeout_handling(caplog, monkeypatch) -> None:
    astrbot_logger = logging.getLogger("astrbot")
    monkeypatch.setattr(astrbot_logger, "propagate", True)
    caplog.set_level(logging.DEBUG, logger="astrbot")

    hang_ev = asyncio.Event()
    providers = {
        "target:slow": _FakeProvider(hang_event=hang_ev),
        "target:fast": _FakeProvider(completion_text="快速诊断"),
    }
    context = _FakeContext(providers=providers)
    detail = SystemDetailView(
        summary=SystemSummary(id="s1", name="Node", status="up"),
        metrics=SystemMetrics(stats={"cpu": 80.0}),
    )
    service = _FakeHistoryService(
        view=SystemHistoryView(
            summary=SystemSummary(id="s1", name="Node", status="up"),
            range=HistoryRange.ONE_HOUR,
        ),
        detail=detail,
    )
    config = WebhookConfig(
        enabled=True,
        target_umos=("target:slow", "target:fast"),
        analysis_timeout_seconds=0.1,
    )
    delivery = WebhookDelivery(
        context=context,
        config=config,
        service=service,
        renderer=_FakeRenderer(),
    )

    notification = NormalizedNotification(
        request_id="timeout-1",
        source=NotificationSource.BESZEL,
        title="Title",
        message="Msg",
        history_system_id="s1",
        send_analysis=True,
    )

    await delivery.deliver(notification)
    if delivery._background_tasks:
        await asyncio.gather(*list(delivery._background_tasks))
    await delivery.close()

    analysis_calls = [
        t for t, chain in context.calls if "[Beszel AI 诊断]" in chain.get_plain_text()
    ]
    assert analysis_calls == ["target:fast"]
    assert "Webhook analysis timed out for target=target:slow" in caplog.text


@pytest.mark.asyncio
async def test_webhook_analysis_prompt_customization() -> None:
    provider = _FakeProvider()
    context = _FakeContext(providers={"target:umo1": provider})
    detail = SystemDetailView(
        summary=SystemSummary(id="s1", name="Node", status="up"),
        metrics=SystemMetrics(stats={"cpu": 80.0}),
    )
    service = _FakeHistoryService(detail=detail)

    # 1. Default prompt
    config_default = WebhookConfig(
        enabled=True, target_umos=("target:umo1",), analysis_prompt=""
    )
    delivery_default = WebhookDelivery(
        context=context,
        config=config_default,
        service=service,
        renderer=_FakeRenderer(),
    )
    n = NormalizedNotification(
        request_id="p1",
        source=NotificationSource.BESZEL,
        title="",
        message="",
        history_system_id="s1",
        send_analysis=True,
    )
    await delivery_default.deliver(n)
    if delivery_default._background_tasks:
        await asyncio.gather(*list(delivery_default._background_tasks))
    await delivery_default.close()

    assert provider.calls[0]["system_prompt"] == DEFAULT_ANALYSIS_PROMPT

    # 2. Custom prompt completely replaces default
    custom_prompt = "你是专有集群诊断智能体，仅输出简报。"
    config_custom = WebhookConfig(
        enabled=True, target_umos=("target:umo1",), analysis_prompt=custom_prompt
    )
    delivery_custom = WebhookDelivery(
        context=context,
        config=config_custom,
        service=service,
        renderer=_FakeRenderer(),
    )
    await delivery_custom.deliver(n)
    if delivery_custom._background_tasks:
        await asyncio.gather(*list(delivery_custom._background_tasks))
    await delivery_custom.close()

    assert provider.calls[1]["system_prompt"] == custom_prompt
    assert DEFAULT_ANALYSIS_PROMPT not in provider.calls[1]["system_prompt"]


@pytest.mark.asyncio
async def test_webhook_delivery_close_cancels_and_is_idempotent() -> None:
    hang_ev = asyncio.Event()
    renderer = _FakeRenderer()
    renderer.hang_event = hang_ev

    config = WebhookConfig(enabled=True, target_umos=("target:umo1",))
    context = _FakeContext()
    delivery = WebhookDelivery(
        context=context,
        config=config,
        service=_FakeHistoryService(),
        renderer=renderer,
    )
    n = NormalizedNotification(
        request_id="cancel-1",
        source=NotificationSource.BESZEL,
        title="T",
        message="M",
        history_system_id="s1",
        send_history=True,
    )

    successes = await delivery.deliver(n)
    assert successes == 1
    assert len(delivery._background_tasks) == 1

    # First close: cancels tasks and calls wait_idle
    await delivery.close()
    assert len(delivery._background_tasks) == 0
    assert renderer.wait_idle_called is True

    # Second close: safe and idempotent
    await delivery.close()

    # Deliver after close returns 0 and does not spawn tasks
    post_successes = await delivery.deliver(n)
    assert post_successes == 0
    assert len(delivery._background_tasks) == 0
    assert [_message_kind(chain) for _, chain in context.calls] == ["text"]


@pytest.mark.asyncio
async def test_webhook_delivery_close_waits_for_started_native_render() -> None:
    loop = asyncio.get_running_loop()
    native_entered = asyncio.Event()
    finish_native = threading.Event()

    class _BlockingEngine:
        def __init__(self) -> None:
            self.calls = 0

        def render(self, *args, **kwargs) -> bytes:
            self.calls += 1
            loop.call_soon_threadsafe(native_entered.set)
            finish_native.wait(10)
            return b"\x89PNG\r\n\x1a\nfake"

    renderer = BeszelRenderer(
        plugin_name="test",
        show_connection_address=False,
        display_timezone=UTC,
        font_path=None,
    )
    engine = _BlockingEngine()
    renderer._engine = engine
    context = _FakeContext()
    summary = SystemSummary(id="s1", name="Node", status="up")
    delivery = WebhookDelivery(
        context=context,
        config=WebhookConfig(enabled=True, target_umos=("target:umo1",)),
        service=_FakeHistoryService(
            view=SystemHistoryView(summary=summary, range=HistoryRange.ONE_HOUR)
        ),
        renderer=renderer,
    )
    n = NormalizedNotification(
        request_id="cancel-real-1",
        source=NotificationSource.BESZEL,
        title="T",
        message="M",
        history_system_id="s1",
        send_history=True,
    )

    try:
        await delivery.deliver(n)
        await asyncio.wait_for(native_entered.wait(), timeout=5)

        close_task = asyncio.create_task(delivery.close())
        # Cancelling the branch cannot stop the native thread, so close waits.
        done, _ = await asyncio.wait({close_task}, timeout=0.2)
        assert not done

        finish_native.set()
        await asyncio.wait_for(close_task, timeout=5)
    finally:
        finish_native.set()
        renderer.close()

    assert engine.calls == 1
    # The render finished after close began, so its image is never delivered.
    assert [_message_kind(chain) for _, chain in context.calls] == ["text"]


@pytest.mark.asyncio
async def test_webhook_server_stop_drains_handlers_before_closing_delivery() -> None:
    order: list[str] = []

    class _Runner:
        def __init__(self, exc: Exception | None = None) -> None:
            self.exc = exc

        async def cleanup(self) -> None:
            order.append("runner.cleanup")
            if self.exc is not None:
                raise self.exc

    class _Delivery:
        async def close(self) -> None:
            order.append("delivery.close")

    server = WebhookServer(
        WebhookConfig(enabled=True), _Delivery(), _FakeHistoryService()
    )
    server._runner = _Runner()
    await server.stop()
    assert order == ["runner.cleanup", "delivery.close"]
    assert server._runner is None

    # Optional tasks are still reclaimed when draining the runner fails.
    order.clear()
    server._runner = _Runner(RuntimeError("cleanup failed"))
    with pytest.raises(RuntimeError):
        await server.stop()
    assert order == ["runner.cleanup", "delivery.close"]
    assert server._runner is None
