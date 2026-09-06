from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from urllib.parse import urlsplit

import pytest
from astrbot_plugin_beszel.core.beszel import client as client_module
from astrbot_plugin_beszel.core.beszel.client import BeszelClient
from astrbot_plugin_beszel.core.beszel.models import HistoryRange
from astrbot_plugin_beszel.core.config import BeszelConfig
from astrbot_plugin_beszel.core.errors import (
    BeszelAuthError,
    BeszelTransportError,
)


class _FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.body):
            return b""
        if size < 0:
            size = len(self.body)
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _FakeResponse:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self.body = json.dumps(payload).encode("utf-8")
        self.content_length = len(self.body)
        self.content = _FakeContent(self.body)


class _ResponseContext:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args) -> None:
        return None


class FixtureSession:
    def __init__(self, response_factory) -> None:
        self.response_factory = response_factory
        self.requests: list[dict] = []
        self.closed = False

    def request(self, method, url, *, params=None, json=None, headers=None, ssl=None):
        path = urlsplit(url).path
        record = {
            "method": method,
            "path": path,
            "params": params or {},
            "json": json,
            "headers": headers or {},
            "ssl": ssl,
        }
        self.requests.append(record)
        return _ResponseContext(self.response_factory(record))

    async def close(self) -> None:
        self.closed = True


def _fixture_config(config_data) -> BeszelConfig:
    raw = config_data["complete"]["beszel"]
    return BeszelConfig(
        base_url=raw["base_url"].rstrip("/"),
        email=raw["email"],
        password=raw["password"],
        timeout_seconds=raw["timeout_seconds"],
        verify_tls=raw["verify_tls"],
    )


def _client_with_session(
    config_data,
    session: FixtureSession,
    monkeypatch: pytest.MonkeyPatch,
) -> BeszelClient:
    monkeypatch.setattr(
        client_module.aiohttp, "ClientSession", lambda **_kwargs: session
    )
    return BeszelClient(_fixture_config(config_data))


def _session_for_fixtures(
    overview_data,
    status_data,
    history_data,
    client_data,
    container_history_data=None,
):
    overview_pages = client_data["systems_pages"]
    systems = overview_data

    def response_factory(request: dict) -> _FakeResponse:
        path = request["path"]
        if path.endswith("/auth-with-password"):
            response = client_data["responses"]["auth"]
            return _FakeResponse(response["status"], response["body"])
        if path.endswith("/collections/systems/records"):
            page = int(request["params"]["page"])
            page_data = overview_pages[page - 1]
            items = systems[page_data["start"] : page_data["end"]]
            response = client_data["responses"]["systems"]
            body = deepcopy(response["body"])
            body["items"] = items
            return _FakeResponse(response["status"], body)
        if "/system_details/records/" in path:
            response = client_data["responses"]["details"]
            return _FakeResponse(response["status"], status_data["details"])
        if path.endswith("/collections/system_stats/records"):
            fields = request["params"]["fields"]
            if fields == "created,stats":
                response = client_data["responses"]["history"]
                body = deepcopy(response["body"])
                body["items"] = history_data["points"]
                return _FakeResponse(response["status"], body)
            response = client_data["responses"]["metrics"]
            body = deepcopy(response["body"])
            body["items"] = [status_data["metrics"]]
            return _FakeResponse(response["status"], body)
        if path.endswith("/collections/container_stats/records"):
            fields = request["params"].get("fields")
            if fields == "created,stats":
                response = client_data["responses"]["history"]
                body = deepcopy(response["body"])
                body["items"] = (
                    container_history_data["records"] if container_history_data else []
                )
                return _FakeResponse(response["status"], body)
            response = client_data["responses"]["containers"]
            body = deepcopy(response["body"])
            body["items"][0]["stats"] = status_data["containers"]
            return _FakeResponse(response["status"], body)
        error = client_data["default_error"]
        return _FakeResponse(error["status"], error["body"])

    return FixtureSession(response_factory)


@pytest.mark.asyncio
async def test_client_authenticates_paginates_and_models_fixture_responses(
    overview_data,
    status_data,
    history_data,
    client_data,
    container_history_data,
    config_data,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session_for_fixtures(
        overview_data,
        status_data,
        history_data,
        client_data,
        container_history_data,
    )
    client = _client_with_session(config_data, session, monkeypatch)

    systems = await client.list_systems()
    system_id = client_data["system_id"]
    details = await client.get_system_details(system_id)
    metrics = await client.get_latest_metrics(system_id)
    containers = await client.get_latest_containers(system_id)
    history = await client.get_history(system_id, HistoryRange.ONE_HOUR)
    container_history = await client.get_container_history(
        system_id, HistoryRange.ONE_HOUR
    )
    await client.close()

    assert len(systems) == len(overview_data)
    assert (
        details is not None and details.hostname == status_data["details"]["hostname"]
    )
    assert (
        metrics is not None
        and metrics.stats["cpu"] == status_data["metrics"]["stats"]["cpu"]
    )
    assert len(containers) == len(status_data["containers"])
    assert len(history) == len(history_data["points"])
    # 4 records in fixture, but records 2 and 3 share the same timestamp -> deduplicated to 3
    assert len(container_history) == 3
    assert session.closed is True

    login = next(
        item for item in session.requests if item["path"].endswith("auth-with-password")
    )
    assert login["json"] == client_data["expected_requests"]["login_body"]
    systems_requests = [
        item
        for item in session.requests
        if item["path"].endswith("/collections/systems/records")
    ]
    assert len(systems_requests) == len(client_data["systems_pages"])
    assert (
        systems_requests[0]["params"]["fields"]
        == client_data["expected_requests"]["systems_fields"]
    )
    assert (
        systems_requests[0]["headers"]["Authorization"]
        == client_data["responses"]["auth"]["body"]["token"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history_range",
    [
        HistoryRange.ONE_HOUR,
        HistoryRange.TWELVE_HOURS,
        HistoryRange.ONE_DAY,
        HistoryRange.ONE_WEEK,
        HistoryRange.THIRTY_DAYS,
    ],
)
async def test_get_container_history_queries_all_ranges(
    history_range: HistoryRange,
    client_data,
    config_data,
    container_history_data,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []

    def response_factory(request: dict) -> _FakeResponse:
        path = request["path"]
        if path.endswith("/auth-with-password"):
            response = client_data["responses"]["auth"]
            return _FakeResponse(response["status"], response["body"])
        if path.endswith("/collections/container_stats/records"):
            requests.append(request)
            response = client_data["responses"]["history"]
            body = deepcopy(response["body"])
            body["items"] = container_history_data["records"]
            return _FakeResponse(response["status"], body)
        error = client_data["default_error"]
        return _FakeResponse(error["status"], error["body"])

    session = FixtureSession(response_factory)
    client = _client_with_session(config_data, session, monkeypatch)
    results = await client.get_container_history("sys-123", history_range)
    await client.close()

    assert len(results) == 3
    assert len(requests) == 1
    req_filter = requests[0]["params"]["filter"]
    assert f"type = '{history_range.stats_type}'" in req_filter
    assert "system = 'sys-123'" in req_filter
    assert requests[0]["params"]["fields"] == "created,stats"
    assert requests[0]["params"]["sort"] == "created"


@pytest.mark.asyncio
async def test_get_container_history_empty_and_invalid_records(
    client_data,
    config_data,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response_factory(request: dict) -> _FakeResponse:
        path = request["path"]
        if path.endswith("/auth-with-password"):
            response = client_data["responses"]["auth"]
            return _FakeResponse(response["status"], response["body"])
        if path.endswith("/collections/container_stats/records"):
            response = client_data["responses"]["history"]
            body = deepcopy(response["body"])
            # Mix valid, empty created, invalid created, and bad json
            body["items"] = [
                {
                    "created": "2026-08-15 12:00:00.000Z",
                    "stats": [{"n": "app", "c": 1.0, "m": 10.0}],
                },
                {"created": None, "stats": []},
                {"created": "invalid-timestamp", "stats": []},
            ]
            return _FakeResponse(response["status"], body)
        error = client_data["default_error"]
        return _FakeResponse(error["status"], error["body"])

    session = FixtureSession(response_factory)
    client = _client_with_session(config_data, session, monkeypatch)
    results = await client.get_container_history("sys-123", HistoryRange.ONE_HOUR)
    await client.close()

    # Only the 1 valid record should be kept
    assert len(results) == 1
    assert results[0].stats[0].name == "app"


@pytest.mark.asyncio
async def test_client_retries_once_after_unauthorized(
    client_data, config_data, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = defaultdict(int)

    def response_factory(request: dict) -> _FakeResponse:
        path = request["path"]
        if path.endswith("/auth-with-password"):
            response = client_data["responses"]["auth"]
            return _FakeResponse(response["status"], response["body"])
        if path.endswith(client_data["retry_probe"]["path"]):
            calls[path] += 1
            case = client_data["retry_probe"]["first" if calls[path] == 1 else "second"]
            return _FakeResponse(case["status"], case["body"])
        error = client_data["default_error"]
        return _FakeResponse(error["status"], error["body"])

    session = FixtureSession(response_factory)
    client = _client_with_session(config_data, session, monkeypatch)
    systems = await client.list_systems()
    await client.close()

    assert systems == []
    assert calls[client_data["retry_probe"]["path"]] == 2
    assert (
        sum(item["path"].endswith("auth-with-password") for item in session.requests)
        == 2
    )
    assert session.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["forbidden", "server_error"])
async def test_client_maps_http_errors(
    case: str,
    client_data,
    config_data,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = client_data["errors"][case]
    expected = {
        "forbidden": BeszelAuthError,
        "server_error": BeszelTransportError,
    }[case]

    def response_factory(request: dict) -> _FakeResponse:
        if request["path"].endswith("/auth-with-password"):
            auth = client_data["responses"]["auth"]
            return _FakeResponse(auth["status"], auth["body"])
        return _FakeResponse(response["status"], response["body"])

    session = FixtureSession(response_factory)
    client = _client_with_session(config_data, session, monkeypatch)

    with pytest.raises(expected) as exc_info:
        await client.list_systems()
    if isinstance(exc_info.value, BeszelTransportError):
        assert exc_info.value.status_code == response["status"]
    await client.close()
