from __future__ import annotations

import importlib
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_loader_shim_and_core_modules_import() -> None:
    module = importlib.import_module("astrbot_plugin_beszel.main")
    assert module.BeszelPlugin.__name__ == "BeszelPlugin"

    for module_name in (
        "astrbot_plugin_beszel.core.access",
        "astrbot_plugin_beszel.core.beszel.client",
        "astrbot_plugin_beszel.core.beszel.models",
        "astrbot_plugin_beszel.core.beszel.service",
        "astrbot_plugin_beszel.core.config",
        "astrbot_plugin_beszel.core.plugin",
        "astrbot_plugin_beszel.core.rendering.engine",
        "astrbot_plugin_beszel.core.rendering.presenter",
        "astrbot_plugin_beszel.core.rendering.renderer",
        "astrbot_plugin_beszel.core.webhook.delivery",
        "astrbot_plugin_beszel.core.webhook.parsers",
        "astrbot_plugin_beszel.core.webhook.server",
    ):
        importlib.import_module(module_name)


def test_runtime_layout_has_no_legacy_root_modules() -> None:
    for filename in (
        "access.py",
        "beszel_client.py",
        "config.py",
        "formatters.py",
        "models.py",
        "plugin.py",
        "renderer.py",
        "service.py",
        "webhook.py",
    ):
        assert not (PLUGIN_ROOT / filename).exists()

    assert (PLUGIN_ROOT / "main.py").is_file()
