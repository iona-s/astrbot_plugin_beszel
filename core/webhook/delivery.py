"""Synchronous best-effort webhook fan-out."""

from __future__ import annotations

from astrbot.api import logger
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from ..beszel.models import HistoryRange
from ..beszel.service import QueryService
from ..config import WebhookConfig
from ..rendering.renderer import BeszelRenderer
from .models import NormalizedNotification


class WebhookDelivery:
    """Deliver each target in configured order, isolating individual failures."""

    def __init__(
        self,
        context,
        config: WebhookConfig,
        service: QueryService,
        renderer: BeszelRenderer,
    ) -> None:
        self.context = context
        self.config = config
        self.service = service
        self.renderer = renderer

    async def deliver(self, notification: NormalizedNotification) -> int:
        logger.debug(
            "WebhookDelivery: starting delivery for request_id=%s (source=%s, title=%s) to %d targets",
            notification.request_id,
            notification.source.value,
            notification.title,
            len(self.config.target_umos),
        )
        image_bytes: bytes | None = None
        if notification.history_system_id is not None:
            try:
                view = await self.service.get_system_history(
                    notification.history_system_id, HistoryRange.ONE_HOUR
                )
                image_bytes = await self.renderer.render_history(view)
                logger.debug(
                    "WebhookDelivery: rendered 1h history image for system_id=%s (size=%d bytes)",
                    notification.history_system_id,
                    len(image_bytes),
                )
            except Exception as exc:
                logger.warning(
                    "Webhook history rendering failed: %s", type(exc).__name__
                )

        text_successes = text_failures = image_successes = image_failures = 0
        for target in self.config.target_umos:
            try:
                sent = await self.context.send_message(
                    target, MessageChain([Plain(self._text(notification))])
                )
                if not sent:
                    text_failures += 1
                    logger.warning(
                        "Webhook text delivery rejected or unhandled for target=%s",
                        target,
                    )
                    continue
                text_successes += 1
                logger.debug("WebhookDelivery: text sent to target=%s", target)
            except Exception as exc:
                text_failures += 1
                logger.warning(
                    "Webhook text delivery failed for target=%s: %s",
                    target,
                    type(exc).__name__,
                )
                continue

            if image_bytes is not None:
                try:
                    sent = await self.context.send_message(
                        target, MessageChain([Image.fromBytes(image_bytes)])
                    )
                    if not sent:
                        image_failures += 1
                        logger.warning(
                            "Webhook image delivery rejected or unhandled for target=%s",
                            target,
                        )
                    else:
                        image_successes += 1
                        logger.debug(
                            "WebhookDelivery: history image sent to target=%s",
                            target,
                        )
                except Exception as exc:
                    image_failures += 1
                    logger.warning(
                        "Webhook image delivery failed for target=%s: %s",
                        target,
                        type(exc).__name__,
                    )
        logger.debug(
            "WebhookDelivery: completed delivery for request_id=%s: text=%d/%d image=%d/%d",
            notification.request_id,
            text_successes,
            text_failures,
            image_successes,
            image_failures,
        )
        return text_successes

    @staticmethod
    def _text(notification: NormalizedNotification) -> str:
        return f"[{notification.source.value}] {notification.title}\n{notification.message}"
