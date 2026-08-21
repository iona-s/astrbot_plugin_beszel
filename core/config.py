"""Configuration normalization and validation."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astrbot.api import logger

from .errors import ConfigurationError

DEFAULT_BASE_URL = "http://127.0.0.1:8090"
SUPPORTED_HISTORY_RANGES = ("1h", "12h", "24h", "1w", "30d")


def _mapping(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _opt_str(value: object, default: str = "") -> str:
    """Return a stripped string, treating explicit None as the field default."""
    if value is None:
        return default
    return str(value).strip()


def _opt_text(value: object, default: str = "") -> str:
    """Return the value as text without stripping, treating None as the default."""
    if value is None:
        return default
    return str(value)


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        text for item in value if item is not None and (text := str(item).strip())
    )


@dataclass(frozen=True, slots=True)
class BeszelConfig:
    base_url: str = DEFAULT_BASE_URL
    email: str = ""
    password: str = field(default="", repr=False)
    timeout_seconds: int = 10
    verify_tls: bool = True
    history_default_range: str = "1h"

    @property
    def query_ready(self) -> bool:
        """Return whether credentials are present without exposing their values."""
        return bool(self.email and self.password)


@dataclass(frozen=True, slots=True)
class AccessConfig:
    mode: str = "admin_only"
    allowed_umos: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DisplayConfig:
    timezone: str = ""


@dataclass(frozen=True, slots=True)
class RenderConfig:
    page_size: int = 10
    show_connection_address: bool = False


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8899
    path: str = "/"
    token: str = field(default="", repr=False)
    target_umos: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginConfig:
    beszel: BeszelConfig = field(default_factory=BeszelConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)

    @classmethod
    def from_mapping(
        cls,
        raw: object,
        *,
        generate_webhook_token: bool = True,
        astrbot_timezone: str = "",
    ) -> PluginConfig:
        data = _mapping(raw)
        beszel_raw = _mapping(data.get("beszel"))
        base_url = _opt_str(beszel_raw.get("base_url"), DEFAULT_BASE_URL).rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError(
                "beszel.base_url must be an absolute http/https URL"
            )

        timeout = beszel_raw.get("timeout_seconds", 10)
        if timeout is None:
            timeout = 10
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 2 <= timeout <= 60
        ):
            raise ConfigurationError("beszel.timeout_seconds must be between 2 and 60")
        default_range = _opt_str(beszel_raw.get("history_default_range"), "1h") or "1h"
        if default_range not in SUPPORTED_HISTORY_RANGES:
            raise ConfigurationError("beszel.history_default_range is not supported")

        access_raw = _mapping(data.get("access"))
        mode = _opt_str(access_raw.get("mode"), "admin_only") or "admin_only"
        if mode not in {"admin_only", "umo_allowlist", "all"}:
            raise ConfigurationError("access.mode is not supported")

        display_raw = _mapping(data.get("display"))
        configured_timezone = _opt_str(display_raw.get("timezone"))
        timezone = configured_timezone or (astrbot_timezone or "").strip()
        if timezone:
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                source = (
                    "display.timezone" if configured_timezone else "AstrBot timezone"
                )
                raise ConfigurationError(
                    f"{source} must be a valid IANA timezone"
                ) from exc

        render_raw = _mapping(data.get("render"))
        page_size = render_raw.get("page_size", 10)
        if page_size is None:
            page_size = 10
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 4 <= page_size <= 20
        ):
            raise ConfigurationError("render.page_size must be between 4 and 20")

        webhook_raw = _mapping(data.get("webhook"))
        enabled = bool(webhook_raw.get("enabled", False))
        port = webhook_raw.get("port", 8899)
        if port is None:
            port = 8899
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            if enabled:
                logger.error(
                    "Invalid webhook.port (%s); webhook service will be disabled",
                    port,
                )
                enabled = False
            port = 8899
        path = _opt_str(webhook_raw.get("path"), "/") or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        if path == "/healthz" or any(char in path for char in "{}?"):
            if enabled:
                logger.error(
                    "Invalid webhook.path (%s); webhook service will be disabled",
                    path,
                )
                enabled = False
            path = "/"
        target_umos = _string_items(webhook_raw.get("target_umos", []))
        token = _opt_text(webhook_raw.get("token"))
        if enabled and not token and generate_webhook_token:
            token = secrets.token_urlsafe(32)
        if enabled and not token:
            logger.error(
                "webhook.token is required when enabled; webhook service will be disabled"
            )
            enabled = False
        if enabled and not target_umos:
            logger.error(
                "webhook.target_umos is empty; webhook service will be disabled"
            )
            enabled = False

        return cls(
            beszel=BeszelConfig(
                base_url=base_url,
                email=_opt_str(beszel_raw.get("email")),
                password=_opt_text(beszel_raw.get("password")),
                timeout_seconds=timeout,
                verify_tls=bool(beszel_raw.get("verify_tls", True)),
                history_default_range=default_range,
            ),
            access=AccessConfig(
                mode=mode,
                allowed_umos=_string_items(access_raw.get("allowed_umos", [])),
            ),
            display=DisplayConfig(timezone=timezone),
            render=RenderConfig(
                page_size=page_size,
                show_connection_address=bool(
                    render_raw.get("show_connection_address", False)
                ),
            ),
            webhook=WebhookConfig(
                enabled=enabled,
                host=_opt_str(webhook_raw.get("host"), "127.0.0.1") or "127.0.0.1",
                port=port,
                path=path,
                token=token,
                target_umos=target_umos,
            ),
        )
