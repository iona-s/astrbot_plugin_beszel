from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    ContainerHistoryPoint,
    HistoryRange,
    StoragePoolState,
    SystemDetailView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemMetrics,
    SystemSummary,
)
from astrbot_plugin_beszel.core.rendering.models import ChartUnit
from astrbot_plugin_beszel.core.rendering.presenter import PresentationBuilder
from astrbot_plugin_beszel.core.rendering.styles import dynamic_series_color


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


def test_history_cards_share_the_requested_window(
    history_data, container_history_data, rendering_data
) -> None:
    view = SystemHistoryView.model_validate(history_data)
    view.container_points = [
        ContainerHistoryPoint(created=m.created, stats=m.stats)
        for raw in container_history_data["records"]
        if (m := ContainerHistoryMetrics.model_validate(raw)).created is not None
    ]
    first_sample = view.points[0].created
    last_sample = view.points[-1].created
    window_end = last_sample + timedelta(minutes=30)
    window_start = window_end - view.range.duration
    builder = _builder(rendering_data)

    in_range = view.model_copy(
        update={
            "points": [p for p in view.points if p.created >= window_start],
            "container_points": [],
            "window_end": window_end,
        }
    )
    assert {
        (card.time_start, card.time_end)
        for card in builder.build_history(in_range).cards
    } == {(window_start, window_end)}

    # Samples older than the range widen the window instead of being clipped.
    widened = view.model_copy(update={"window_end": window_end})
    document = builder.build_history(widened)
    assert any(card.title == "容器 CPU 使用率" for card in document.cards)
    assert {(card.time_start, card.time_end) for card in document.cards} == {
        (first_sample, window_end)
    }

    # Without a query time the newest sample ends the window.
    fallback = builder.build_history(view)
    assert {(card.time_start, card.time_end) for card in fallback.cards} == {
        (last_sample - view.range.duration, last_sample)
    }


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
    # 2080 MiB in bytes is > 2 GB (2080 * 1024 * 1024)
    assert db_mem_series.points[-1].value >= 2000 * 1024 * 1024

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


def test_container_history_keeps_top_container_when_all_fall_below_cutoff(
    history_data, container_history_data, rendering_data
) -> None:
    builder = _builder(rendering_data)

    idle_points = []
    for raw in container_history_data["idle_records"]:
        m = ContainerHistoryMetrics.model_validate(raw)
        if m.created is not None:
            idle_points.append(ContainerHistoryPoint(created=m.created, stats=m.stats))
    view_idle = SystemHistoryView.model_validate(history_data)
    view_idle.container_points = idle_points
    doc_idle = builder.build_history(view_idle)

    # Every CPU peak is below the default 10% cutoff of the 10% axis floor.
    cpu_card = next(c for c in doc_idle.cards if c.title == "容器 CPU 使用率")
    assert [s.name for s in cpu_card.series] == ["proxy"]
    assert cpu_card.extra_series_count == 2
    mem_card = next(c for c in doc_idle.cards if c.title == "容器内存使用")
    assert [s.name for s in mem_card.series] == ["proxy", "agent", "cron"]
    assert mem_card.extra_series_count == 0

    zero_memory_points = []
    for raw in container_history_data["zero_memory_records"]:
        m = ContainerHistoryMetrics.model_validate(raw)
        if m.created is not None:
            zero_memory_points.append(
                ContainerHistoryPoint(created=m.created, stats=m.stats)
            )
    view_zero = SystemHistoryView.model_validate(history_data)
    view_zero.container_points = zero_memory_points
    doc_zero = builder.build_history(view_zero)

    cpu_card = next(c for c in doc_zero.cards if c.title == "容器 CPU 使用率")
    assert [s.name for s in cpu_card.series] == ["proxy", "agent"]
    assert cpu_card.extra_series_count == 0
    mem_card = next(c for c in doc_zero.cards if c.title == "容器内存使用")
    assert [s.name for s in mem_card.series] == ["agent"]
    assert mem_card.extra_series_count == 1


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
    # Bandwidth -> Container Network -> Swap -> Load -> Temperature -> Fan ->
    # Battery -> GPU -> Extra FS -> Storage pools (minus duplicates of the above)
    expected_full_titles = [
        "CPU 使用率",
        "容器 CPU 使用率",
        "内存使用",
        "容器内存使用",
        "磁盘使用",
        "磁盘 I/O",
        "带宽",
        "容器网络 I/O",
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
        "tank 使用",
        "tank I/O",
    ]
    assert titles == expected_full_titles
    # Extra filesystems and pools start their own grids, as on the Hub.
    assert [card.title for card in doc.cards if card.starts_section] == [
        "Archive 使用",
        "tank 使用",
    ]


def test_container_network_card_sums_sent_and_received(
    history_data, container_history_data, rendering_data
) -> None:
    view = SystemHistoryView.model_validate(history_data)
    view.container_points = [
        ContainerHistoryPoint(created=m.created, stats=m.stats)
        for raw in container_history_data["records"]
        if (m := ContainerHistoryMetrics.model_validate(raw)).created is not None
    ]
    document = _builder(rendering_data).build_history(view)

    net_card = next(c for c in document.cards if c.title == "容器网络 I/O")
    assert net_card.subtitle == "容器网络流量"
    assert net_card.unit == ChartUnit.BYTES_PER_SECOND
    # backup peaks at 2.49 MB/s, so the default 10% cutoff (of a 2.86 MB/s
    # axis) drops worker at 0.27 MB/s; containers without "b" read 0.
    assert [s.name for s in net_card.series] == ["backup", "web", "db"]
    assert net_card.extra_series_count == 7
    web = next(s for s in net_card.series if s.name == "web")
    assert web.points[0].value == 180_000 + 1_250_000

    cpu_card = next(c for c in document.cards if c.title == "容器 CPU 使用率")
    cpu_colors = {s.name: s.color for s in cpu_card.series}
    for series in net_card.series:
        assert series.color == cpu_colors[series.name]


def test_history_names_root_disk_charts_like_the_hub(
    history_data, rendering_data
) -> None:
    view = SystemHistoryView.model_validate(history_data)
    view.summary.info["rdn"] = "SSD-SATA"
    document = _builder(rendering_data).build_history(view)

    cards = {card.title: card for card in document.cards}
    assert cards["SSD-SATA 使用"].subtitle == "SSD-SATA的磁盘使用"
    assert cards["SSD-SATA I/O"].subtitle == "SSD-SATA的吞吐量"
    assert "磁盘使用" not in cards


def test_storage_pool_charts_follow_the_newest_sample(
    history_data, rendering_data
) -> None:
    start = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    older = {
        "tank": {"d": 100.0, "du": 40.0, "rb": 1000, "wb": 2000, "h": "ONLINE"},
        "raidz": {"d": 30.0, "du": 10.0},
        "gone": {"d": 50.0, "du": 5.0},
    }
    newer = {
        # rb and wb are omitted when zero.
        "tank": {"d": 100.0, "du": 41.0, "h": "DEGRADED"},
        # The pool switched to raw capacity; its usable sample above is dropped.
        "raidz": {"d": 60.0, "du": 24.0, "raw": True},
        "b:fixture-data-pool": {"n": "Data", "hu": True, "d": 20.0, "du": 1.0},
        "b:fixture-root-pool": {"n": "Root", "hu": True, "hi": True, "d": 9.0},
    }
    view = SystemHistoryView(
        summary=SystemSummary.model_validate(history_data["summary"]),
        range=HistoryRange.ONE_HOUR,
        points=[
            SystemHistoryPoint(created=start, stats={"cpu": 1.0, "z": older}),
            SystemHistoryPoint(
                created=start + timedelta(minutes=1), stats={"cpu": 1.0, "z": newer}
            ),
        ],
    )
    document = _builder(rendering_data).build_history(view)

    titles = [card.title for card in document.cards]
    assert titles[-5:] == [
        "Data I/O",
        "raidz 使用",
        "raidz I/O",
        "tank 使用",
        "tank I/O",
    ]
    assert not any(title.startswith(("gone", "Root")) for title in titles)
    cards = {card.title: card for card in document.cards}

    tank_usage = cards["tank 使用"]
    assert tank_usage.subtitle == "存储池 tank 的使用量"
    assert tank_usage.unit == ChartUnit.BYTES
    assert tank_usage.axis_max == 100.0 * 1024**3
    assert [p.value for p in tank_usage.series[0].points] == [
        40.0 * 1024**3,
        41.0 * 1024**3,
    ]
    tank_io = cards["tank I/O"]
    assert tank_io.subtitle == "存储池 tank 的吞吐量"
    assert [[p.value for p in s.points] for s in tank_io.series] == [
        [1000.0, 0.0],
        [2000.0, 0.0],
    ]

    raidz_usage = cards["raidz 使用"]
    assert raidz_usage.subtitle == "存储池 raidz 的原始使用量"
    assert [p.value for p in raidz_usage.series[0].points] == [24.0 * 1024**3]


def test_history_storage_pool_table_follows_the_hub(
    history_data, rendering_data
) -> None:
    view = SystemHistoryView.model_validate(history_data)
    view.storage_pools = [
        StoragePoolState(
            name="tank",
            health="ONLINE",
            size=1000 * 1024**3,
            alloc=400 * 1024**3,
            free=600 * 1024**3,
            scrub={"state": "SCANNING", "progress": "45.10%"},
            vdevs=[
                {"name": "raidz1-0", "state": "ONLINE"},
                {"name": "sdb", "state": "ONLINE", "checksumErrs": 2},
                {"name": "sdc", "state": "ONLINE", "checksumErrs": 1, "readErrs": 4},
            ],
            details_updated="2026-08-15 12:30:00.000Z",
        ),
        StoragePoolState(
            name="b:fixture-data-pool",
            display_name="data",
            raw=True,
            size=60 * 1024**3,
            alloc=30 * 1024**3,
            free=30 * 1024**3,
            updated="2026-08-15 12:59:00.000Z",
        ),
        StoragePoolState(
            name="backup",
            health="FAULTED",
            scrub={"state": "FINISHED", "errors": 3},
        ),
    ]

    rows = _builder(rendering_data).build_history(view).storage_pools

    # Sorted by display name, like the Hub table.
    assert [row.name for row in rows] == ["backup", "data", "tank"]
    backup, data, tank = rows
    assert (backup.type_label, backup.health_tone) == ("ZFS", "danger")
    assert backup.scrub_text == "FINISHED，3 个错误"
    assert (backup.capacity_text, backup.updated_text) == ("-", "-")
    # Raw pools report physical space: no free space, unknown health.
    assert (data.type_label, data.health_text, data.health_tone) == (
        "Btrfs",
        "未知",
        "outline",
    )
    assert (data.capacity_text, data.used_text, data.free_text) == (
        "60.0 GB (原始)",
        "30.0 GB (原始)",
        "-",
    )
    assert (data.scrub_text, data.errors_text, data.has_errors) == ("无", "无", False)
    assert data.updated_text == "2026-08-15 12:59"
    # An ONLINE pool with device errors is a warning.
    assert (tank.health_text, tank.health_tone) == ("ONLINE", "warning")
    assert (tank.errors_text, tank.has_errors) == ("读 4 · 校验 3", True)
    assert tank.scrub_text == "SCANNING (45.10%)"
    assert (tank.capacity_text, tank.used_text, tank.free_text) == (
        "1.0 TB",
        "400.0 GB",
        "600.0 GB",
    )
    assert tank.updated_text == "2026-08-15 12:30"


def _detail_view(history_data) -> SystemHistoryView:
    start = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    breakdowns = (
        [20.0, 10.0, 5.0, 0.0, 60.0, 0.0, 3.0, 2.0],
        [20.0, 10.0, 5.0, 0.0, 60.0, 0.0, 3.0, 2.0],
        # Agents before v0.21.1 send five values.
        [25.0, 10.0, 5.0, 0.0, 60.0],
    )
    points = [
        SystemHistoryPoint(
            created=start + timedelta(minutes=minute),
            stats={
                "cpu": 40.0,
                "cpub": cpub,
                "cpus": [index * 5.0 for index in range(10)],
                "dios": [0, 0, 10.0 if minute == 1 else 0, 0, 0, 150],
                "ni": {
                    f"eth{index}": [(7 - index) * 10, index * 10, 0, index * 100]
                    for index in range(8)
                },
            },
        )
        for minute, cpub in enumerate(breakdowns)
    ]
    return SystemHistoryView(
        summary=SystemSummary.model_validate(history_data["summary"]),
        range=HistoryRange.ONE_HOUR,
        points=points,
    )


def test_history_detail_charts_follow_the_hub_sheets(
    history_data, rendering_data
) -> None:
    view = _detail_view(history_data)
    assert not any(
        card.title == "CPU 时间细分"
        for card in _builder(rendering_data).build_history(view).cards
    )
    builder = PresentationBuilder(
        plugin_name=rendering_data["plugin_name"],
        show_connection_address=True,
        display_timezone=ZoneInfo(rendering_data["display_timezone"]),
        history_detail_charts=True,
    )
    cards = builder.build_history(view).cards

    titles = [card.title for card in cards]
    # A separate grid after the main charts; charts without data are left out.
    assert titles[titles.index("CPU 时间细分") :] == [
        "CPU 时间细分",
        "CPU 核心",
        "I/O 利用率",
        "队列深度",
        "按网卡下行",
        "按网卡上行",
    ]
    by_title = {card.title: card for card in cards}
    assert by_title["CPU 时间细分"].starts_section

    # Stacked like the Hub and listed top to bottom; zero bands are dropped
    # and five-value samples read the newer states as zero.
    breakdown = by_title["CPU 时间细分"]
    assert breakdown.stacked
    assert [s.name for s in breakdown.series] == [
        "系统",
        "用户",
        "IOWait",
        "空闲",
        "SoftIRQ",
        "Nice",
    ]
    assert [[p.value for p in s.points] for s in breakdown.series] == [
        [100.0, 100.0, 100.0],
        [90.0, 90.0, 90.0],
        [70.0, 70.0, 65.0],
        [65.0, 65.0, 60.0],
        [5.0, 5.0, 0.0],
        [2.0, 2.0, 0.0],
    ]

    # The eight busiest of ten cores, in core order, as lines.
    cores = by_title["CPU 核心"]
    assert not cores.filled
    assert [s.name for s in cores.series] == [f"CPU {i}" for i in range(2, 10)]
    assert cores.extra_series_count == 2
    assert cores.series[0].color == dynamic_series_color(
        2, 10, saturation=0.65, lightness=0.52, base_hue=226.0
    )
    assert [p.value for p in by_title["队列深度"].series[0].points] == [1.5] * 3

    # Interfaces keep the Hub order (total received) and their colors in both
    # directions; each chart keeps its six busiest.
    received, sent = by_title["按网卡下行"], by_title["按网卡上行"]
    assert [s.name for s in received.series] == [f"eth{i}" for i in range(7, 1, -1)]
    assert [s.name for s in sent.series] == [f"eth{i}" for i in range(5, -1, -1)]
    assert received.extra_series_count == sent.extra_series_count == 2
    received_colors = {s.name: s.color for s in received.series}
    for series in sent.series:
        if series.name in received_colors:
            assert series.color == received_colors[series.name]


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
