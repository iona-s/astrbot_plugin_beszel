from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
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


@pytest.mark.asyncio
async def test_commands_query_with_the_command_cache_ttl(monkeypatch) -> None:
    class FakeRenderer:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def render_status(self, _view) -> bytes:
            return b"status"

        async def render_history(self, _view) -> bytes:
            return b"history"

    class RecordingService:
        def __init__(self) -> None:
            self.ttls: list[tuple[str, float]] = []

        async def get_overview(self, *, ttl=0):
            self.ttls.append(("overview", ttl))
            return []

        async def get_system_detail(self, _selector, *, ttl=0):
            self.ttls.append(("status", ttl))
            return SimpleNamespace(summary=SimpleNamespace(name="node"))

        async def get_system_history(self, _selector, _range, *, ttl=0):
            self.ttls.append(("history", ttl))
            return SimpleNamespace(
                summary=SimpleNamespace(name="node"), range=SimpleNamespace(value="1h")
            )

    class AdminEvent:
        unified_msg_origin = "fixture:admin"

        def is_admin(self) -> bool:
            return True

    monkeypatch.setattr(
        "astrbot_plugin_beszel.core.plugin.BeszelRenderer", FakeRenderer
    )
    context = MagicMock()
    context.get_config.return_value = {}
    plugin = BeszelPlugin(
        context,
        {"beszel": {"command_cache_ttl_seconds": 7, "hardware_cache_ttl_seconds": 120}},
    )
    assert plugin.service.hardware_cache_ttl == 120
    service = RecordingService()
    plugin.service = service

    for kind in ("overview", "status", "history"):
        await plugin._build_query_output(AdminEvent(), kind, selector="node")

    assert service.ttls == [("overview", 7), ("status", 7), ("history", 7)]
