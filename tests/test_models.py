from __future__ import annotations

import pytest
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerStats,
    HistoryRange,
    PocketBaseListResult,
    SystemDetailView,
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
    assert not hasattr(result, "page")


def test_container_stats_uses_short_protocol_aliases(models_data) -> None:
    container = ContainerStats.model_validate(models_data["container_extra_fields"])
    assert container.name
    assert container.cpu is not None
    assert container.memory is not None
    assert not hasattr(container, "display_name")
    assert not hasattr(container, "cpu_percent")


def test_metrics_require_stats(models_data) -> None:
    with pytest.raises(ValidationError):
        SystemMetrics.model_validate(models_data["metrics_without_stats"])


def test_removed_compatibility_models_and_fields_are_absent() -> None:
    from astrbot_plugin_beszel.core.beszel import models

    assert not hasattr(models, "BeszelModel")
    assert not hasattr(models.SystemSummary, "metrics")
    assert not hasattr(models.SystemHistoryView, "has_gaps")
