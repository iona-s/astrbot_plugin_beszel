from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
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
    overview_data, status_data, history_data, rendering_data, tmp_path
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
        for index, png in enumerate(outputs):
            path = tmp_path / f"fixture-{index}.png"
            path.write_bytes(png)
            assert path.stat().st_size == len(png)
    finally:
        renderer.close()
