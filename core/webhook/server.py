"""aiohttp webhook server lifecycle and HTTP semantics."""

from __future__ import annotations

import secrets
from uuid import uuid4

from aiohttp import web
from astrbot.api import logger

from ..beszel.service import QueryService
from ..config import WebhookConfig
from .delivery import WebhookDelivery
from .parsers import (
    WebhookPayloadError,
    attach_history,
    history_requested,
    parse_payload,
)


class WebhookServer:
    """Own an aiohttp runner/site inside the plugin event loop."""

    def __init__(
        self,
        config: WebhookConfig,
        delivery: WebhookDelivery,
        service: QueryService,
    ) -> None:
        self.config = config
        self.delivery = delivery
        self.service = service
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        if not self.config.enabled or self._runner is not None:
            return
        app = web.Application(client_max_size=2 * 1024 * 1024)
        app.router.add_get("/healthz", self.healthz)
        app.router.add_post(self.config.path, self.notify)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.config.host, self.config.port)
        try:
            await self._site.start()
        except OSError:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.error("Webhook listener could not bind; webhook disabled")

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def healthz(self, _: web.Request) -> web.Response:
        logger.debug(
            "Webhook healthz check requested: ready=%s", self._runner is not None
        )
        return web.json_response({"ready": self._runner is not None})

    async def notify(self, request: web.Request) -> web.Response:
        request_id = uuid4().hex
        auth = request.headers.get("Authorization", "")
        has_bearer = auth.startswith("Bearer ")
        content_type = request.headers.get("Content-Type", "")
        logger.debug(
            "Webhook request received: id=%s method=%s path=%s content_type=%s auth=%s",
            request_id,
            request.method,
            request.path,
            content_type,
            "Bearer <present>"
            if has_bearer
            else ("<empty>" if not auth else "<invalid>"),
        )
        expected = f"Bearer {self.config.token}"
        if not has_bearer or not secrets.compare_digest(auth, expected):
            logger.debug("Webhook request id=%s failed authentication", request_id)
            return web.json_response(
                {"request_id": request_id, "status": "unauthorized"}, status=401
            )
        try:
            body = await request.read()
            notification = parse_payload(
                body,
                content_type=content_type,
                headers=request.headers,
                request_id=request_id,
            )
            logger.debug(
                "Webhook request id=%s parsed notification: source=%s, title=%s, send_history=%s",
                request_id,
                notification.source.value,
                notification.title,
                notification.send_history,
            )
            if history_requested(notification):
                try:
                    systems = await self.service.list_systems()
                    known_systems = {system.id: system.name for system in systems}
                    notification = attach_history(notification, known_systems)
                    logger.debug(
                        "Webhook request id=%s resolved history target: system_id=%s",
                        request_id,
                        notification.history_system_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Webhook history system resolution failed: %s",
                        type(exc).__name__,
                    )
        except WebhookPayloadError as exc:
            status = exc.status
            logger.debug(
                "Webhook request id=%s payload parse error: %s -> HTTP %d",
                request_id,
                exc,
                status,
            )
            return web.json_response(
                {"request_id": request_id, "status": "invalid"}, status=status
            )
        try:
            text_successes = await self.delivery.deliver(notification)
        except Exception as exc:
            logger.error(
                "Webhook request id=%s delivery raised %s",
                request_id,
                type(exc).__name__,
                exc_info=True,
            )
            return web.json_response(
                {"request_id": request_id, "status": "delivery_error"}, status=500
            )
        if text_successes == 0:
            logger.debug(
                "Webhook request id=%s all deliveries failed -> HTTP 502", request_id
            )
            return web.json_response(
                {"request_id": request_id, "status": "delivery_failed"}, status=502
            )
        logger.debug(
            "Webhook request id=%s delivery successful -> HTTP 200", request_id
        )
        return web.json_response({"request_id": request_id, "status": "delivered"})
