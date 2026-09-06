"""Small lifecycle-managed asynchronous client for the Beszel REST API."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp
from astrbot.api import logger

from ..config import BeszelConfig
from ..errors import (
    BeszelAuthError,
    BeszelProtocolError,
    BeszelTransportError,
)
from .models import (
    ContainerHistoryMetrics,
    ContainerStats,
    HistoryRange,
    PocketBaseListResult,
    SystemDetails,
    SystemHistoryMetrics,
    SystemMetrics,
    SystemSummary,
)

USER_AGENT = "astrbot-plugin-beszel/1.0.0"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class BeszelClient:
    """Read-only Beszel client with one shared aiohttp session."""

    def __init__(self, config: BeszelConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._token = None

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/{path.lstrip('/')}"

    async def _read_json(self, response: aiohttp.ClientResponse) -> Any:
        """Read a response body with a hard size bound, then parse it as JSON.

        A trustworthy Content-Length above the limit fails fast; otherwise the
        body is accumulated in chunks and reading stops as soon as the limit
        is exceeded, because a single ``content.read(n)`` may return a short
        chunk before EOF.
        """
        content_length = response.content_length
        if content_length is not None and content_length > MAX_RESPONSE_BYTES:
            raise BeszelProtocolError("⚠️ Beszel 返回数据体积异常")
        body = bytearray()
        while True:
            chunk = await response.content.read(64 * 1024)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise BeszelProtocolError("⚠️ Beszel 返回数据体积异常")
        try:
            return json.loads(bytes(body))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.debug(
                "Failed to parse JSON response: %s (body len=%d)", exc, len(body)
            )
            raise BeszelProtocolError(
                "⚠️ Beszel 返回数据格式异常，非有效 JSON 格式"
            ) from exc

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_body: dict[str, str] | None = None,
        authenticated: bool = True,
        replayed: bool = False,
    ) -> Any:
        session = await self._ensure_session()
        headers = (
            {"Authorization": self._token} if authenticated and self._token else {}
        )
        logger.debug(
            "Beszel API request: %s %s params=%s payload=%s authenticated=%s replayed=%s",
            method,
            path,
            params,
            _sanitize_for_log(json_body),
            authenticated,
            replayed,
        )
        try:
            async with session.request(
                method,
                self._url(path),
                params=params,
                json=json_body,
                headers=headers,
                ssl=self.config.verify_tls,
            ) as response:
                if response.status == 401 and authenticated and not replayed:
                    logger.debug(
                        "Beszel API returned 401 for %s %s, clearing token and retrying login...",
                        method,
                        path,
                    )
                    self._token = None
                    await self._login()
                    return await self._request_json(
                        method,
                        path,
                        params=params,
                        json_body=json_body,
                        authenticated=authenticated,
                        replayed=True,
                    )
                if response.status == 401:
                    logger.debug("Beszel API 401 Unauthorized for %s %s", method, path)
                    raise BeszelAuthError(
                        "🔒 Beszel 身份验证失败，请管理员检查邮箱或密码是否正确"
                    )
                if response.status in {400, 403, 404, 429} or response.status >= 500:
                    try:
                        err_payload = await self._read_json(response)
                        logger.debug(
                            "Beszel API error response: %s %s [HTTP %d] body=%s",
                            method,
                            path,
                            response.status,
                            _sanitize_for_log(err_payload),
                        )
                    except Exception:
                        logger.debug(
                            "Beszel API error response: %s %s [HTTP %d] (unreadable body)",
                            method,
                            path,
                            response.status,
                        )
                    if response.status == 403:
                        raise BeszelAuthError(
                            "🚫 Beszel 拒绝访问（HTTP 403 Forbidden），当前账号权限不足"
                        )
                    raise BeszelTransportError(
                        f"❌ Beszel 服务请求失败（HTTP {response.status}）",
                        status_code=response.status,
                    )
                if response.status < 200 or response.status >= 300:
                    raise BeszelTransportError(
                        f"❌ Beszel 服务请求失败（HTTP {response.status}）",
                        status_code=response.status,
                    )
                payload = await self._read_json(response)
                logger.debug(
                    "Beszel API response: %s %s [HTTP %d] body=%s",
                    method,
                    path,
                    response.status,
                    _sanitize_for_log(payload),
                )
                return payload
        except TimeoutError as exc:
            logger.debug("Beszel API request timed out: %s %s", method, path)
            raise BeszelTransportError("⏱️ 请求 Beszel 服务超时，请稍后重试") from exc
        except aiohttp.ClientError as exc:
            logger.debug("Beszel API network error: %s %s: %s", method, path, exc)
            raise BeszelTransportError(
                "❌ 无法连接到 Beszel 服务端，请检查网络或服务地址是否正常"
            ) from exc

    async def _login(self) -> None:
        if not self.config.query_ready:
            raise BeszelAuthError(
                "🔒 未配置 Beszel 登录凭据（邮箱/密码），请先在插件配置中填写"
            )
        payload = {"identity": self.config.email, "password": self.config.password}
        data = await self._request_json(
            "POST",
            "/api/collections/users/auth-with-password",
            json_body=payload,
            authenticated=False,
        )
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise BeszelAuthError("❌ Beszel 登录异常，返回数据中缺少访问令牌")
        self._token = token

    async def _authenticated_json(
        self, path: str, *, params: dict[str, str | int]
    ) -> Any:
        if self._token is None:
            await self._login()
        return await self._request_json("GET", path, params=params)

    async def _list_records(
        self,
        collection: str,
        *,
        params: dict[str, str | int],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        per_page = min(200, limit) if limit is not None else 200
        while True:
            page_params = {**params, "page": page, "perPage": per_page}
            payload = await self._authenticated_json(
                f"/api/collections/{collection}/records", params=page_params
            )
            try:
                result = PocketBaseListResult[dict[str, Any]].model_validate(payload)
            except Exception as exc:
                raise BeszelProtocolError(
                    f"⚠️ Beszel 数据集合（{collection}）解析失败"
                ) from exc
            records.extend(result.items)
            logger.debug(
                "BeszelClient._list_records: collection=%s page=%d total_pages=%d items_on_page=%d accumulated=%d",
                collection,
                page,
                result.total_pages,
                len(result.items),
                len(records),
            )
            if limit is not None and len(records) >= limit:
                return records[:limit]
            if page >= result.total_pages or not result.items:
                break
            page += 1
        return records

    async def list_systems(self) -> list[SystemSummary]:
        records = await self._list_records(
            "systems",
            params={
                "fields": "id,name,status,updated,info,host,port",
                "sort": "name",
            },
        )
        try:
            return [SystemSummary.model_validate(record) for record in records]
        except Exception as exc:
            raise BeszelProtocolError("⚠️ Beszel 探针列表数据解析失败") from exc

    async def get_system_details(self, system_id: str) -> SystemDetails | None:
        try:
            payload = await self._authenticated_json(
                f"/api/collections/system_details/records/{quote(system_id, safe='')}",
                params={"fields": "hostname,os,kernel,arch,cpu,cores,threads,memory"},
            )
        except BeszelTransportError as exc:
            if exc.status_code == 404:
                logger.debug(
                    "BeszelClient.get_system_details: system_details not found (404) for system_id=%s",
                    system_id,
                )
                return None
            raise
        try:
            return SystemDetails.model_validate(payload)
        except Exception as exc:
            raise BeszelProtocolError("⚠️ Beszel 探针详情数据解析失败") from exc

    async def get_latest_metrics(self, system_id: str) -> SystemMetrics | None:
        records = await self._list_records(
            "system_stats",
            params={
                "filter": self._filter_eq("system", system_id) + " && type = '1m'",
                "fields": "stats",
                "sort": "-created",
            },
            limit=1,
        )
        if not records:
            return None
        try:
            return SystemMetrics.model_validate(records[0])
        except Exception as exc:
            raise BeszelProtocolError("⚠️ Beszel 监控指标数据解析失败") from exc

    async def get_latest_containers(self, system_id: str) -> list[ContainerStats]:
        records = await self._list_records(
            "container_stats",
            params={
                "filter": self._filter_eq("system", system_id) + " && type = '1m'",
                "fields": "stats",
                "sort": "-created",
            },
            limit=1,
        )
        if not records:
            return []

        raw_stats = records[0].get("stats")
        if not isinstance(raw_stats, list):
            raise BeszelProtocolError("⚠️ Beszel 容器监控数据解析失败")

        containers: list[ContainerStats] = []
        for index, item in enumerate(raw_stats):
            try:
                containers.append(ContainerStats.model_validate(item))
            except Exception as exc:
                logger.debug(
                    "Skipping invalid container_stats item at index %d: %s",
                    index,
                    type(exc).__name__,
                )
        return containers

    async def get_history(
        self, system_id: str, history_range: HistoryRange
    ) -> list[SystemHistoryMetrics]:
        cutoff = datetime.now(UTC) - history_range.duration
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        records = await self._list_records(
            "system_stats",
            params={
                "filter": (
                    self._filter_eq("system", system_id)
                    + f" && type = '{history_range.stats_type}'"
                    + f" && created >= '{cutoff_str}'"
                ),
                "fields": "created,stats",
                "sort": "created",
            },
        )
        by_timestamp: dict[datetime, SystemHistoryMetrics] = {}
        for record in records:
            try:
                point = SystemHistoryMetrics.model_validate(record)
                if point.created is None:
                    continue
                by_timestamp[point.created] = point
            except (ValueError, BeszelProtocolError) as exc:
                logger.debug("Skipping invalid history record: %s", type(exc).__name__)
                continue
        return [by_timestamp[key] for key in sorted(by_timestamp)]

    async def get_container_history(
        self, system_id: str, history_range: HistoryRange
    ) -> list[ContainerHistoryMetrics]:
        cutoff = datetime.now(UTC) - history_range.duration
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        records = await self._list_records(
            "container_stats",
            params={
                "filter": (
                    self._filter_eq("system", system_id)
                    + f" && type = '{history_range.stats_type}'"
                    + f" && created >= '{cutoff_str}'"
                ),
                "fields": "created,stats",
                "sort": "created",
            },
        )
        by_timestamp: dict[datetime, ContainerHistoryMetrics] = {}
        for record in records:
            try:
                point = ContainerHistoryMetrics.model_validate(record)
                if point.created is None:
                    continue
                by_timestamp[point.created] = point
            except (ValueError, BeszelProtocolError) as exc:
                logger.debug(
                    "Skipping invalid container history record: %s", type(exc).__name__
                )
                continue
        return [by_timestamp[key] for key in sorted(by_timestamp)]

    @staticmethod
    def _filter_eq(field: str, value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"{field} = '{escaped}'"


def _sanitize_for_log(data: Any) -> Any:
    """Recursively redact sensitive credentials and network connection details for logging."""
    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            if str(k).casefold() in {"password", "token", "host", "port", "ip"}:
                sanitized[k] = "<redacted>"
            elif isinstance(v, (dict, list)):
                sanitized[k] = _sanitize_for_log(v)
            else:
                sanitized[k] = v
        return sanitized
    if isinstance(data, list):
        return [_sanitize_for_log(item) for item in data]
    return data
