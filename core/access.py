"""Shared authorization policy for commands and LLM tools."""

from __future__ import annotations

from typing import Protocol

from .config import AccessConfig
from .errors import BeszelPluginError


class EventLike(Protocol):
    unified_msg_origin: str

    def is_admin(self) -> bool: ...


class AccessDeniedError(BeszelPluginError):
    """The caller is not allowed to query infrastructure data."""


class AccessPolicy:
    """Enforce the configured policy before any client operation."""

    def __init__(self, config: AccessConfig) -> None:
        self.config = config

    def require_query_access(self, event: EventLike) -> None:
        if event.is_admin():
            return
        if self.config.mode == "all":
            return
        if (
            self.config.mode == "umo_allowlist"
            and event.unified_msg_origin in self.config.allowed_umos
        ):
            return
        raise AccessDeniedError("没有权限查询 Beszel 监控信息")
