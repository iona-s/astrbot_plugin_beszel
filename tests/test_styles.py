from __future__ import annotations

from astrbot_plugin_beszel.core.rendering.styles import (
    COLOR_CRIT,
    COLOR_GOOD,
    COLOR_PAUSED,
    COLOR_WARN,
    SERIES_PALETTE,
    dynamic_series_color,
    metric_color,
    series_color,
    threshold_color,
)


def test_dynamic_series_color_distribution() -> None:
    # Single item defaults to hue 0
    c0 = dynamic_series_color(0, 1, saturation=0.60, lightness=0.55)
    assert c0 == (209, 71, 71)

    # 2 items distribute 180 degrees apart (complementary colors: red vs cyan)
    f0 = dynamic_series_color(0, 2, saturation=0.60, lightness=0.55)
    f1 = dynamic_series_color(1, 2, saturation=0.60, lightness=0.55)
    assert f0 == (209, 71, 71)
    assert f1 == (71, 209, 209)
    # Distinctness: Red and Cyan have completely different R and B/G channels
    assert abs(f0[0] - f1[0]) > 100

    # 4 items: distinct hues at 0, 90, 180, 270 degrees
    colors_4 = [dynamic_series_color(i, 4) for i in range(4)]
    assert len(set(colors_4)) == 4
    for c in colors_4:
        assert len(c) == 3
        assert all(0 <= val <= 255 for val in c)

    # Safe handling of total <= 0
    safe_c = dynamic_series_color(0, 0)
    assert len(safe_c) == 3

    # GPU base hue 226 degrees
    gpu_c = dynamic_series_color(0, 1, saturation=0.65, lightness=0.52, base_hue=226.0)
    assert gpu_c == (53, 90, 212)


def test_load_average_palette_matches_beszel_hub() -> None:
    # 1 min = Purple
    c_1m = series_color(0, metric_key="load")
    assert c_1m == (168, 85, 247)

    # 5 min = Blue
    c_5m = series_color(1, metric_key="load")
    assert c_5m == (37, 99, 235)

    # 15 min = Orange
    c_15m = series_color(2, metric_key="load")
    assert c_15m == (249, 115, 22)

    # metric_color("load") matches 1m Purple
    assert metric_color("load") == (168, 85, 247)


def test_paired_palettes_and_semantic_metrics() -> None:
    # Network Rx (Emerald) / Tx (Rose)
    assert series_color(0, metric_key="net") == (16, 185, 129)
    assert series_color(1, metric_key="net") == (244, 63, 94)

    # Disk I/O Read (Blue) / Write (Amber)
    assert series_color(0, metric_key="disk_io") == (37, 99, 235)
    assert series_color(1, metric_key="disk_io") == (245, 158, 11)

    # Memory palette
    assert series_color(0, metric_key="mem") == (16, 185, 129)
    assert series_color(1, metric_key="mem") == (13, 148, 136)

    # Fallback to series palette
    assert series_color(0, metric_key="unknown") == SERIES_PALETTE[0]


def test_threshold_color_states() -> None:
    assert threshold_color(50.0, "up") == COLOR_GOOD
    assert threshold_color(70.0, "up") == COLOR_WARN
    assert threshold_color(95.0, "up") == COLOR_CRIT
    assert threshold_color(50.0, "down") == COLOR_PAUSED
    assert threshold_color(None, "up") == COLOR_GOOD
