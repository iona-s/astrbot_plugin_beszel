"""AstrBot registration and lifecycle coordination."""

from dataclasses import dataclass
from typing import Literal

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import BaseMessageComponent, Image, Plain
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import star_handlers_registry

from .access import AccessPolicy
from .beszel.client import BeszelClient
from .beszel.models import HistoryRange
from .beszel.service import QueryService
from .config import SUPPORTED_HISTORY_RANGES, PluginConfig
from .errors import BeszelPluginError
from .formatters import format_system_list, resolve_timezone
from .rendering.renderer import BeszelRenderer
from .webhook.delivery import WebhookDelivery
from .webhook.server import WebhookServer

_IMPLEMENTATION_MODULE = __name__


def _astrbot_timezone(context: object) -> str:
    get_config = getattr(context, "get_config", None)
    if not callable(get_config):
        return ""
    value = get_config().get("timezone", "")
    return str(value).strip() if value is not None else ""


@dataclass(frozen=True, slots=True)
class QueryOutput:
    """User-facing messages plus the minimal result returned to an LLM."""

    messages: list[list[BaseMessageComponent]]
    summary: str


class BeszelPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = PluginConfig.from_mapping(
            config or {}, astrbot_timezone=_astrbot_timezone(context)
        )
        self.display_timezone = resolve_timezone(self.config.display.timezone)
        if config is not None and self.config.webhook.token_generated:
            self._persist_generated_webhook_token(config)
        self.client = BeszelClient(self.config.beszel)
        self.access = AccessPolicy(self.config.access)
        self.service = QueryService(
            self.client,
            default_history_range=HistoryRange.parse(
                self.config.beszel.history_default_range
            ),
            cache_ttl=self.config.beszel.cache_ttl_seconds,
        )
        self.renderer = BeszelRenderer(
            plugin_name="astrbot_plugin_beszel",
            show_connection_address=self.config.render.show_connection_address,
            display_timezone=self.display_timezone,
            font_path=self.config.render.font_path,
        )
        self.webhook_server: WebhookServer | None = None

    def _persist_generated_webhook_token(self, config: AstrBotConfig) -> None:
        """Persist a freshly generated webhook token back to the config.

        Warn instead of letting a save failure break plugin construction; an
        unsaved token changes on every restart. Never log the token value.
        """
        failure_message = (
            "Generated webhook token could not be saved; it changes on every "
            "restart and notifiers will get HTTP 401. Set webhook.token "
            "manually to keep it stable."
        )
        webhook_raw = config.get("webhook", {})
        save_config = getattr(config, "save_config", None)
        if not isinstance(webhook_raw, dict) or not callable(save_config):
            logger.warning(failure_message)
            return
        webhook_raw["token"] = self.config.webhook.token
        try:
            save_config()
        except Exception:
            logger.warning(failure_message, exc_info=True)
            return
        logger.info(
            "Generated a new webhook Bearer token and saved it to the plugin config"
        )

    async def initialize(self) -> None:
        await self.renderer.initialize()
        if self.config.webhook.enabled:
            delivery = WebhookDelivery(
                self.context, self.config.webhook, self.service, self.renderer
            )
            self.webhook_server = WebhookServer(
                self.config.webhook,
                delivery,
                self.service,
            )
            await self.webhook_server.start()

    async def terminate(self) -> None:
        self.service.invalidate_cache()
        if self.webhook_server is not None:
            try:
                await self.webhook_server.stop()
            except Exception:
                logger.error("Webhook server shutdown failed", exc_info=True)
            finally:
                self.webhook_server = None
        try:
            self.renderer.close()
        except Exception:
            logger.error("Rendering engine shutdown failed", exc_info=True)
        try:
            await self.client.close()
        except Exception:
            logger.error("Beszel client shutdown failed", exc_info=True)

    async def _build_query_output(
        self,
        event: AstrMessageEvent,
        kind: Literal["list", "overview", "status", "history"],
        *,
        selector: str | None = None,
        history_range: str | None = None,
    ) -> QueryOutput:
        self.access.require_query_access(event)
        if kind == "list":
            systems = await self.service.list_systems()
            return QueryOutput(
                [[Plain(format_system_list(systems, timezone=self.display_timezone))]],
                f"Sent Beszel system list: {len(systems)} systems",
            )
        if kind == "overview":
            systems = await self.service.get_overview()
            if not systems:
                return QueryOutput(
                    [
                        [
                            Plain(
                                format_system_list(
                                    systems, timezone=self.display_timezone
                                )
                            )
                        ]
                    ],
                    "Sent Beszel overview: 0 systems",
                )
            images = await self.renderer.render_overview(
                systems, self.config.render.page_size
            )
            return QueryOutput(
                [[Image.fromBytes(image)] for image in images],
                f"Sent Beszel overview: {len(systems)} systems, {len(images)} image pages",
            )
        if not selector:
            raise BeszelPluginError(
                "⚠️ 请提供要查询的探针名称或 ID（例如：/beszel status my-server）"
            )
        if kind == "status":
            view = await self.service.get_system_detail(selector)
            image = await self.renderer.render_status(view)
            return QueryOutput(
                [[Image.fromBytes(image)]],
                f"Sent Beszel status image for {view.summary.name}",
            )
        view = await self.service.get_system_history(
            selector,
            history_range,
        )
        image = await self.renderer.render_history(view)
        return QueryOutput(
            [[Image.fromBytes(image)]],
            f"Sent Beszel history image for {view.summary.name} ({view.range.value})",
        )

    async def _safe_query_output(
        self, event: AstrMessageEvent, kind: str, **kwargs
    ) -> QueryOutput:
        try:
            logger.debug(
                "BeszelPlugin query received: kind=%s kwargs=%s origin=%s",
                kind,
                kwargs,
                getattr(event, "unified_msg_origin", None),
            )
            result = await self._build_query_output(event, kind, **kwargs)
            logger.debug(
                "BeszelPlugin query succeeded: kind=%s summary=%s", kind, result.summary
            )
            return result
        except BeszelPluginError as exc:
            logger.debug(
                "BeszelPlugin query %s failed with domain error: %s",
                kind,
                exc,
            )
            return QueryOutput([[Plain(str(exc))]], f"Beszel {kind} failed")
        except Exception as exc:
            logger.error(
                "Beszel %s failed: %s", kind, type(exc).__name__, exc_info=True
            )
            return QueryOutput(
                [
                    [
                        Plain(
                            "❌ Beszel 查询执行失败，请稍后重试或联系管理员检查配置与日志"
                        )
                    ]
                ],
                f"Beszel {kind} failed",
            )

    @filter.command_group("beszel")
    async def beszel(self, event: AstrMessageEvent):
        """查询 Beszel 探针列表、概览、详情或历史"""
        yield event.plain_result(
            "📌 Beszel 监控指令用法：\n"
            "• /beszel list - 查看探针节点列表\n"
            "• /beszel overview - 查看所有节点监控大盘\n"
            "• /beszel status <名称/ID> - 查看指定单机运行详情\n"
            "• /beszel history <名称/ID> [1h|12h|24h|1w|30d] - 查看历史趋势图"
        )

    @beszel.command("list")
    async def beszel_list(self, event: AstrMessageEvent):
        """列出所有可见 Beszel 探针，仅返回纯文本"""
        output = await self._safe_query_output(event, "list")
        for message in output.messages:
            yield event.chain_result(message)

    @beszel.command("overview")
    async def beszel_overview(self, event: AstrMessageEvent):
        """查询所有探针当前概览"""
        output = await self._safe_query_output(event, "overview")
        for message in output.messages:
            yield event.chain_result(message)

    @beszel.command("status")
    async def beszel_status(self, event: AstrMessageEvent, selector: GreedyStr):
        """查询单个探针当前详情"""
        output = await self._safe_query_output(event, "status", selector=str(selector))
        for message in output.messages:
            yield event.chain_result(message)

    @beszel.command("history")
    async def beszel_history(self, event: AstrMessageEvent, query: GreedyStr):
        """查询单个探针历史，支持 1h、12h、24h、1w、30d"""
        selector, range_value = self._history_arguments(str(query))
        output = await self._safe_query_output(
            event,
            "history",
            selector=selector,
            history_range=range_value,
        )
        for message in output.messages:
            yield event.chain_result(message)

    async def _send_output(self, event: AstrMessageEvent, output: QueryOutput) -> str:
        for components in output.messages:
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain(components),
            )
        return output.summary

    @filter.llm_tool("beszel_list_systems")
    async def tool_list_systems(self, event: AstrMessageEvent) -> str:
        """获取 Beszel 监控平台中所有受监控的探针节点/服务器主机的纯文本列表及在线状态。

        适用场景：当用户询问“有哪些服务器/探针/主机”、“列出所有受监控机器”或需要查看探针名称与在线状态时调用。
        不适用场景：不要用于查询具体的硬件占用图表或详细监控指标（请使用 overview、status 或 history）。
        """
        return await self._send_output(
            event, await self._safe_query_output(event, "list")
        )

    @filter.llm_tool("beszel_get_overview")
    async def tool_get_overview(self, event: AstrMessageEvent) -> str:
        """获取 Beszel 监控中所有服务器/探针节点的整体运行状态概览长图（包含各节点 CPU、内存、根分区磁盘、网络吞吐、温度、GPU 等核心指标进度条）。

        适用场景：当用户询问“所有服务器的运行状态”、“看看所有机器的整体负载”、“服务器监控概览”等全局概况时调用。
        不适用场景：当用户明确指定了某一台特定服务器/探针名称（如“查看 NAS 的状态”、“pve情况如何”）时，严禁调用此工具，应调用 beszel_get_system_status。
        """
        return await self._send_output(
            event, await self._safe_query_output(event, "overview")
        )

    @filter.llm_tool("beszel_get_system_status")
    async def tool_get_system_status(self, event: AstrMessageEvent, system: str) -> str:
        """获取指定单一服务器/探针节点的当前实时硬件运行状态详情长图（包含系统规格、负载、内存明细、根分区与外挂盘 EFS、GPU 利用率/显存、网络吞吐等）。

        适用场景：当用户询问某台特定机器当前的实时状态、硬件占用或实时指标时调用。
        不适用场景：当用户询问历史趋势、过去一段时间的时序曲线走势时（如“过去一小时”、“昨天”），不要调用此工具，应调用 beszel_get_system_history。

        Args:
            system(string): 探针节点的主机名、名称或 15 位 ID（例如 'pc'、'nas'、'pve'、'服务器' 等）。
        """
        return await self._send_output(
            event,
            await self._safe_query_output(event, "status", selector=system),
        )

    @filter.llm_tool("beszel_get_system_history")
    async def tool_get_system_history(
        self,
        event: AstrMessageEvent,
        system: str,
        range: str | None = None,
    ) -> str:
        """获取指定单一服务器/探针节点的历史时序监控走势长图（包含 CPU 使用率、精确内存、磁盘使用、磁盘 I/O 读写吞吐、网络带宽 Rx/Tx、系统负载、温度、GPU 功耗与利用率、外挂盘 I/O 等历史多曲线图表）。

        适用场景：当用户明确要求查看某台服务器的历史记录、时序曲线、走势图或指定了历史时间跨度时调用。
        不适用场景：当用户只询问某机器当前的实时状态时不要调用此工具。

        Args:
            system(string): 探针节点的主机名、名称或 15 位 ID（例如 'pc'、'nas'、'pve'、'服务器' 等）。
            range(string): 可选历史时间跨度，仅支持 '1h', '12h', '24h', '1w', '30d'。若未指定则传 null。
        """
        return await self._send_output(
            event,
            await self._safe_query_output(
                event,
                "history",
                selector=system,
                history_range=range,
            ),
        )

    @staticmethod
    def _history_arguments(query: str) -> tuple[str, str | None]:
        """Split ``<name-or-id> [range]``, treating only a trailing supported
        range as the range so system names may end in other duration words."""
        parts = query.strip().split()
        if not parts:
            return "", None
        tail = parts[-1].casefold()
        if len(parts) > 1 and tail in SUPPORTED_HISTORY_RANGES:
            return " ".join(parts[:-1]), tail
        return " ".join(parts), None


def bind_loader_module(loader_module: str) -> None:
    """Bind registrations from this implementation module to AstrBot's loader module."""
    if loader_module in star_map:
        return
    metadata = star_map.pop(_IMPLEMENTATION_MODULE, None)
    if metadata is None:
        raise RuntimeError("Beszel plugin metadata was not registered")
    metadata.module_path = loader_module
    star_map[loader_module] = metadata
    BeszelPlugin.__module__ = loader_module

    for handler in list(star_handlers_registry):
        if handler.handler_module_path != _IMPLEMENTATION_MODULE:
            continue
        old_full_name = handler.handler_full_name
        handler.handler.__module__ = loader_module
        handler.handler_module_path = loader_module
        handler.handler_full_name = f"{loader_module}_{handler.handler_name}"
        star_handlers_registry.star_handlers_map.pop(old_full_name, None)
        star_handlers_registry.star_handlers_map[handler.handler_full_name] = handler
