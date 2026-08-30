from __future__ import annotations

from zoneinfo import ZoneInfo

from astrbot_plugin_beszel.core.beszel.models import (
    SystemDetailView,
    SystemHistoryView,
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
    assert not hasattr(document, "total_count")


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
    assert not hasattr(document, "has_gaps")


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
    assert not hasattr(document, "sample_count")
    assert not hasattr(document, "has_gaps")
