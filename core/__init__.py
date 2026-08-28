"""Beszel plugin implementation package."""

from __future__ import annotations

from typing import Any

__all__ = ["PluginConfig"]


def __getattr__(name: str) -> Any:
    if name == "PluginConfig":
        from .config import PluginConfig

        return PluginConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
