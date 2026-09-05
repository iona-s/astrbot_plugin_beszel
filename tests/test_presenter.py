from __future__ import annotations

from zoneinfo import ZoneInfo

from astrbot_plugin_beszel.core.beszel.models import (
    HistoryRange,
    SystemDetailView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemMetrics,
    SystemSummary,
)
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
