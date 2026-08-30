from __future__ import annotations

import shutil
import sys
from pathlib import Path

import astrbot.core.star.star_manager as star_manager_module
from astrbot.core.star.star_manager import PluginManager

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = PLUGIN_ROOT.name
MODULE_PATH = f"data.plugins.{PLUGIN_NAME}.main"


class _FakeContext:
    def get_all_stars(self):
        return star_manager_module.star_registry

    def get_registered_star(self, name):
        return next(
            (item for item in star_manager_module.star_registry if item.name == name),
            None,
        )

    def get_config(self):
        return {}


async def _global_get(_key, default=None):
    return default


async def _sync_configs():
    return None


async def run() -> None:
    isolated_root = TESTS_ROOT / "loader-work"
    shutil.rmtree(isolated_root, ignore_errors=True)
    isolated_root.mkdir()
    try:
        plugin_store = isolated_root / "data" / "plugins"
        plugin_store.mkdir(parents=True)
        shutil.copytree(
            PLUGIN_ROOT,
            plugin_store / PLUGIN_NAME,
            ignore=shutil.ignore_patterns(
                ".git",
                ".pytest_cache",
                ".pytest-review",
                ".beszel-loader-*",
                ".ruff_cache",
                "__pycache__",
                "tests",
            ),
        )
        config_dir = isolated_root / "data" / "config"
        config_dir.mkdir()
        reserved_dir = isolated_root / "reserved"
        reserved_dir.mkdir()

        star_manager_module.sp.global_get = _global_get
        star_manager_module.sync_command_configs = _sync_configs
        manager = PluginManager(_FakeContext(), {})
        manager.plugin_store_path = str(plugin_store)
        manager.plugin_config_path = str(config_dir)
        manager.reserved_plugin_path = str(reserved_dir)
        sys.path.insert(0, str(isolated_root))
        try:
            success, error = await manager.load(specified_dir_name=PLUGIN_NAME)
            if not success:
                raise RuntimeError(error)
            metadata = star_manager_module.star_map[MODULE_PATH]
            if metadata.star_cls is None:
                raise RuntimeError("plugin class was not instantiated")
            if metadata.module_path != MODULE_PATH:
                raise RuntimeError(f"unexpected module path: {metadata.module_path}")
            await manager._terminate_plugin(metadata)
        finally:
            if str(isolated_root) in sys.path:
                sys.path.remove(str(isolated_root))
            star_manager_module.star_handlers_registry.clear()
            star_manager_module.star_map.clear()
            star_manager_module.star_registry.clear()
    finally:
        shutil.rmtree(isolated_root, ignore_errors=True)


if __name__ == "__main__":
    import asyncio

    asyncio.run(run())
