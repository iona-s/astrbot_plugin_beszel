from __future__ import annotations

from unittest.mock import MagicMock

from astrbot_plugin_beszel.core.plugin import BeszelPlugin


def test_beszel_plugin_passes_render_scale_to_renderer(
    config_data, monkeypatch
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeRenderer:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(
        "astrbot_plugin_beszel.core.plugin.BeszelRenderer",
        FakeRenderer,
    )

    context = MagicMock()
    context.get_config.return_value = {}

    # 1. Default config from fixture -> 100
    minimal_config = config_data["minimal"]
    plugin_default = BeszelPlugin(context, minimal_config)
    expected_default = config_data["expected"]["minimal"]["render_scale"]
    assert plugin_default.config.render.render_scale == expected_default
    assert captured_kwargs["render_scale"] == expected_default

    # 2. Configured render_scale from fixture
    scaled_case = config_data["render_scale_cases"]["scaled_150"]
    captured_kwargs.clear()
    plugin_scaled = BeszelPlugin(context, scaled_case["raw"])
    assert plugin_scaled.config.render.render_scale == scaled_case["expected"]
    assert captured_kwargs["render_scale"] == scaled_case["expected"]
