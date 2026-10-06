"""Asynchronous public rendering facade for Beszel image views."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import tzinfo
from pathlib import Path

from ..beszel.models import SystemDetailView, SystemHistoryView, SystemSummary
from ..errors import RenderingError
from ..formatters import status_state
from .engine import PytakumiEngine
from .presenter import PresentationBuilder
from .styles import RENDER_WIDTH, STATUS_RENDER_WIDTH
from .templates.environment import BeszelTemplateRenderer


class BeszelRenderer:
    """Compose presentation, template, and Pytakumi layers behind async methods."""

    _bundled_font_path = (
        Path(__file__).resolve().parent.parent
        / "assets"
        / "fonts"
        / "NotoSansSC-Regular.otf"
    )

    def __init__(
        self,
        *,
        plugin_name: str,
        show_connection_address: bool,
        display_timezone: tzinfo,
        font_path: str | Path | None,
        container_history_threshold: int = 10,
        render_scale: int = 100,
    ) -> None:
        self.presentation = PresentationBuilder(
            plugin_name=plugin_name,
            show_connection_address=show_connection_address,
            display_timezone=display_timezone,
            container_history_threshold=container_history_threshold,
        )
        self.templates = BeszelTemplateRenderer()
        self._font_path = font_path
        self._render_scale = render_scale
        self._engine: PytakumiEngine | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._active_renders: int = 0
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()
        self._closed = False

    def _scaled_dimensions(self, base_width: int) -> tuple[int, float]:
        target_width = round(base_width * self._render_scale / 100)
        dpr = self._render_scale / 100.0
        return target_width, dpr

    @property
    def engine(self) -> PytakumiEngine:
        if self._engine is None:
            raise RenderingError("🖼️ 图片渲染引擎尚未初始化，请稍后重试")
        return self._engine

    async def initialize(self) -> None:
        """Initialize the native engine and font registration off the event loop.

        Raises:
            RenderingError: The renderer is closed or the engine cannot start.
        """
        if self._closed:
            raise RenderingError("🖼️ 图片渲染引擎已关闭")
        if self._engine is not None:
            return
        engine = await asyncio.to_thread(
            PytakumiEngine,
            bundled_font_path=self._bundled_font_path,
            configured_font_path=self._font_path,
            stylesheet=self.templates.stylesheet,
        )
        if self._closed:
            raise RenderingError("🖼️ 图片渲染引擎已关闭")
        self._engine = engine

    async def render_overview(
        self, systems: list[SystemSummary], page_size: int
    ) -> list[bytes]:
        """Render one PNG per page, sequentially, or no pages for empty input."""
        if not systems:
            return []
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        await self.initialize()
        page_count = (len(systems) + page_size - 1) // page_size
        online_count = 0
        offline_count = 0
        for system in systems:
            state = status_state(system.status)
            if state == "up":
                online_count += 1
            elif state == "down":
                offline_count += 1
        summary_counts = (len(systems), online_count, offline_count)
        outputs: list[bytes] = []
        width, dpr = self._scaled_dimensions(RENDER_WIDTH)
        for page_number, start in enumerate(range(0, len(systems), page_size), start=1):
            page = systems[start : start + page_size]
            document = self.presentation.build_overview(
                page,
                page_number=page_number,
                page_count=page_count,
                summary_counts=summary_counts,
            )
            markup = self.templates.render_overview(document)
            outputs.append(await self._render_native(markup, width=width, dpr=dpr))
        return outputs

    async def render_status(self, view: SystemDetailView) -> bytes:
        await self.initialize()
        document = self.presentation.build_status(view)
        markup = self.templates.render_status(document)
        width, dpr = self._scaled_dimensions(STATUS_RENDER_WIDTH)
        return await self._render_native(markup, width=width, dpr=dpr)

    async def render_history(self, view: SystemHistoryView) -> bytes:
        await self.initialize()
        document = self.presentation.build_history(view)
        markup = self.templates.render_history(
            document,
            timezone=self.presentation.display_timezone,
        )
        width, dpr = self._scaled_dimensions(RENDER_WIDTH)
        return await self._render_native(markup, width=width, dpr=dpr)

    async def _render_native(self, markup: str, *, width: int, dpr: float) -> bytes:
        """Run one native render in the renderer-owned thread pool.

        Completion is tracked on the worker future rather than on the awaiting
        coroutine: cancelling a caller cannot stop a render that already started,
        so ``wait_idle`` must keep waiting until the worker really finishes.

        Args:
            markup: Rendered template markup.
            width: Output width in CSS pixels.
            dpr: Device pixel ratio forwarded to the engine.

        Returns:
            Encoded PNG bytes.

        Raises:
            RenderingError: The renderer is closed or the render fails.
        """
        if self._closed:
            raise RenderingError("🖼️ 图片渲染引擎已关闭")
        engine = self.engine
        if self._executor is None:
            self._executor = ThreadPoolExecutor(thread_name_prefix="beszel-render")
        loop = asyncio.get_running_loop()
        future = self._executor.submit(
            engine.render, markup, width=width, device_pixel_ratio=dpr
        )
        self._active_renders += 1
        self._idle_event.clear()
        future.add_done_callback(
            lambda _future: loop.call_soon_threadsafe(self._on_render_finished)
        )
        return await asyncio.wrap_future(future, loop=loop)

    def _on_render_finished(self) -> None:
        self._active_renders -= 1
        if self._active_renders <= 0:
            self._active_renders = 0
            self._idle_event.set()

    async def wait_idle(self) -> None:
        """Wait for any in-flight native thread rendering operations to complete."""
        await self._idle_event.wait()

    def close(self) -> None:
        """Drop the native engine and release the render pool without blocking.

        Renders already submitted still finish and release ``wait_idle``; later
        calls fail instead of recreating the engine or the pool.
        """
        self._closed = True
        self._engine = None
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
