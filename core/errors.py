"""Domain errors exposed by the plugin boundary layers."""


class BeszelPluginError(Exception):
    """Base class for user-actionable plugin errors."""


class ConfigurationError(BeszelPluginError):
    """Plugin configuration is invalid or incomplete."""


class BeszelTransportError(BeszelPluginError):
    """The Hub could not be reached or returned an invalid response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BeszelAuthError(BeszelPluginError):
    """The configured Beszel credentials were rejected."""


class BeszelProtocolError(BeszelPluginError):
    """A response did not satisfy the supported Beszel contract."""


class SystemNotFoundError(BeszelPluginError):
    """No system matched a selector."""


class AmbiguousSystemError(BeszelPluginError):
    """Multiple systems matched a selector."""


class InvalidHistoryRangeError(BeszelPluginError):
    """A history range is outside the supported fixed set."""


class RenderingError(BeszelPluginError):
    """The configured image-rendering backend could not produce a PNG."""
