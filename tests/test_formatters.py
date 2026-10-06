from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from astrbot_plugin_beszel.core.beszel.models import SystemSummary
from astrbot_plugin_beszel.core.formatters import (
    format_datetime,
    format_system_list,
    resolve_timezone,
    status_state,
)
from astrbot_plugin_beszel.core.rendering.formatters import (
    format_bandwidth,
    format_bytes,
    format_gb,
    format_mb,
)


def test_resolve_timezone_uses_named_zone_or_host_local_time(rendering_data) -> None:
    name = rendering_data["display_timezone"]

    assert resolve_timezone(name) == ZoneInfo(name)
    assert resolve_timezone("") is None


def test_host_local_time_is_resolved_for_each_instant(overview_data) -> None:
    # Winter and summer instants straddle any daylight saving change.
    for value in (
        datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    ):
        assert format_datetime(value, None) == value.astimezone().strftime(
            "%Y-%m-%d %H:%M"
        )
    naive = datetime(2026, 7, 15, 12, 0)
    assert format_datetime(naive, None) == naive.replace(
        tzinfo=UTC
    ).astimezone().strftime("%Y-%m-%d %H:%M")

    systems = [SystemSummary.model_validate(item) for item in overview_data]
    offline = next(
        system
        for system in systems
        if status_state(system.status) == "down" and system.updated is not None
    )
    assert format_datetime(offline.updated, None) in format_system_list(
        systems, timezone=None
    )


def test_byte_values_use_hub_style_units() -> None:
    assert format_bytes(512) == "512.0 B"
    assert format_bytes(1000) == "1.0 KB"
    assert format_bytes(1.5 * 1024**3) == "1.5 GB"
    assert format_gb(64) == "64.0 GB"
    assert format_gb(2048) == "2.0 TB"
    assert format_mb(768) == "768.0 MB"
    assert format_bandwidth(15.1 * 1024**2) == "15.1 MB/s"
    assert format_bytes(None) == "N/A"
