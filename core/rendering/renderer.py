"""Asynchronous public rendering facade for Beszel image views."""

from __future__ import annotations

import asyncio
from datetime import tzinfo
from pathlib import Path

from ..beszel.models import SystemDetailView, SystemHistoryView, SystemSummary
from ..errors import RenderingError
from ..formatters import status_state
from .engine import PytakumiEngine
from .presenter import PresentationBuilder
from .styles import RENDER_WIDTH
from .templates import BeszelTemplateRenderer


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
        plugin_name: str = "Beszel",
        show_connection_address: bool = False,
        display_timezone: tzinfo | None = None,
        font_path: str | Path | None = None,
    ) -> None:
        self.presentation = PresentationBuilder(
            plugin_name=plugin_name,
            show_connection_address=show_connection_address,
            display_timezone=display_timezone,
        )
        self.templates = BeszelTemplateRenderer()
        self._font_path = font_path
        self._engine: PytakumiEngine | None = None

    @property
    def engine(self) -> PytakumiEngine:
        if self._engine is None:
            raise RenderingError("图片渲染引擎尚未初始化")
        return self._engine

    async def initialize(self) -> None:
        """Initialize the native engine and font registration off the event loop."""
        if self._engine is not None:
            return
        self._engine = await asyncio.to_thread(
            PytakumiEngine,
            bundled_font_path=self._bundled_font_path,
            configured_font_path=self._font_path,
            stylesheet=self.templates.stylesheet,
        )

    async def render_overview(
        self, systems: list[SystemSummary], page_size: int = 10
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
        summary_counts = (online_count, offline_count)
        outputs: list[bytes] = []
        for page_number, start in enumerate(range(0, len(systems), page_size), start=1):
            page = systems[start : start + page_size]
            document = self.presentation.build_overview(
                page,
                page_number=page_number,
                page_count=page_count,
                summary_systems=systems,
                summary_counts=summary_counts,
            )
            markup = self.templates.render_overview(document)
            outputs.append(
                await asyncio.to_thread(
                    self.engine.render,
                    markup,
                    width=RENDER_WIDTH,
                )
            )
        return outputs

    async def render_status(self, view: SystemDetailView) -> bytes:
        await self.initialize()
        document = self.presentation.build_status(view)
        markup = self.templates.render_status(document)
        return await asyncio.to_thread(
            self.engine.render,
            markup,
            width=RENDER_WIDTH,
        )

    async def render_history(self, view: SystemHistoryView) -> bytes:
        await self.initialize()
        document = self.presentation.build_history(view)
        markup = self.templates.render_history(
            document,
            timezone=self.presentation.display_timezone,
        )
        return await asyncio.to_thread(
            self.engine.render,
            markup,
            width=RENDER_WIDTH,
        )

    def close(self) -> None:
        """Drop the native engine reference so its resources can be reclaimed."""
        self._engine = None
