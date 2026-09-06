from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    ContainerHistoryPoint,
    HistoryRange,
    SystemDetailView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemMetrics,
    SystemSummary,
)
from astrbot_plugin_beszel.core.rendering.models import ChartUnit
from astrbot_plugin_beszel.core.rendering.presenter import PresentationBuilder


def _builder(rendering_data) -> PresentationBuilder:
    return PresentationBuilder(
        plugin_name=rendering_data["plugin_name"],
        show_connection_address=True,
        display_timezone=ZoneInfo(rendering_data["display_timezone"]),
    )


def test_overview_presentation_uses_current_fields(
    overview_data, rendering_data
) -> None:
    systems = [SystemSummary.model_validate(item) for item in overview_data]
    document = _builder(rendering_data).build_overview(
        systems[:4],
        page_number=1,
        page_count=3,
        summary_counts=(len(systems), 7, 1),
    )

    assert document.header.title == "所有客户端"
    assert document.header.subtitle == "共 9 个探针节点"
    assert len(document.rows) == 4
    assert document.rows[0].name == overview_data[0]["name"]
    assert document.rows[0].status_state == "up"
    assert document.footer.label.startswith(rendering_data["plugin_name"])


def test_status_presentation_maps_metrics_and_containers(
    status_data, rendering_data
) -> None:
    view = SystemDetailView.model_validate(status_data)
    document = _builder(rendering_data).build_status(view)

    assert document.header.title == status_data["summary"]["name"]
    assert document.header.status is not None
    assert document.header.status.state == "up"
    assert {card.label for card in document.metric_cards} == {
        "CPU 使用率",
        "内存使用率",
        "网络实时带宽",
    }
    assert [row.name for row in document.containers] == sorted(
        row.name for row in document.containers
    )
    assert any(section.title == "独立显卡监控 (GPU)" for section in document.sections)


def test_status_presentation_bandwidth_zero_and_absent_states(
    status_data, models_data, rendering_data
) -> None:
    # 1. Valid sample without bandwidth displays 0 B/s
    view_zero = SystemDetailView.model_validate(status_data)
    view_zero.metrics = SystemMetrics.model_validate(
        models_data["sample_without_bandwidth"]
    )
    doc_zero = _builder(rendering_data).build_status(view_zero)
    net_card_zero = next(
        card for card in doc_zero.metric_cards if card.label == "网络实时带宽"
    )
    assert net_card_zero.value == 0.0
    assert net_card_zero.value_text == "0.0 B/s"
    assert net_card_zero.secondary_text == "↓ 0.0 B/s · ↑ 0.0 B/s"

    # 2. Absent metrics record stays unavailable ("N/A")
    view_none = SystemDetailView.model_validate(status_data)
    view_none.metrics = None
    doc_none = _builder(rendering_data).build_status(view_none)
    net_card_none = next(
        card for card in doc_none.metric_cards if card.label == "网络实时带宽"
    )
    assert net_card_none.value is None
    assert net_card_none.value_text == "N/A"
    assert net_card_none.secondary_text == ""


def test_history_presentation_splits_missing_samples(
    history_gap_data, rendering_data
) -> None:
    view = SystemHistoryView.model_validate(history_gap_data)
    document = _builder(rendering_data).build_history(view)

    assert document.header.title == history_gap_data["summary"]["name"]
    assert document.header.range_label == "1 小时"
    assert document.header.connection_address == (
        f"{history_gap_data['summary']['host']}:{history_gap_data['summary']['port']}"
    )
    assert document.cards
    assert all(card.series for card in document.cards)
    assert any(
        len(series.segments) > 1 for card in document.cards for series in card.series
    )


def test_history_presentation_preserves_idle_middle_sample_without_false_gap(
    history_gap_data, models_data, rendering_data
) -> None:
    points = [
        SystemHistoryPoint.model_validate(item)
        for item in models_data["consecutive_minute_points"]
    ]
    view = SystemHistoryView(
        summary=SystemSummary.model_validate(history_gap_data["summary"]),
        range=HistoryRange.ONE_HOUR,
        points=points,
    )
    document = _builder(rendering_data).build_history(view)

    net_card = next(card for card in document.cards if card.title == "带宽")
    assert len(net_card.series) == 2
    for series in net_card.series:
        assert len(series.points) == 3
        assert series.points[1].value == 0.0
        assert len(series.segments) == 1
        assert len(series.segments[0]) == 3


def test_container_history_cards_generation_and_threshold_filtering(
    history_data, container_history_data, rendering_data
) -> None:
    # Build container points from fixture (10 containers across timestamps)
    container_points = []
    for raw in container_history_data["records"]:
        m = ContainerHistoryMetrics.model_validate(raw)
        if m.created is not None:
            container_points.append(
                ContainerHistoryPoint(created=m.created, stats=m.stats)
            )

    view = SystemHistoryView.model_validate(history_data)
    view.container_points = container_points
    document = _builder(rendering_data).build_history(view)

    # Check that container cards were interleaved right after their respective host cards (Option A)
    titles = [card.title for card in document.cards]
    assert "容器 CPU 使用率" in titles
    assert "容器内存使用" in titles
    assert titles.index("容器 CPU 使用率") == titles.index("CPU 使用率") + 1
    assert titles.index("容器内存使用") == titles.index("内存使用") + 1

    cpu_card = next(c for c in document.cards if c.title == "容器 CPU 使用率")
    mem_card = next(c for c in document.cards if c.title == "容器内存使用")

    # Default threshold 10%: 6 containers kept, 4 omitted
    assert len(cpu_card.series) == 6
    assert len(mem_card.series) == 6
    assert cpu_card.unit == ChartUnit.PERCENT
    assert mem_card.unit == ChartUnit.BYTES

    # extra_series_count indicates the omitted containers (10 total - 6 = 4)
    assert cpu_card.extra_series_count == 4
    assert mem_card.extra_series_count == 4

    # Memory unit conversion: check that values are converted from MiB to bytes
    db_mem_series = next(s for s in mem_card.series if s.name == "db")
    assert db_mem_series.current_value is not None
    # 2080 MiB in bytes is > 2 GB (2080 * 1024 * 1024)
    assert db_mem_series.current_value >= 2000 * 1024 * 1024

    # Color determinism: the same container name has the identical color in CPU and Memory cards
    cpu_colors = {s.name: s.color for s in cpu_card.series}
    mem_colors = {s.name: s.color for s in mem_card.series}
    common_names = set(cpu_colors.keys()) & set(mem_colors.keys())
    assert len(common_names) == 6  # all kept containers appear in both
    for name in common_names:
        assert cpu_colors[name] == mem_colors[name]

    # Threshold = 0: full display of all 10 containers without omission
    builder_all = PresentationBuilder(
        plugin_name=rendering_data["plugin_name"],
        show_connection_address=True,
        display_timezone=ZoneInfo(rendering_data["display_timezone"]),
        container_history_threshold=0,
    )
    doc_all = builder_all.build_history(view)
    cpu_card_all = next(c for c in doc_all.cards if c.title == "容器 CPU 使用率")
    mem_card_all = next(c for c in doc_all.cards if c.title == "容器内存使用")
    assert len(cpu_card_all.series) == 10
    assert cpu_card_all.extra_series_count == 0
    assert len(mem_card_all.series) == 10
    assert mem_card_all.extra_series_count == 0

    # Threshold = 20%: stricter filtering
    builder_20 = PresentationBuilder(
        plugin_name=rendering_data["plugin_name"],
        show_connection_address=True,
        display_timezone=ZoneInfo(rendering_data["display_timezone"]),
        container_history_threshold=20,
    )
    doc_20 = builder_20.build_history(view)
    cpu_card_20 = next(c for c in doc_20.cards if c.title == "容器 CPU 使用率")
    mem_card_20 = next(c for c in doc_20.cards if c.title == "容器内存使用")
    assert len(cpu_card_20.series) == 5
    assert cpu_card_20.extra_series_count == 5
    assert len(mem_card_20.series) == 3
    assert mem_card_20.extra_series_count == 7


def test_container_history_gaps_and_empty_handling(
    history_data, container_history_data, rendering_data
) -> None:
    # 1. Empty container history creates no container cards
    view_empty = SystemHistoryView.model_validate(history_data)
    view_empty.container_points = []
    doc_empty = _builder(rendering_data).build_history(view_empty)
    assert not any("容器" in card.title for card in doc_empty.cards)

    # 2. Gap records: 15-minute gap in batch-job produces 2 distinct segments
    gap_points = []
    for raw in container_history_data["gap_records"]:
        m = ContainerHistoryMetrics.model_validate(raw)
        if m.created is not None:
            gap_points.append(ContainerHistoryPoint(created=m.created, stats=m.stats))

    view_gap = SystemHistoryView.model_validate(history_data)
    view_gap.container_points = gap_points
    doc_gap = _builder(rendering_data).build_history(view_gap)
    cpu_card = next(c for c in doc_gap.cards if c.title == "容器 CPU 使用率")
    batch_series = cpu_card.series[0]
    assert batch_series.name == "batch-job"
    assert len(batch_series.segments) == 2


def test_history_cards_ordering_matches_beszel_hub_option_a(
    history_data, container_history_data, rendering_data
) -> None:
    container_points = []
    for raw in container_history_data["records"]:
        m = ContainerHistoryMetrics.model_validate(raw)
        if m.created is not None:
            container_points.append(
                ContainerHistoryPoint(created=m.created, stats=m.stats)
            )

    view = SystemHistoryView.model_validate(history_data)
    view.container_points = container_points
    doc = _builder(rendering_data).build_history(view)

    titles = [card.title for card in doc.cards]
    # Verify complete sequence matching Beszel Hub default layout:
    # CPU -> Container CPU -> Memory -> Container Memory -> Root Disk -> Disk I/O ->
    # Bandwidth -> Swap -> Load -> Temperature -> Fan -> Battery -> GPU -> Extra FS
    expected_full_titles = [
        "CPU 使用率",
        "容器 CPU 使用率",
        "内存使用",
        "容器内存使用",
        "磁盘使用",
        "磁盘 I/O",
        "带宽",
        "Swap 交换空间",
        "系统负载",
        "温度",
        "风扇",
        "电池电量",
        "Demo GPU 12GB 功耗",
        "Demo GPU 12GB 使用",
        "Demo GPU 12GB VRAM",
        "Archive 使用",
        "Archive I/O",
        "Projects 使用",
        "Projects I/O",
    ]
    assert titles == expected_full_titles


def test_fan_and_temperature_dynamic_series_colors(
    history_data, rendering_data
) -> None:
    # Construct history points with multiple fans and temperature sensors
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    fan_point = SystemHistoryPoint(
        created=now,
        stats={
            "cpu": 25.0,
            "f": {"fan_case": 1200, "fan_cpu": 1800},
            "t": {"CPU Core 1": 45.0, "CPU Core 2": 48.0},
        },
    )
    view = SystemHistoryView(
        summary=SystemSummary.model_validate(history_data["summary"]),
        range=HistoryRange.ONE_HOUR,
        points=[fan_point],
    )
    doc = _builder(rendering_data).build_history(view)

    fan_card = next(c for c in doc.cards if c.title == "风扇")
    assert len(fan_card.series) == 2
    # 2 fans must receive complementary hues (0 deg: Red, 180 deg: Cyan)
    f0_color = fan_card.series[0].color
    f1_color = fan_card.series[1].color
    assert f0_color == (209, 71, 71)
    assert f1_color == (71, 209, 209)

    temp_card = next(c for c in doc.cards if c.title == "温度")
    assert len(temp_card.series) == 2
    t0_color = temp_card.series[0].color
    t1_color = temp_card.series[1].color
    assert t0_color == (209, 71, 71)
    assert t1_color == (71, 209, 209)
