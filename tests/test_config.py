from __future__ import annotations

import pytest
from astrbot_plugin_beszel.core.config import PluginConfig
from astrbot_plugin_beszel.core.errors import ConfigurationError


def test_minimal_config_uses_documented_defaults(config_data) -> None:
    config = PluginConfig.from_mapping(config_data["minimal"])
    expected = config_data["expected"]["minimal"]

    assert config.beszel.base_url == expected["base_url"]
    assert config.beszel.timeout_seconds == expected["timeout_seconds"]
    assert config.beszel.history_default_range == expected["history_default_range"]
    assert config.beszel.cache_ttl_seconds == expected["cache_ttl_seconds"]
    assert config.access.mode == expected["access_mode"]
    assert config.render.page_size == expected["page_size"]
    assert config.webhook.enabled is expected["webhook_enabled"]


def test_complete_config_normalizes_values(config_data) -> None:
    config = PluginConfig.from_mapping(
        config_data["complete"], astrbot_timezone=config_data["astrbot_timezone"]
    )
    expected = config_data["expected"]["complete"]

    assert config.beszel.base_url == expected["base_url"]
    assert config.beszel.history_default_range == expected["history_default_range"]
    assert config.beszel.cache_ttl_seconds == expected["cache_ttl_seconds"]
    assert config.access.allowed_umos == tuple(expected["allowed_umos"])
    assert config.display.timezone == expected["timezone"]
    assert config.render.font_path == expected["font_path"]
    assert config.webhook.path == expected["webhook_path"]
    assert config.webhook.enabled is expected["webhook_enabled"]
    assert config.webhook.token == expected["webhook_token"]
    assert config.webhook.target_umos == tuple(expected["webhook_target_umos"])


def test_astrbot_timezone_is_used_when_display_timezone_is_empty(config_data) -> None:
    config = PluginConfig.from_mapping(
        config_data["inherited_timezone"],
        astrbot_timezone=config_data["astrbot_timezone"],
    )
    assert config.display.timezone == config_data["expected"]["inherited_timezone"]


@pytest.mark.parametrize(
    "case",
    [
        "base_url",
        "timeout",
        "history_range",
        "access_mode",
        "timezone",
        "page_size",
        "cache_ttl_negative",
        "cache_ttl_overflow",
        "cache_ttl_type",
    ],
)
def test_invalid_config_cases_raise_configuration_error(config_data, case: str) -> None:
    raw = next(item["raw"] for item in config_data["invalid"] if item["name"] == case)
    with pytest.raises(ConfigurationError):
        PluginConfig.from_mapping(raw)


def test_enabled_webhook_generates_token_without_exposing_fixture_credentials(
    config_data,
) -> None:
    config = PluginConfig.from_mapping(config_data["generated_webhook_token"])

    assert config.webhook.enabled is True
    assert config.webhook.token_generated is True
    assert len(config.webhook.token) >= 32


def test_cache_ttl_fixtures_support_disabled_and_fallback(config_data) -> None:
    disabled = PluginConfig.from_mapping(config_data["cache_ttl_disabled"])
    assert disabled.beszel.cache_ttl_seconds == 0

    fallback = PluginConfig.from_mapping(config_data["cache_ttl_default_fallback"])
    assert (
        fallback.beszel.cache_ttl_seconds
        == config_data["expected"]["minimal"]["cache_ttl_seconds"]
    )


def test_non_ascii_webhook_token_disables_webhook_without_failing_queries(
    config_data,
) -> None:
    config = PluginConfig.from_mapping(config_data["non_ascii_webhook_token"])

    assert config.webhook.enabled is False
    assert (
        config.webhook.token
        == config_data["non_ascii_webhook_token"]["webhook"]["token"]
    )
    assert config.webhook.token_generated is False
    assert config.beszel.base_url == config_data["expected"]["minimal"]["base_url"]
