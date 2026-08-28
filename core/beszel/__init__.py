"""Beszel API boundary with lazy transport imports."""

from __future__ import annotations

from typing import Any

__all__ = ["BeszelClient", "HistoryRange"]


def __getattr__(name: str) -> Any:
    if name == "BeszelClient":
        from .client import BeszelClient

        return BeszelClient
    if name == "HistoryRange":
        from .models import HistoryRange

        return HistoryRange
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
