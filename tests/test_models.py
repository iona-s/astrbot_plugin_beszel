from __future__ import annotations

from datetime import UTC, datetime

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryMetrics,
    ContainerHistoryPoint,
    ContainerStats,
    HistoryRange,
    PocketBaseListResult,
    SystemDetailView,
    SystemHistoryMetrics,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemMetrics,
    SystemSummary,
)
from pydantic import ValidationError


def test_public_fixture_views_validate(
    overview_data, status_data, history_data
) -> None:
    overview = [SystemSummary.model_validate(item) for item in overview_data]
    status = SystemDetailView.model_validate(status_data)
    history = SystemHistoryView.model_validate(history_data)

    assert len(overview) == len(overview_data)
    assert status.summary.name == status_data["summary"]["name"]
    assert status.metrics is not None and status.metrics.stats["cpu"] > 0
    assert history.range is HistoryRange.ONE_HOUR
    assert len(history.points) == len(history_data["points"])


def test_pocketbase_alias_and_extra_fields(client_data) -> None:
    case = client_data["alias_case"]
    result = PocketBaseListResult[dict].model_validate(case)
    assert result.total_pages == case["totalPages"]
    assert result.items == case["items"]


def test_container_stats_uses_short_protocol_aliases(models_data) -> None:
    container = ContainerStats.model_validate(models_data["container_extra_fields"])
    assert container.name
    assert container.cpu is not None
    assert container.memory is not None


def test_metrics_require_stats(models_data) -> None:
    with pytest.raises(ValidationError):
        SystemMetrics.model_validate(models_data["metrics_without_stats"])


def test_omitted_bandwidth_is_normalized_to_zero_without_mutating_input(
    models_data,
) -> None:
    raw_stats = dict(models_data["sample_without_bandwidth"]["stats"])
    original_keys = tuple(raw_stats.keys())

    metrics = SystemMetrics(stats=raw_stats)
    assert metrics.stats["b"] == [0, 0]
    assert "b" not in raw_stats
    assert tuple(raw_stats.keys()) == original_keys

    point = SystemHistoryPoint(
        created=datetime(2026, 8, 15, 12, 0, tzinfo=UTC), stats=raw_stats
    )
    assert point.stats["b"] == [0, 0]
    assert "b" not in raw_stats

    history_metric = SystemHistoryMetrics(
        created=datetime(2026, 8, 15, 12, 0, tzinfo=UTC), stats=raw_stats
    )
    assert history_metric.stats["b"] == [0, 0]


def test_explicit_and_malformed_bandwidth_values_are_preserved(models_data) -> None:
    zero = SystemMetrics.model_validate(models_data["sample_explicit_zero_bandwidth"])
    assert zero.stats["b"] == [0, 0]

    nonzero = SystemMetrics.model_validate(models_data["sample_nonzero_bandwidth"])
    assert nonzero.stats["b"] == [1024, 2048]

    malformed = SystemMetrics.model_validate(models_data["sample_malformed_bandwidth"])
    assert malformed.stats["b"] == "invalid"


def test_container_history_metrics_filters_malformed_items(
    container_history_data,
) -> None:
    # Third record has 10 valid containers + 1 invalid dict item without 'n'
    record = container_history_data["records"][2]
    metrics = ContainerHistoryMetrics.model_validate(record)
    assert metrics.created is not None
    assert len(metrics.stats) == 10
    names = [c.name for c in metrics.stats]
    assert "web" in names
    assert "backup" in names


def test_container_history_point_and_system_history_view_backward_compatibility(
    history_data, container_history_data
) -> None:
    # Backward compatibility: view without container_points defaults to empty list
    view_default = SystemHistoryView.model_validate(history_data)
    assert view_default.container_points == []

    # View with populated container points
    points = [
        ContainerHistoryPoint(
            created=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            stats=[ContainerStats(n="web", c=12.5, m=256.0)],
        )
    ]
    view_with_containers = SystemHistoryView(
        summary=view_default.summary,
        range=view_default.range,
        points=view_default.points,
        container_points=points,
    )
    assert len(view_with_containers.container_points) == 1
    assert view_with_containers.container_points[0].stats[0].name == "web"
