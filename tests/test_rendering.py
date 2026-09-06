from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    ContainerHistoryPoint,
    SystemDetailView,
    SystemHistoryView,
    SystemSummary,
)
from astrbot_plugin_beszel.core.rendering.renderer import BeszelRenderer
from astrbot_plugin_beszel.core.rendering.templates.environment import (
    BeszelTemplateRenderer,
)


def _renderer(rendering_data) -> BeszelRenderer:
    return BeszelRenderer(
        plugin_name=rendering_data["plugin_name"],
        show_connection_address=True,
        display_timezone=ZoneInfo(rendering_data["display_timezone"]),
        font_path=None,
    )


def test_templates_render_fixture_documents(
    overview_data, status_data, history_data, rendering_data
) -> None:
    renderer = _renderer(rendering_data)
    try:
        systems = [SystemSummary.model_validate(item) for item in overview_data]
        overview = renderer.presentation.build_overview(
            systems[:3], page_number=1, page_count=1, summary_counts=(9, 7, 1)
        )
        status = renderer.presentation.build_status(
            SystemDetailView.model_validate(status_data)
        )
        history = renderer.presentation.build_history(
            SystemHistoryView.model_validate(history_data)
        )
        templates = BeszelTemplateRenderer()

        overview_markup = templates.render_overview(overview)
        status_markup = templates.render_status(status)
        history_markup = templates.render_history(
            history, timezone=ZoneInfo(rendering_data["display_timezone"])
        )

        assert overview_data[0]["name"] in overview_markup
        assert status_data["summary"]["name"] in status_markup
        assert "1 小时" in history_markup
        assert overview_markup.lstrip().startswith("<!doctype html>")
    finally:
        renderer.close()


@pytest.mark.asyncio
async def test_pytakumi_renders_fixture_views_to_png(
    overview_data, status_data, history_data, rendering_data
) -> None:
    renderer = _renderer(rendering_data)
    try:
        systems = [SystemSummary.model_validate(item) for item in overview_data]
        overview_pngs = await renderer.render_overview(systems, page_size=4)
        status_png = await renderer.render_status(
            SystemDetailView.model_validate(status_data)
        )
        history_png = await renderer.render_history(
            SystemHistoryView.model_validate(history_data)
        )

        outputs = [*overview_pngs, status_png, history_png]
        assert len(overview_pngs) == 3
        assert all(
            png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1024 for png in outputs
        )
    finally:
        renderer.close()


@pytest.mark.asyncio
async def test_pytakumi_renders_container_history_scenarios(
    history_data, container_history_data, rendering_data
) -> None:
    renderer = _renderer(rendering_data)
    try:
        # 1. Single container scenario
        single_points = [
            ContainerHistoryPoint(created=m.created, stats=m.stats)
            for raw in container_history_data["single_container_records"]
            if (m := ContainerHistoryMetrics.model_validate(raw)).created is not None
        ]
        view_single = SystemHistoryView.model_validate(history_data)
        view_single.container_points = single_points
        single_png = await renderer.render_history(view_single)
        assert single_png.startswith(b"\x89PNG\r\n\x1a\n") and len(single_png) > 1024

        # 2. Scenario exercising default 10% threshold filtering and omitted +4
        high_points = [
            ContainerHistoryPoint(created=m.created, stats=m.stats)
            for raw in container_history_data["records"]
            if (m := ContainerHistoryMetrics.model_validate(raw)).created is not None
        ]
        view_high = SystemHistoryView.model_validate(history_data)
        view_high.container_points = high_points
        high_png = await renderer.render_history(view_high)
        assert high_png.startswith(b"\x89PNG\r\n\x1a\n") and len(high_png) > 1024

        # Verify HTML markup includes container titles and +4 indicator
        doc_high = renderer.presentation.build_history(view_high)
        templates = BeszelTemplateRenderer()
        markup = templates.render_history(
            doc_high, timezone=ZoneInfo(rendering_data["display_timezone"])
        )
        assert "容器 CPU 使用率" in markup
        assert "容器内存使用" in markup
        assert "+4" in markup
    finally:
        renderer.close()
