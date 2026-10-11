"""Independent asynchronous webhook fan-out delivery."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.provider import Provider
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from ..analysis import (
    DEFAULT_ANALYSIS_PROMPT,
    AnalysisRecord,
    add_analysis_history,
    extract_analysis_context,
    format_analysis_reply,
    summarize_analysis,
)
from ..beszel.models import HistoryRange, SystemDetailView, SystemHistoryView
from ..beszel.service import QueryService
from ..config import WebhookConfig
from ..rendering.renderer import BeszelRenderer
from .models import NormalizedNotification
from .parsers import analysis_requested, attach_history, history_requested


class WebhookDelivery:
    """Deliver raw text, history image, and AI analysis via independent async branches."""

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
        self._background_tasks: set[asyncio.Task] = set()
        self._closed: bool = False
        # Kept in memory only, so a restart or plugin reload starts fresh.
        self._analysis_records: dict[str, deque[AnalysisRecord]] = {}
        self._analysis_tails: dict[str, asyncio.Task] = {}

    async def deliver(self, notification: NormalizedNotification) -> int:
        """Send the raw text and schedule the enabled optional branches.

        Only the raw-text fan-out is awaited. The history image and analysis
        branches run as tracked background tasks over all configured targets,
        independent of the raw-text outcome.

        Args:
            notification: The authenticated and normalized webhook notification.

        Returns:
            The number of targets that accepted the raw text, which alone
            decides the HTTP status.
        """
        if self._closed:
            return 0

        logger.debug(
            "WebhookDelivery: starting delivery for request_id=%s (source=%s, title=%s) to %d targets",
            notification.request_id,
            notification.source.value,
            notification.title,
            len(self.config.target_umos),
        )

        # Schedule independent background tasks only for sources that can bind a node.
        if history_requested(notification):
            self._spawn_task(
                self._deliver_history(notification),
                name=f"history-{notification.request_id}",
            )

        if analysis_requested(notification):
            # Capture the analysis reference time once, when the request is accepted.
            self._spawn_task(
                self._deliver_analysis(notification, datetime.now(UTC)),
                name=f"analysis-{notification.request_id}",
            )

        # Await raw text delivery across all configured target UMOs in the request
        text_successes = 0
        raw_message = MessageChain([Plain(self._text(notification))])
        for target in self.config.target_umos:
            try:
                sent = await self.context.send_message(target, raw_message)
                if not sent:
                    logger.warning(
                        "Webhook text delivery rejected or unhandled for target=%s",
                        target,
                    )
                    continue
                text_successes += 1
                logger.debug("WebhookDelivery: text sent to target=%s", target)
            except Exception as exc:
                logger.warning(
                    "Webhook text delivery failed for target=%s: %s",
                    target,
                    type(exc).__name__,
                )

        logger.debug(
            "WebhookDelivery: completed raw text delivery for request_id=%s: %d/%d successes",
            notification.request_id,
            text_successes,
            len(self.config.target_umos),
        )
        return text_successes

    def _spawn_task(self, coro: Coroutine[Any, Any, None], *, name: str) -> None:
        # Keep a strong reference until the task finishes; ``deliver`` has
        # already rejected new work after ``close``.
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "Webhook background delivery error in %s: %s",
                    task.get_name(),
                    type(exc).__name__,
                    exc_info=exc,
                )

    async def _resolve_system(
        self, notification: NormalizedNotification
    ) -> NormalizedNotification:
        if notification.history_system_id is not None:
            return notification
        try:
            systems = await self.service.list_systems()
            known_systems = {system.id: system.name for system in systems}
            return attach_history(notification, known_systems)
        except Exception as exc:
            logger.warning(
                "Webhook system resolution failed for request_id=%s: %s",
                notification.request_id,
                type(exc).__name__,
            )
            return notification

    async def _deliver_history(self, notification: NormalizedNotification) -> None:
        notification = await self._resolve_system(notification)
        if not notification.history_system_id or not notification.send_history:
            logger.debug(
                "Webhook history skipped: node could not be resolved (request_id=%s)",
                notification.request_id,
            )
            return

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
                "Webhook history rendering failed for request_id=%s: %s",
                notification.request_id,
                type(exc).__name__,
            )
            return

        if self._closed or not image_bytes:
            return

        chain = MessageChain([Image.fromBytes(image_bytes)])
        for target in self.config.target_umos:
            if self._closed:
                break
            try:
                sent = await self.context.send_message(target, chain)
                if not sent:
                    logger.warning(
                        "Webhook image delivery rejected or unhandled for target=%s (request_id=%s)",
                        target,
                        notification.request_id,
                    )
                else:
                    logger.debug(
                        "WebhookDelivery: history image sent to target=%s (request_id=%s)",
                        target,
                        notification.request_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Webhook image delivery failed for target=%s (request_id=%s): %s",
                    target,
                    notification.request_id,
                    type(exc).__name__,
                )

    async def _deliver_analysis(
        self, notification: NormalizedNotification, reference_time: datetime
    ) -> None:
        notification = await self._resolve_system(notification)
        system_id = notification.history_system_id
        if not system_id or not notification.send_analysis:
            logger.debug(
                "Webhook analysis skipped: node could not be resolved (request_id=%s)",
                notification.request_id,
            )
            return

        # Analyses of one node finish in acceptance order, so a later alert such
        # as a recovery can include the diagnosis of an earlier one.
        previous_task = self._analysis_tails.get(system_id)
        current_task = asyncio.current_task()
        self._analysis_tails[system_id] = current_task
        try:
            detail: SystemDetailView | None = None
            try:
                detail = await self.service.get_system_detail(system_id)
            except Exception as exc:
                logger.warning(
                    "Webhook analysis detail query failed (request_id=%s): %s",
                    notification.request_id,
                    type(exc).__name__,
                )

            history: SystemHistoryView | None = None
            try:
                history = await self.service.get_system_history(
                    system_id, HistoryRange.ONE_HOUR
                )
            except Exception as exc:
                logger.warning(
                    "Webhook analysis history query failed (request_id=%s): %s",
                    notification.request_id,
                    type(exc).__name__,
                )

            user_prompt = extract_analysis_context(
                notification, detail, history, reference_time
            )
            if previous_task is not None:
                await asyncio.wait([previous_task])
            if user_prompt is None:
                logger.debug(
                    "Webhook analysis skipped (request_id=%s): no valid metrics or input exceeded limits",
                    notification.request_id,
                )
                return

            records = self._analysis_records.setdefault(
                system_id, deque(maxlen=self.config.analysis_context_max_records)
            )
            prompt, history_count = add_analysis_history(
                user_prompt,
                records,
                reference_time,
                recent_hours=self.config.analysis_context_recent_hours,
                context_hours=self.config.analysis_context_hours,
            )
            logger.debug(
                "WebhookDelivery: analysis for system_id=%s includes %d earlier diagnoses (request_id=%s)",
                system_id,
                history_count,
                notification.request_id,
            )

            reply_text = await self._generate_analysis(prompt, notification.request_id)
            if reply_text is None or self._closed:
                return
            if self.config.analysis_context_hours:
                records.append(
                    summarize_analysis(user_prompt, reply_text, reference_time)
                )

            chain = MessageChain([Plain(reply_text)])
            for target in self.config.target_umos:
                if self._closed:
                    break
                try:
                    sent = await self.context.send_message(target, chain)
                    if not sent:
                        logger.warning(
                            "Webhook analysis message delivery rejected or unhandled for target=%s (request_id=%s)",
                            target,
                            notification.request_id,
                        )
                    else:
                        logger.debug(
                            "WebhookDelivery: analysis sent to target=%s (request_id=%s)",
                            target,
                            notification.request_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "Webhook analysis delivery failed for target=%s (request_id=%s): %s",
                        target,
                        notification.request_id,
                        type(exc).__name__,
                    )
        finally:
            if self._analysis_tails.get(system_id) is current_task:
                del self._analysis_tails[system_id]

    async def _generate_analysis(
        self,
        user_prompt: str,
        request_id: str,
    ) -> str | None:
        """Call the chat model once on behalf of every target.

        Args:
            user_prompt: Alert JSON, including any earlier diagnoses as ``history``.
            request_id: Correlation ID used in logs.

        Returns:
            The diagnosis with ``DIAGNOSIS_PREFIX``, or None when no provider is
            available or the call fails, times out, or returns no usable text.
        """
        provider: Provider | None = None
        if self.config.analysis_provider_id:
            found = self.context.get_provider_by_id(self.config.analysis_provider_id)
            if isinstance(found, Provider):
                provider = found
            else:
                logger.warning(
                    "Configured analysis provider %s is missing or not a chat provider; "
                    "using a target session provider (request_id=%s)",
                    self.config.analysis_provider_id,
                    request_id,
                )

        if provider is None:
            # AstrBot 4.27.3 deprecates the sync lookup, which may query the
            # database on the event loop; 4.25 only provides the sync form.
            provider_getter = getattr(self.context, "get_using_provider_async", None)
            for target in self.config.target_umos:
                try:
                    if provider_getter is not None:
                        provider = await provider_getter(target)
                    else:
                        provider = self.context.get_using_provider(target)
                except Exception as exc:
                    logger.warning(
                        "Webhook provider selection failed for target=%s (request_id=%s): %s",
                        target,
                        request_id,
                        type(exc).__name__,
                    )
                    continue
                if provider is not None:
                    break

        if provider is None:
            logger.warning(
                "No chat completion provider available for webhook analysis (request_id=%s)",
                request_id,
            )
            return None

        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=user_prompt,
                    system_prompt=self.config.analysis_prompt
                    or DEFAULT_ANALYSIS_PROMPT,
                    contexts=[],
                    func_tool=None,
                ),
                timeout=self.config.analysis_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "Webhook analysis timed out (request_id=%s, timeout=%ds)",
                request_id,
                self.config.analysis_timeout_seconds,
            )
            return None
        except Exception as exc:
            logger.warning(
                "LLM text_chat failed (request_id=%s): %s",
                request_id,
                type(exc).__name__,
            )
            return None

        if getattr(resp, "role", None) != "assistant":
            logger.warning(
                "LLM response (request_id=%s) had non-assistant role: %s",
                request_id,
                getattr(resp, "role", None),
            )
            return None

        reply_text = format_analysis_reply(getattr(resp, "completion_text", None))
        if not reply_text:
            logger.warning(
                "LLM response completion_text was empty (request_id=%s)", request_id
            )
            return None
        return reply_text

    async def close(self) -> None:
        """Cancel and await all pending background tasks, then wait for renderer idle."""
        self._closed = True
        pending = list(self._background_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()
        # Cancelling a task cannot stop a native render that already started.
        await self.renderer.wait_idle()

    @staticmethod
    def _text(notification: NormalizedNotification) -> str:
        return f"[{notification.source.value}] {notification.title}\n{notification.message}"
