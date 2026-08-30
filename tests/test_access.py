from __future__ import annotations

import pytest
from astrbot_plugin_beszel.core.access import AccessDeniedError, AccessPolicy
from astrbot_plugin_beszel.core.config import AccessConfig


class _Event:
    def __init__(self, origin: str, admin: bool) -> None:
        self.unified_msg_origin = origin
        self._admin = admin

    def is_admin(self) -> bool:
        return self._admin


def test_access_policy_uses_fixture_allowlist(config_data, webhook_data) -> None:
    access_raw = config_data["complete"]["access"]
    policy = AccessPolicy(
        AccessConfig(
            mode=access_raw["mode"],
            allowed_umos=tuple(item.strip() for item in access_raw["allowed_umos"][:1]),
        )
    )
    allowed_origin = access_raw["allowed_umos"][0].strip()

    policy.require_query_access(_Event(allowed_origin, admin=False))

    with pytest.raises(AccessDeniedError):
        policy.require_query_access(
            _Event(webhook_data["server"]["denied_origin"], admin=False)
        )


@pytest.mark.parametrize("mode", ["admin_only", "all"])
def test_access_policy_allows_admin_or_all_modes(webhook_data, mode: str) -> None:
    policy = AccessPolicy(AccessConfig(mode=mode))
    origin = webhook_data["server"]["fallback_origin"]
    policy.require_query_access(_Event(origin, admin=True))
    if mode == "all":
        policy.require_query_access(_Event(origin, admin=False))
    else:
        with pytest.raises(AccessDeniedError):
            policy.require_query_access(_Event(origin, admin=False))
