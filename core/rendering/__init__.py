"""Public rendering exports with a lazy concrete backend import."""

from __future__ import annotations

from typing import Any

__all__ = ["BeszelRenderer"]


def __getattr__(name: str) -> Any:
    if name == "BeszelRenderer":
        from .renderer import BeszelRenderer

        return BeszelRenderer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
