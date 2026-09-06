from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    ContainerHistoryPoint,
    SystemDetailView,
    SystemHistoryView,
    SystemSummary,
)
from astrbot_plugin_beszel.core.rendering.models import (
    ChartPoint,
    ChartSeries,
    ChartUnit,
    DocumentFooter,
    DocumentHeader,
    HistoryChartCard,
    HistoryDocument,
)
from astrbot_plugin_beszel.core.rendering.renderer import BeszelRenderer
from astrbot_plugin_beszel.core.rendering.templates.charts import (
    TICK_LABEL_WIDTH,
    build_chart_view,
)
from astrbot_plugin_beszel.core.rendering.templates.environment import (
    BeszelTemplateRenderer,
)


def _png_dimensions(png: bytes) -> tuple[int, int]:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return width, height


def _renderer(rendering_data, render_scale: int = 100) -> BeszelRenderer:
    return BeszelRenderer(
        plugin_name=rendering_data["plugin_name"],
        show_connection_address=True,
        display_timezone=ZoneInfo(rendering_data["display_timezone"]),
        font_path=None,
        render_scale=render_scale,
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
        assert ".table-meter-track" in templates.stylesheet
        assert "border-radius: 9999px" in templates.stylesheet
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


def test_chart_geometry_single_point_segment_has_marker() -> None:
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    point = ChartPoint(created=now, value=42.0)
    series = ChartSeries(
        name="test",
        color=(255, 0, 0),
        points=(point,),
        segments=((point,),),
        current_value=42.0,
    )
    card = HistoryChartCard(
        title="单点测试",
        subtitle="",
        unit=ChartUnit.PERCENT,
        series=(series,),
        axis_min=0.0,
        axis_max=100.0,
    )
    view = build_chart_view(card, timezone=UTC)
    assert view.geometry is not None
    assert len(view.geometry.segments) == 1
    seg = view.geometry.segments[0]
    assert len(seg.markers) == 1
    assert seg.line_path is None
    assert seg.area_path is None

    # Test template renders the circle marker
    doc = HistoryDocument(
        header=DocumentHeader(title="单点测试"),
        cards=(card,),
        footer=DocumentFooter(label=""),
    )
    templates = BeszelTemplateRenderer()
    markup = templates.render_history(doc, timezone=UTC)
    assert '<circle class="chart-marker"' in markup


def test_chart_geometry_data_gap_isolated_single_point() -> None:
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    # Multi-point segment
    p1 = ChartPoint(created=now, value=10.0)
    p2 = ChartPoint(created=now + timedelta(minutes=1), value=20.0)
    p3 = ChartPoint(created=now + timedelta(minutes=2), value=30.0)
    # Gap of 100 minutes, followed by isolated single point
    p4 = ChartPoint(created=now + timedelta(minutes=102), value=40.0)

    series = ChartSeries(
        name="gap_test",
        color=(0, 128, 255),
        points=(p1, p2, p3, p4),
        segments=((p1, p2, p3), (p4,)),
        current_value=40.0,
    )
    card = HistoryChartCard(
        title="缺口单点测试",
        subtitle="",
        unit=ChartUnit.PERCENT,
        series=(series,),
        axis_min=0.0,
        axis_max=100.0,
    )
    view = build_chart_view(card, timezone=UTC)
    assert view.geometry is not None
    assert len(view.geometry.segments) == 2
    seg_multi = view.geometry.segments[0]
    seg_single = view.geometry.segments[1]

    # Multi-point segment has curve and area, but no markers
    assert seg_multi.line_path is not None
    assert seg_multi.area_path is not None
    assert seg_multi.markers == ()

    # Single-point segment has 1 marker, and no curve/area
    assert seg_single.line_path is None
    assert seg_single.area_path is None
    assert len(seg_single.markers) == 1

    doc = HistoryDocument(
        header=DocumentHeader(title="缺口测试"),
        cards=(card,),
        footer=DocumentFooter(label=""),
    )
    templates = BeszelTemplateRenderer()
    markup = templates.render_history(doc, timezone=UTC)
    assert '<circle class="chart-marker"' in markup
    assert '<path class="chart-line"' in markup


def test_chart_multi_series_ticks_cover_full_time_span_and_stay_in_bounds() -> None:
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    end_time = now + timedelta(hours=1)

    # Series A: sorted first (e.g. higher peak metric), but only has 1 sample at the end
    point_a = ChartPoint(created=end_time, value=99.0)
    series_a = ChartSeries(
        name="container-a",
        color=(255, 0, 0),
        points=(point_a,),
        segments=((point_a,),),
        current_value=99.0,
    )

    # Series B: runs across the full 1-hour range with irregular sampling
    points_b = tuple(
        ChartPoint(created=now + timedelta(minutes=m), value=20.0)
        for m in [0, 1, 2, 30, 60]
    )
    series_b = ChartSeries(
        name="container-b",
        color=(0, 128, 255),
        points=points_b,
        segments=(points_b,),
        current_value=20.0,
    )

    card = HistoryChartCard(
        title="多序列时间轴测试",
        subtitle="",
        unit=ChartUnit.PERCENT,
        series=(series_a, series_b),
        axis_min=0.0,
        axis_max=100.0,
    )

    view = build_chart_view(card, timezone=UTC)
    assert view.geometry is not None
    ticks = view.geometry.ticks
    assert len(ticks) >= 2

    curr = 0.0
    boxes: list[tuple[float, float, str, str]] = []
    for t in ticks:
        left = curr + t.margin_left
        right = left + TICK_LABEL_WIDTH
        boxes.append((left, right, t.label, t.anchor))
        curr = right

    # 1. Start and end ticks represent the global time range, not series A's isolated point
    assert boxes[0][2] == "12:00"
    assert boxes[0][3] == "start"
    assert boxes[-1][2] == "13:00"
    assert boxes[-1][3] == "end"

    # 2. All tick boxes stay within plot_left and plot_right bounds
    assert boxes[0][0] >= view.geometry.plot_left - 1e-6
    assert boxes[-1][1] <= view.geometry.plot_right + 1e-6

    # 3. No adjacent tick boxes overlap
    for k in range(len(boxes) - 1):
        assert boxes[k + 1][0] >= boxes[k][1] - 1e-6


def test_pytakumi_engine_forwards_device_pixel_ratio() -> None:
    from astrbot_plugin_beszel.core.rendering.engine import PytakumiEngine

    engine = PytakumiEngine(
        bundled_font_path=BeszelRenderer._bundled_font_path,
        configured_font_path=None,
    )
    captured: dict[str, object] = {}

    class FakeNativeRenderer:
        def render(
            self,
            source,
            *,
            width,
            height,
            format,
            stylesheets,
            font_families,
            lang,
            device_pixel_ratio,
        ):
            captured["width"] = width
            captured["height"] = height
            captured["device_pixel_ratio"] = device_pixel_ratio
            return b"\x89PNG\r\n\x1a\n" + b"\x00" * 30

    engine._renderer = FakeNativeRenderer()
    png = engine.render(
        "<html><body>test</body></html>", width=800, device_pixel_ratio=2.5
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert captured["width"] == 800
    assert captured["device_pixel_ratio"] == 2.5


@pytest.mark.asyncio
async def test_beszel_renderer_forwards_scaled_width_and_dpr_to_engine(
    overview_data, status_data, history_data, rendering_data
) -> None:
    systems = [SystemSummary.model_validate(item) for item in overview_data]
    status_view = SystemDetailView.model_validate(status_data)
    history_view = SystemHistoryView.model_validate(history_data)

    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def render(
            self,
            markup: str,
            *,
            width: int,
            height: int | None = None,
            device_pixel_ratio: float = 1.0,
        ) -> bytes:
            self.calls.append(
                {
                    "width": width,
                    "height": height,
                    "device_pixel_ratio": device_pixel_ratio,
                }
            )
            return b"\x89PNG\r\n\x1a\nfake"

    test_cases = [
        # (scale, expected_dpr, expected_history_overview_width, expected_status_width)
        (100, 1.0, 1200, 800),
        (200, 2.0, 2400, 1600),
        (50, 0.5, 600, 400),
    ]

    for (
        scale,
        expected_dpr,
        expected_overview_width,
        expected_status_width,
    ) in test_cases:
        renderer = _renderer(rendering_data, render_scale=scale)
        fake_engine = FakeEngine()
        renderer._engine = fake_engine

        # 1. Overview
        await renderer.render_overview(systems, page_size=len(systems))
        assert len(fake_engine.calls) == 1
        assert fake_engine.calls[0]["width"] == expected_overview_width
        assert fake_engine.calls[0]["device_pixel_ratio"] == expected_dpr

        # 2. Status
        fake_engine.calls.clear()
        await renderer.render_status(status_view)
        assert len(fake_engine.calls) == 1
        assert fake_engine.calls[0]["width"] == expected_status_width
        assert fake_engine.calls[0]["device_pixel_ratio"] == expected_dpr

        # 3. History
        fake_engine.calls.clear()
        await renderer.render_history(history_view)
        assert len(fake_engine.calls) == 1
        assert fake_engine.calls[0]["width"] == expected_overview_width
        assert fake_engine.calls[0]["device_pixel_ratio"] == expected_dpr


@pytest.mark.asyncio
async def test_render_scale_dimensions_and_normalized_height(
    overview_data, status_data, history_data, rendering_data
) -> None:
    systems = [SystemSummary.model_validate(item) for item in overview_data]
    status_view = SystemDetailView.model_validate(status_data)
    history_view = SystemHistoryView.model_validate(history_data)

    scale_cases = [
        (100, 1200, 800),
        (200, 2400, 1600),
        (50, 600, 400),
    ]

    results: dict[int, dict[str, tuple[int, int]]] = {}

    for scale, expected_history_width, expected_status_width in scale_cases:
        renderer = _renderer(rendering_data, render_scale=scale)
        try:
            overview_pngs = await renderer.render_overview(
                systems, page_size=len(systems)
            )
            status_png = await renderer.render_status(status_view)
            history_png = await renderer.render_history(history_view)

            assert len(overview_pngs) == 1
            overview_w, overview_h = _png_dimensions(overview_pngs[0])
            status_w, status_h = _png_dimensions(status_png)
            history_w, history_h = _png_dimensions(history_png)

            assert overview_w == expected_history_width
            assert history_w == expected_history_width
            assert status_w == expected_status_width

            results[scale] = {
                "overview": (overview_w, overview_h),
                "status": (status_w, status_h),
                "history": (history_w, history_h),
            }
        finally:
            renderer.close()

    # Verify normalized height tolerance: abs(h_scaled / dpr - h_base) / h_base <= 3%
    base_overview_h = results[100]["overview"][1]
    base_status_h = results[100]["status"][1]
    base_history_h = results[100]["history"][1]

    for scale, dpr in [(50, 0.5), (200, 2.0)]:
        overview_norm_h = results[scale]["overview"][1] / dpr
        status_norm_h = results[scale]["status"][1] / dpr
        history_norm_h = results[scale]["history"][1] / dpr

        assert abs(overview_norm_h - base_overview_h) / base_overview_h <= 0.03
        assert abs(status_norm_h - base_status_h) / base_status_h <= 0.03
        assert abs(history_norm_h - base_history_h) / base_history_h <= 0.03

    # Multipage overview dimension consistency check at 150%
    renderer_multi = _renderer(rendering_data, render_scale=150)
    try:
        multi_pngs = await renderer_multi.render_overview(systems, page_size=4)
        assert len(multi_pngs) == 3
        for png in multi_pngs:
            w, _h = _png_dimensions(png)
            assert w == 1800
    finally:
        renderer_multi.close()
