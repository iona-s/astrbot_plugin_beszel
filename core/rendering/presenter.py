"""Map validated Beszel models to immutable presentation documents."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from ..beszel.models import (
    ContainerHistoryPoint,
    SystemDetailView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemSummary,
)
from ..formatters import StatusState, status_state
from .formatters import (
    bytes_iec,
    format_bandwidth,
    gb_iec,
    gb_to_bytes,
    mb_iec,
    mib_rate_to_bytes,
    percent,
    safe_float,
    uptime_cn,
)
from .models import (
    ChartPoint,
    ChartSeries,
    ChartUnit,
    ContainerRow,
    DetailRow,
    DetailSection,
    DocumentFooter,
    DocumentHeader,
    HistoryChartCard,
    HistoryDocument,
    MetadataItem,
    OverviewDocument,
    OverviewRow,
    ProgressMetric,
    StatusBadge,
    StatusDocument,
)
from .styles import (
    dynamic_series_color,
    metric_color,
    series_color,
    threshold_color,
)

STATUS_LABELS: dict[StatusState, str] = {
    "up": "在线",
    "down": "离线",
    "unknown": "未知",
}

HISTORY_RANGE_LABELS = {
    "1h": "1 小时",
    "12h": "12 小时",
    "24h": "24 小时",
    "1w": "1 周",
    "30d": "30 天",
}


@dataclass(frozen=True, slots=True)
class _GpuMetric:
    name: str
    usage: float | None
    memory_used_mib: float | None
    memory_total_mib: float | None
    power_watts: float | None


@dataclass(frozen=True, slots=True)
class _DiskMetric:
    name: str
    percent_used: float | None
    secondary_text: str


@dataclass(frozen=True, slots=True)
class _CapacityHistory:
    points: tuple[tuple[datetime, float], ...]
    unit: ChartUnit
    maximum: float | None


class PresentationBuilder:
    """Build backend-neutral documents without importing a rendering backend."""

    def __init__(
        self,
        *,
        plugin_name: str,
        show_connection_address: bool,
        display_timezone: tzinfo,
        container_history_threshold: int = 10,
    ) -> None:
        self.plugin_name = plugin_name
        self.show_connection_address = show_connection_address
        self.display_timezone = display_timezone
        self.container_history_threshold = container_history_threshold

    def build_overview(
        self,
        systems: Iterable[SystemSummary],
        *,
        page_number: int,
        page_count: int,
        summary_counts: tuple[int, int, int],
    ) -> OverviewDocument:
        systems_tuple = tuple(systems)
        page_number = max(1, page_number)
        page_count = max(page_number, page_count)
        total_count, online_count, offline_count = summary_counts
        header = DocumentHeader(
            title="所有客户端",
            subtitle=f"共 {total_count} 个探针节点",
            metadata=(
                MetadataItem("在线", str(online_count)),
                MetadataItem("离线", str(offline_count)),
            ),
        )

        return OverviewDocument(
            header=header,
            rows=tuple(self._overview_row(item) for item in systems_tuple),
            footer=DocumentFooter(
                f"{self.plugin_name} · 第 {page_number}/{page_count} 页"
            ),
        )

    def build_status(self, view: SystemDetailView) -> StatusDocument:
        summary = view.summary
        stats = view.metrics.stats if view.metrics else {}
        status = self._status_badge(summary.status)

        cpu_value = self._cpu_value(stats)
        memory_percent = self._usage_percent(
            stats,
            "mp",
            used_key="mu",
            total_key="m",
        )
        memory_used = safe_float(stats.get("mu"))
        memory_total = safe_float(stats.get("m"))
        if memory_total is None and view.details and view.details.memory:
            memory_total = view.details.memory / (1024**3)
        memory_secondary = self._capacity_text(memory_used, memory_total)

        bandwidth = stats.get("b")
        rx_speed, tx_speed, total_bandwidth = self._bandwidth_values(bandwidth)
        load_average = self._extract_load_avg(stats.get("la"))
        if load_average is None:
            load_average = self._extract_load_avg(summary.info.get("la"))

        metric_cards = (
            ProgressMetric(
                label="CPU 使用率",
                value=safe_float(cpu_value),
                value_text=percent(cpu_value),
                secondary_text=f"负载: {load_average}" if load_average else "",
                color=threshold_color(safe_float(cpu_value), summary.status),
            ),
            ProgressMetric(
                label="内存使用率",
                value=memory_percent,
                value_text=percent(memory_percent),
                secondary_text=memory_secondary,
                color=threshold_color(memory_percent, summary.status),
            ),
            ProgressMetric(
                label="网络实时带宽",
                value=total_bandwidth,
                value_text=(
                    format_bandwidth(total_bandwidth)
                    if total_bandwidth is not None
                    else "N/A"
                ),
                secondary_text=(
                    f"↓ {format_bandwidth(rx_speed)} · ↑ {format_bandwidth(tx_speed)}"
                    if rx_speed is not None and tx_speed is not None
                    else ""
                ),
                color=metric_color("net"),
                maximum=0.0,
            ),
        )

        container_rows: list[ContainerRow] = []
        for c in view.containers:
            container_rows.append(
                ContainerRow(
                    name=c.name,
                    cpu_text=percent(c.cpu),
                    memory_text=mb_iec(c.memory),
                )
            )

        container_rows.sort(key=lambda item: item.name.casefold())

        sections = self._status_sections(view, stats)
        header_metadata = []
        if view.details and view.details.os:
            header_metadata.append(MetadataItem("系统", view.details.os))
        arch_val = view.details.arch if view.details else None
        if arch_val:
            header_metadata.append(MetadataItem("架构", arch_val))
        return StatusDocument(
            header=DocumentHeader(
                title=summary.name,
                subtitle=f"探针 ID: {summary.id}",
                status=status,
                metadata=tuple(header_metadata),
            ),
            metric_cards=metric_cards,
            sections=sections,
            containers=tuple(container_rows),
            footer=DocumentFooter(f"{self.plugin_name} · {summary.name}"),
        )

    def build_history(self, view: SystemHistoryView) -> HistoryDocument:
        points = tuple(sorted(view.points, key=lambda point: point.created))
        summary = view.summary
        info = summary.info or {}

        range_value = view.range.value
        range_label = HISTORY_RANGE_LABELS.get(range_value, range_value)
        conn_addr = (
            self._address(summary.host, summary.port)
            if (self.show_connection_address and summary.host)
            else ""
        )
        details = view.details
        uptime_str = uptime_cn(info.get("u")) if info.get("u") is not None else ""
        os_str = details.os if details and details.os else ""
        cpu_str = details.cpu if details and details.cpu else ""
        memory_str = (
            gb_iec(details.memory / (1024**3))
            if (details and details.memory is not None)
            else ""
        )

        header = DocumentHeader(
            title=summary.name,
            status=self._status_badge(summary.status),
            range_label=range_label,
            connection_address=conn_addr,
            uptime_text=uptime_str,
            os_text=os_str,
            cpu_text=cpu_str,
            memory_text=memory_str,
        )
        container_cpu_card: HistoryChartCard | None = None
        container_mem_card: HistoryChartCard | None = None
        if view.container_points:
            container_points = tuple(
                sorted(view.container_points, key=lambda p: p.created)
            )
            container_cpu_card, container_mem_card = self._container_history_cards(
                container_points,
                gap_seconds=view.range.expected_interval.total_seconds() * 1.5,
            )
        cards = list(
            self._history_cards(
                points,
                gap_seconds=view.range.expected_interval.total_seconds() * 1.5,
                container_cpu_card=container_cpu_card,
                container_mem_card=container_mem_card,
            )
        )
        return HistoryDocument(
            header=header,
            cards=tuple(cards),
            footer=DocumentFooter(f"{self.plugin_name} · {summary.name}"),
        )

    def _overview_row(self, system: SystemSummary) -> OverviewRow:
        info = system.info or {}
        state = status_state(system.status)
        is_online = state == "up"

        # CPU
        cpu_val = self._cpu_value(info) if is_online else None
        cpu_percent = safe_float(cpu_val)
        cpu_text = percent(cpu_percent) if cpu_percent is not None else "-"
        cpu_color = threshold_color(cpu_percent, status=system.status)

        # Memory
        memory_percent = (
            self._usage_percent(
                info,
                "mp",
                used_key="mu",
                total_key="m",
            )
            if is_online
            else None
        )
        memory_text = percent(memory_percent) if memory_percent is not None else "-"
        memory_color = threshold_color(memory_percent, status=system.status)

        # Disk
        disk_percent = (
            self._usage_percent(
                info,
                "dp",
                used_key="du",
                total_key="d",
            )
            if is_online
            else None
        )
        disk_text = percent(disk_percent) if disk_percent is not None else "-"
        disk_color = threshold_color(disk_percent, status=system.status)

        # Load average & load_state (P1)
        if is_online:
            la_raw = info.get("la")
            if isinstance(la_raw, (list, tuple)) and la_raw:
                la_values = [
                    v for item in la_raw if (v := safe_float(item)) is not None
                ]
            elif (v := safe_float(la_raw)) is not None:
                la_values = [v]
            else:
                la_values = []

            if la_values:
                load_text = " ".join(f"{v:.2f}" for v in la_values)
                threads_raw = safe_float(info.get("t"))
                if threads_raw is None or threads_raw <= 0:
                    threads_raw = 1.0
                threads = max(1.0, float(threads_raw))
                max_load = max(la_values)
                load_percent = (max_load / threads) * 100.0
                if load_percent < 65.0:
                    load_state = "up"
                elif load_percent < 90.0:
                    load_state = "warn"
                else:
                    load_state = "down"
            else:
                load_text = "-"
                load_state = "none"
        else:
            load_text = "-"
            load_state = "none"

        # Network bandwidth
        bandwidth = format_bandwidth(info.get("bb")) if is_online else "N/A"
        network_text = bandwidth if bandwidth != "N/A" else "-"

        # Services (P2)
        services = info.get("sv") if is_online else None
        if isinstance(services, (list, tuple)) and len(services) >= 2:
            total = safe_float(services[0])
            failed = safe_float(services[1])
            if total is not None and total > 0 and failed is not None and failed >= 0:
                total_int = int(total)
                failed_int = int(failed)
                services_text = f"{total_int} (失败: {failed_int})"
                services_state = "down" if failed_int > 0 else "up"
            else:
                services_text = "-"
                services_state = "none"
        else:
            services_text = "-"
            services_state = "none"

        # Uptime (P2)
        if state == "up":
            uptime = info.get("u")
            uptime_text = uptime_cn(uptime) if uptime is not None else "-"
        elif state == "down":
            uptime_text = "离线"
        else:
            uptime_text = "未知"

        # Agent version
        agent_version = info.get("v") or "-"

        return OverviewRow(
            name=system.name,
            status_state=state,
            cpu_percent=cpu_percent,
            cpu_text=cpu_text,
            cpu_color=cpu_color,
            memory_percent=memory_percent,
            memory_text=memory_text,
            memory_color=memory_color,
            disk_percent=disk_percent,
            disk_text=disk_text,
            disk_color=disk_color,
            load_text=load_text,
            load_state=load_state,
            network_text=network_text,
            services_text=services_text,
            services_state=services_state,
            uptime_text=uptime_text,
            agent_version=agent_version,
        )

    def _status_sections(
        self, view: SystemDetailView, stats: dict[str, Any]
    ) -> tuple[DetailSection, ...]:
        summary = view.summary
        gpus = self._extract_gpus(stats.get("g"))
        if not gpus:
            gpus = self._extract_gpus(summary.info.get("g"))

        sections: list[DetailSection] = []
        if gpus:
            gpu_rows: list[DetailRow] = []
            raw_temps = stats.get("t")
            for gpu in gpus:
                gpu_rows.append(
                    DetailRow(
                        label=f"{gpu.name} 核心占用",
                        value=percent(gpu.usage),
                    )
                )
                if gpu.memory_used_mib is not None and gpu.memory_total_mib is not None:
                    memory_percent = (
                        gpu.memory_used_mib / gpu.memory_total_mib * 100.0
                        if gpu.memory_total_mib > 0
                        else None
                    )
                    gpu_rows.append(
                        DetailRow(
                            label=f"{gpu.name} 显存",
                            value=(
                                f"{percent(memory_percent)} "
                                f"({mb_iec(gpu.memory_used_mib)} / "
                                f"{mb_iec(gpu.memory_total_mib)})"
                            ),
                        )
                    )
                if gpu.power_watts is not None:
                    gpu_rows.append(
                        DetailRow(
                            label=f"{gpu.name} 功耗",
                            value=f"{gpu.power_watts:.0f} W",
                        )
                    )
                if isinstance(raw_temps, dict):
                    for sensor_name, raw_value in sorted(raw_temps.items()):
                        sensor_value = safe_float(raw_value)
                        if (
                            sensor_value is not None
                            and gpu.name.casefold() in str(sensor_name).casefold()
                        ):
                            gpu_rows.append(
                                DetailRow(
                                    label=f"{gpu.name} 温度",
                                    value=f"{sensor_value:.1f} °C",
                                )
                            )
                            break
            sections.append(DetailSection("独立显卡监控 (GPU)", tuple(gpu_rows)))

        all_disks: list[_DiskMetric] = []
        disk_percent = self._usage_percent(
            stats,
            "dp",
            used_key="du",
            total_key="d",
        )
        if disk_percent is not None or safe_float(stats.get("du")) is not None:
            all_disks.append(
                _DiskMetric(
                    "根磁盘 ( / )",
                    disk_percent,
                    self._capacity_text(
                        safe_float(stats.get("du")), safe_float(stats.get("d"))
                    ),
                )
            )
        efs_source = stats if stats.get("efs") is not None else summary.info
        all_disks.extend(
            _DiskMetric(f"磁盘 ({item.name})", item.percent_used, item.secondary_text)
            for item in self._extract_efs_items(efs_source)
        )
        if all_disks:
            sections.append(
                DetailSection(
                    "存储与挂载磁盘",
                    tuple(
                        DetailRow(
                            label=disk.name,
                            value=(
                                f"{percent(disk.percent_used)}"
                                + (
                                    f" ({disk.secondary_text})"
                                    if disk.secondary_text
                                    else ""
                                )
                            ),
                            percent=disk.percent_used,
                            color=threshold_color(disk.percent_used, summary.status),
                        )
                        for disk in all_disks
                    ),
                )
            )

        network_rows = self._network_rows(stats)
        if network_rows:
            sections.append(DetailSection("网络接口流量明细", tuple(network_rows)))

        config_rows = self._system_rows(view)
        sections.append(DetailSection("硬件与系统配置", tuple(config_rows)))
        return tuple(sections)

    def _system_rows(self, view: SystemDetailView) -> list[DetailRow]:
        summary = view.summary
        rows: list[DetailRow] = []
        details = view.details
        if details:
            fields = (
                ("主机名", details.hostname),
                ("操作系统", details.os),
                ("内核版本", details.kernel),
                ("系统架构", details.arch),
                ("处理器型号", details.cpu),
                (
                    "核心 / 线程",
                    f"{details.cores or 'N/A'} 核 / {details.threads or 'N/A'} 线程",
                ),
                ("物理总内存", bytes_iec(details.memory)),
                (
                    "Agent 版本",
                    f"v{str(summary.info['v']).lstrip('v')}"
                    if summary.info.get("v")
                    else "N/A",
                ),
                (
                    "系统运行时间",
                    uptime_cn(summary.info.get("u"))
                    if summary.info.get("u") is not None
                    else "N/A",
                ),
            )
            rows.extend(
                DetailRow(label=label, value=str(value) if value is not None else "N/A")
                for label, value in fields
            )
        if self.show_connection_address and summary.host:
            rows.append(
                DetailRow(
                    label="连接地址",
                    value=self._address(summary.host, summary.port),
                )
            )
        return rows

    def _network_rows(self, stats: dict[str, Any]) -> list[DetailRow]:
        raw_interfaces = stats.get("ni")
        if not isinstance(raw_interfaces, dict):
            return []
        rows: list[DetailRow] = []
        for name, raw_values in sorted(raw_interfaces.items()):
            if not isinstance(raw_values, (list, tuple)) or len(raw_values) < 2:
                continue
            tx = format_bandwidth(raw_values[0])
            rx = format_bandwidth(raw_values[1])
            value = f"↓ {rx} · ↑ {tx}"
            if len(raw_values) >= 4:
                value += (
                    f" · 总下行: {bytes_iec(raw_values[3])}"
                    f" · 总上行: {bytes_iec(raw_values[2])}"
                )
            rows.append(DetailRow(label=f"网卡: {name}", value=value))
        return rows

    def _history_cards(
        self,
        points: tuple[SystemHistoryPoint, ...],
        *,
        gap_seconds: float,
        container_cpu_card: HistoryChartCard | None = None,
        container_mem_card: HistoryChartCard | None = None,
    ) -> list[HistoryChartCard]:
        valid_points = [
            (point.created, point.stats)
            for point in points
            if point.created is not None and isinstance(point.stats, dict)
        ]
        cards: list[HistoryChartCard] = []

        cpu_points = [
            (created, value)
            for created, stats in valid_points
            if (value := self._cpu_value(stats)) is not None
        ]
        self._append_chart(
            cards,
            "CPU 使用率",
            "系统范围内的平均 CPU 使用率",
            ChartUnit.PERCENT,
            [("使用率", cpu_points, "cpu")],
            gap_seconds,
        )
        if container_cpu_card is not None:
            cards.append(container_cpu_card)

        mu_history = self._capacity_history(
            (
                (
                    created,
                    stats.get("mu"),
                    stats.get("m"),
                    stats.get("mp"),
                )
                for created, stats in valid_points
            )
        )
        mz_history = self._capacity_history(
            (
                (created, stats.get("mz"), stats.get("m"), None)
                for created, stats in valid_points
            )
        )
        mb_history = self._capacity_history(
            (
                (created, stats.get("mb"), stats.get("m"), None)
                for created, stats in valid_points
            )
        )

        mem_series: list[tuple[str, list[tuple[datetime, float]], Any]] = [
            ("已用", list(mu_history.points), "mem")
        ]
        if any(p[1] > 0 for p in mz_history.points):
            mem_series.append(("ZFS ARC", list(mz_history.points), "mem"))
        if any(p[1] > 0 for p in mb_history.points):
            mem_series.append(("缓存 / 缓冲", list(mb_history.points), "mem"))

        self._append_chart(
            cards,
            "内存使用",
            "采集时间下的精确内存使用率",
            mu_history.unit,
            mem_series,
            gap_seconds,
            maximum_override=mu_history.maximum,
        )
        if container_mem_card is not None:
            cards.append(container_mem_card)

        disk_history = self._capacity_history(
            (
                (
                    created,
                    stats.get("du"),
                    stats.get("d"),
                    stats.get("dp"),
                )
                for created, stats in valid_points
            )
        )
        self._append_chart(
            cards,
            "磁盘使用",
            "根分区的使用",
            disk_history.unit,
            [("根分区", list(disk_history.points), "disk")],
            gap_seconds,
            maximum_override=disk_history.maximum,
        )

        disk_read: list[tuple[datetime, float]] = []
        disk_write: list[tuple[datetime, float]] = []
        for created, stats in valid_points:
            dio = stats.get("dio")
            read_value = write_value = 0.0
            if isinstance(dio, (list, tuple)) and len(dio) >= 2:
                read_value = safe_float(dio[0]) or 0.0
                write_value = safe_float(dio[1]) or 0.0
            else:
                read_value = mib_rate_to_bytes(safe_float(stats.get("dr")) or 0.0)
                write_value = mib_rate_to_bytes(safe_float(stats.get("dw")) or 0.0)
            disk_read.append((created, read_value))
            disk_write.append((created, write_value))
        if disk_read or disk_write:
            self._append_chart(
                cards,
                "磁盘 I/O",
                "根文件系统的吞吐量",
                ChartUnit.BYTES_PER_SECOND,
                [("读取", disk_read, "disk_io"), ("写入", disk_write, "disk_io")],
                gap_seconds,
            )

        receive: list[tuple[datetime, float]] = []
        transmit: list[tuple[datetime, float]] = []
        for created, stats in valid_points:
            bandwidth = stats.get("b")
            if isinstance(bandwidth, (list, tuple)) and len(bandwidth) >= 2:
                tx = safe_float(bandwidth[0])
                rx = safe_float(bandwidth[1])
                if tx is not None:
                    transmit.append((created, tx))
                if rx is not None:
                    receive.append((created, rx))
            else:
                value = safe_float(bandwidth)
                if value is not None:
                    receive.append((created, value))
        if receive or transmit:
            self._append_chart(
                cards,
                "带宽",
                "公共接口的网络流量",
                ChartUnit.BYTES_PER_SECOND,
                [("下行 (Rx)", receive, "net"), ("上行 (Tx)", transmit, "net")],
                gap_seconds,
            )

        swap_samples: list[tuple[datetime, Any, Any, Any]] = []
        for created, stats in valid_points:
            used = safe_float(stats.get("su"))
            swap_samples.append(
                (
                    created,
                    used,
                    stats.get("s"),
                    stats.get("sp"),
                )
            )
        swap_history = self._capacity_history(
            swap_samples,
            allow_used_without_total=True,
        )
        if swap_history.points and max(value for _, value in swap_history.points) > 0:
            self._append_chart(
                cards,
                "Swap 交换空间",
                "系统使用的 Swap 空间",
                swap_history.unit,
                [("Swap", list(swap_history.points), "swap")],
                gap_seconds,
                maximum_override=swap_history.maximum,
            )

        load_one: list[tuple[datetime, float]] = []
        load_five: list[tuple[datetime, float]] = []
        load_fifteen: list[tuple[datetime, float]] = []
        for created, stats in valid_points:
            raw_load = stats.get("la")
            if isinstance(raw_load, (list, tuple)):
                buckets = (load_one, load_five, load_fifteen)
                for index, bucket in enumerate(buckets):
                    if len(raw_load) > index:
                        value = safe_float(raw_load[index])
                        if value is not None:
                            bucket.append((created, value))
            else:
                value = safe_float(raw_load)
                if value is not None:
                    load_one.append((created, value))
        if load_one:
            load_specs = [("1 分钟", load_one, "load")]
            if load_five:
                load_specs.append(("5 分钟", load_five, "load"))
            if load_fifteen:
                load_specs.append(("15 分钟", load_fifteen, "load"))
            self._append_chart(
                cards,
                "系统负载",
                "系统负载平均值随时间变化",
                ChartUnit.LOAD,
                load_specs,
                gap_seconds,
            )

        temperature_points: dict[str, list[tuple[datetime, float]]] = {}
        for created, stats in valid_points:
            direct = safe_float(stats.get("dt"))
            raw_temperature = stats.get("t")
            if direct is not None:
                temperature_points.setdefault("主温度", []).append((created, direct))
            elif (scalar := safe_float(raw_temperature)) is not None:
                temperature_points.setdefault("主温度", []).append((created, scalar))
            elif isinstance(raw_temperature, (list, tuple)):
                values = [safe_float(item) for item in raw_temperature]
                finite_values = [item for item in values if item is not None]
                if finite_values:
                    temperature_points.setdefault("主温度", []).append(
                        (created, max(finite_values))
                    )
            elif isinstance(raw_temperature, dict):
                for name, raw_value in raw_temperature.items():
                    value = safe_float(raw_value)
                    if value is not None:
                        temperature_points.setdefault(str(name), []).append(
                            (created, value)
                        )
        if temperature_points:
            ordered_temperatures = sorted(
                temperature_points.items(),
                key=lambda item: (
                    -item[1][-1][1] if item[1] else 0.0,
                    item[0].casefold(),
                ),
            )
            top_temperatures = ordered_temperatures[:4]
            extra_count = max(0, len(ordered_temperatures) - 4)
            total_temps = len(top_temperatures)
            temp_specs = [
                (
                    name,
                    values,
                    dynamic_series_color(
                        i, total_temps, saturation=0.60, lightness=0.55
                    ),
                )
                for i, (name, values) in enumerate(top_temperatures)
            ]
            self._append_chart(
                cards,
                "温度",
                "系统传感器的温度",
                ChartUnit.TEMPERATURE,
                temp_specs,
                gap_seconds,
                extra_series_count=extra_count,
            )

        fan_points: dict[str, list[tuple[datetime, float]]] = {}
        for created, stats in valid_points:
            raw_fans = stats.get("f")
            if isinstance(raw_fans, dict):
                for name, raw_value in raw_fans.items():
                    value = safe_float(raw_value)
                    if value is not None:
                        fan_points.setdefault(str(name), []).append((created, value))
        if fan_points:
            ordered_fans = sorted(
                fan_points.items(),
                key=lambda item: (
                    -max((value for _, value in item[1]), default=0.0),
                    item[0].casefold(),
                ),
            )
            total_fans = len(ordered_fans)
            fan_specs = [
                (
                    name,
                    values,
                    dynamic_series_color(
                        i, total_fans, saturation=0.60, lightness=0.55
                    ),
                )
                for i, (name, values) in enumerate(ordered_fans)
            ]
            self._append_chart(
                cards,
                "风扇",
                "系统风扇转速 (RPM)",
                ChartUnit.RPM,
                fan_specs,
                gap_seconds,
            )

        battery_values = [
            (created, value)
            for created, stats in valid_points
            if (value := self._battery_value(stats.get("bat"))) is not None
        ]
        if battery_values:
            self._append_chart(
                cards,
                "电池电量",
                "系统电池剩余电量",
                ChartUnit.PERCENT,
                [("电量", battery_values, "battery")],
                gap_seconds,
            )

        gpu_samples: dict[str, list[tuple[datetime, _GpuMetric]]] = {}
        for created, stats in valid_points:
            for gpu in self._extract_gpus(stats.get("g")):
                gpu_samples.setdefault(gpu.name, []).append((created, gpu))
        gpu_names = sorted(gpu_samples, key=str.casefold)
        for i, gpu_name in enumerate(gpu_names):
            usage: list[tuple[datetime, float]] = []
            vram: list[tuple[datetime, float]] = []
            power: list[tuple[datetime, float]] = []
            total_vram: float | None = None
            for created, gpu in gpu_samples[gpu_name]:
                if gpu.usage is not None:
                    usage.append((created, gpu.usage))
                if (
                    gpu.memory_used_mib is not None
                    and gpu.memory_total_mib is not None
                    and gpu.memory_total_mib > 0
                ):
                    vram.append((created, gpu.memory_used_mib * (1024**2)))
                    if total_vram is None:
                        total_vram = gpu.memory_total_mib * (1024**2)
                if gpu.power_watts is not None:
                    power.append((created, gpu.power_watts))
            if power:
                self._append_chart(
                    cards,
                    f"{gpu_name} 功耗",
                    "GPU 平均能耗",
                    ChartUnit.WATTS,
                    [
                        (
                            "功耗",
                            power,
                            dynamic_series_color(
                                i,
                                len(gpu_names),
                                saturation=0.65,
                                lightness=0.52,
                                base_hue=226.0,
                            ),
                        )
                    ],
                    gap_seconds,
                )
            if usage:
                self._append_chart(
                    cards,
                    f"{gpu_name} 使用",
                    f"{gpu_name} 平均利用率",
                    ChartUnit.PERCENT,
                    [("利用率", usage, "gpu")],
                    gap_seconds,
                )
            if vram:
                self._append_chart(
                    cards,
                    f"{gpu_name} VRAM",
                    "采集时间下的精确内存使用率",
                    ChartUnit.BYTES,
                    [("VRAM", vram, "mem")],
                    gap_seconds,
                    maximum_override=total_vram,
                )

        filesystem_names = sorted(
            {
                str(name)
                for _, stats in valid_points
                if isinstance(stats.get("efs"), dict)
                for name in stats["efs"]
            },
            key=str.casefold,
        )
        for filesystem_name in filesystem_names:
            usage_samples: list[tuple[datetime, Any, Any, Any]] = []
            reads: list[tuple[datetime, float]] = []
            writes: list[tuple[datetime, float]] = []
            for created, stats in valid_points:
                filesystems = stats.get("efs")
                if (
                    not isinstance(filesystems, dict)
                    or filesystem_name not in filesystems
                ):
                    continue
                data = filesystems[filesystem_name]
                scalar = safe_float(data)
                if scalar is not None:
                    usage_samples.append((created, None, None, scalar))
                    continue
                if not isinstance(data, dict):
                    continue
                used = safe_float(data.get("du"))
                total = safe_float(data.get("d"))
                usage_samples.append((created, used, total, data.get("dp")))
                read_value = safe_float(data.get("rb"))
                write_value = safe_float(data.get("wb"))
                if read_value is None:
                    read_value = mib_rate_to_bytes(safe_float(data.get("r")) or 0.0)
                if write_value is None:
                    write_value = mib_rate_to_bytes(safe_float(data.get("w")) or 0.0)
                reads.append((created, read_value))
                writes.append((created, write_value))
            filesystem_history = self._capacity_history(usage_samples)
            if filesystem_history.points:
                self._append_chart(
                    cards,
                    f"{filesystem_name} 使用",
                    f"{filesystem_name}的磁盘使用",
                    filesystem_history.unit,
                    [
                        (
                            filesystem_name,
                            list(filesystem_history.points),
                            "disk",
                        )
                    ],
                    gap_seconds,
                    maximum_override=filesystem_history.maximum,
                )
            if reads or writes:
                self._append_chart(
                    cards,
                    f"{filesystem_name} I/O",
                    f"{filesystem_name}的吞吐量",
                    ChartUnit.BYTES_PER_SECOND,
                    [("读取", reads, "disk_io"), ("写入", writes, "disk_io")],
                    gap_seconds,
                )
        return cards

    def _container_history_cards(
        self,
        points: tuple[ContainerHistoryPoint, ...],
        *,
        gap_seconds: float,
    ) -> tuple[HistoryChartCard | None, HistoryChartCard | None]:
        cpu_by_container: dict[str, list[tuple[datetime, float]]] = {}
        mem_by_container: dict[str, list[tuple[datetime, float]]] = {}

        for point in points:
            created = point.created
            for c in point.stats:
                name = c.name
                if (cpu_val := safe_float(c.cpu)) is not None:
                    cpu_by_container.setdefault(name, []).append((created, cpu_val))
                if (mem_val := safe_float(c.memory)) is not None:
                    mem_by_container.setdefault(name, []).append(
                        (created, mem_val * (1024**2))
                    )

        if not cpu_by_container and not mem_by_container:
            return None, None

        threshold = max(0, min(50, self.container_history_threshold))

        # CPU threshold filtering
        peak_cpu = {
            name: max((v for _, v in pts), default=0.0)
            for name, pts in cpu_by_container.items()
        }
        sorted_cpu = sorted(
            peak_cpu.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        if sorted_cpu:
            all_cpu_vals = [v for pts in cpu_by_container.values() for _, v in pts]
            _, cpu_axis_max = self._axis_bounds(ChartUnit.PERCENT, all_cpu_vals, None)
            if threshold <= 0:
                kept_cpu_names = [name for name, _ in sorted_cpu]
            else:
                cpu_cutoff = cpu_axis_max * (threshold / 100.0)
                kept_cpu_names = [
                    name for name, peak in sorted_cpu if peak >= cpu_cutoff
                ]
            extra_cpu = max(0, len(sorted_cpu) - len(kept_cpu_names))
        else:
            kept_cpu_names = []
            extra_cpu = 0

        # Memory threshold filtering
        peak_mem = {
            name: max((v for _, v in pts), default=0.0)
            for name, pts in mem_by_container.items()
        }
        sorted_mem = sorted(
            peak_mem.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        if sorted_mem:
            all_mem_vals = [v for pts in mem_by_container.values() for _, v in pts]
            _, mem_axis_max = self._axis_bounds(ChartUnit.BYTES, all_mem_vals, None)
            if threshold <= 0:
                kept_mem_names = [name for name, _ in sorted_mem]
            else:
                mem_cutoff = mem_axis_max * (threshold / 100.0)
                kept_mem_names = [
                    name for name, peak in sorted_mem if peak >= mem_cutoff
                ]
            extra_mem = max(0, len(sorted_mem) - len(kept_mem_names))
        else:
            kept_mem_names = []
            extra_mem = 0

        all_kept_names = sorted(
            set(kept_cpu_names) | set(kept_mem_names), key=str.casefold
        )
        total_containers = len(all_kept_names)
        name_to_color = {
            name: dynamic_series_color(
                i,
                total_containers,
                saturation=0.65,
                lightness=0.50,
            )
            for i, name in enumerate(all_kept_names)
        }

        cpu_card: HistoryChartCard | None = None
        mem_card: HistoryChartCard | None = None

        if kept_cpu_names:
            cpu_specs = [
                (name, cpu_by_container[name], name_to_color[name])
                for name in kept_cpu_names
            ]
            cpu_cards: list[HistoryChartCard] = []
            self._append_chart(
                cpu_cards,
                "容器 CPU 使用率",
                "容器范围内的 CPU 使用率",
                ChartUnit.PERCENT,
                cpu_specs,
                gap_seconds,
                extra_series_count=extra_cpu,
            )
            if cpu_cards:
                cpu_card = cpu_cards[0]

        if kept_mem_names:
            mem_specs = [
                (name, mem_by_container[name], name_to_color[name])
                for name in kept_mem_names
            ]
            mem_cards: list[HistoryChartCard] = []
            self._append_chart(
                mem_cards,
                "容器内存使用",
                "采集时间下的容器内存使用",
                ChartUnit.BYTES,
                mem_specs,
                gap_seconds,
                extra_series_count=extra_mem,
            )
            if mem_cards:
                mem_card = mem_cards[0]

        return cpu_card, mem_card

    def _append_chart(
        self,
        cards: list[HistoryChartCard],
        title: str,
        subtitle: str,
        unit: ChartUnit,
        series_specs: list[tuple[str, list[tuple[datetime, float]], Any]],
        gap_seconds: float,
        *,
        maximum_override: float | None = None,
        extra_series_count: int = 0,
    ) -> None:
        series: list[ChartSeries] = []
        for index, (name, raw_points, metric_key) in enumerate(series_specs):
            normalized_points = tuple(
                ChartPoint(created=created, value=float(value))
                for created, value in sorted(raw_points, key=lambda item: item[0])
                if math.isfinite(float(value))
            )
            if not normalized_points:
                continue
            segments = self.split_series_points(normalized_points, gap_seconds)
            values = tuple(point.value for point in normalized_points)
            color = (
                metric_key
                if isinstance(metric_key, tuple)
                else series_color(index, metric_key=metric_key)
            )
            series.append(
                ChartSeries(
                    name=name,
                    color=color,
                    points=normalized_points,
                    segments=segments,
                    current_value=values[-1],
                )
            )
        if not series:
            return
        axis_min, axis_max = self._axis_bounds(
            unit,
            [point.value for item in series for point in item.points],
            maximum_override,
        )
        cards.append(
            HistoryChartCard(
                title=title,
                subtitle=subtitle,
                unit=unit,
                series=tuple(series),
                axis_min=axis_min,
                axis_max=axis_max,
                extra_series_count=extra_series_count,
            )
        )

    @staticmethod
    def split_series_points(
        points: Iterable[ChartPoint], gap_seconds: float
    ) -> tuple[tuple[ChartPoint, ...], ...]:
        """Split chart points at missing samples or non-finite values."""
        segments: list[list[ChartPoint]] = []
        previous: datetime | None = None
        for point in points:
            if not math.isfinite(point.value):
                previous = None
                continue
            if (
                previous is None
                or (point.created - previous).total_seconds() > gap_seconds
            ):
                segments.append([])
            segments[-1].append(point)
            previous = point.created
        return tuple(tuple(segment) for segment in segments if segment)

    @staticmethod
    def _axis_bounds(
        unit: ChartUnit, values: list[float], maximum_override: float | None
    ) -> tuple[float, float]:
        if unit is ChartUnit.PERCENT:
            if maximum_override is not None and maximum_override > 0:
                return 0.0, maximum_override
            maximum = max(values) if values else 100.0
            if maximum <= 0:
                return 0.0, 100.0
            return 0.0, 100.0 if maximum >= 85.0 else max(10.0, maximum * 1.15)
        if unit is ChartUnit.BYTES:
            maximum = (
                maximum_override
                if maximum_override and maximum_override > 0
                else max(values, default=1.0) * 1.1
            )
            return 0.0, max(1.0, maximum)
        if unit is ChartUnit.BYTES_PER_SECOND:
            return 0.0, max(1.0, max(values, default=1.0) * 1.15)
        if unit is ChartUnit.WATTS:
            return 0.0, max(10.0, max(values, default=1.0) * 1.15)
        if unit is ChartUnit.TEMPERATURE:
            minimum = min(values) if values else 30.0
            maximum = max(values) if values else 70.0
            low = max(0.0, minimum - 5.0)
            return low, max(low + 10.0, maximum + 5.0)
        if unit is ChartUnit.RPM:
            return 0.0, max(100.0, max(values, default=1000.0) * 1.15)
        if unit is ChartUnit.LOAD:
            return 0.0, max(0.2, max(values, default=1.0) * 1.15)
        minimum = min(values) if values else 0.0
        maximum = max(values) if values else 1.0
        low = minimum if minimum < 0 else 0.0
        return low, maximum if maximum > low else low + 1.0

    @staticmethod
    def _status_badge(status: str | None) -> StatusBadge:
        state = status_state(status)
        return StatusBadge(state=state, label=STATUS_LABELS[state])

    @staticmethod
    def _capacity_text(used: float | None, total: float | None) -> str:
        if used is not None and total is not None and total > 0:
            return f"{gb_iec(used)} / {gb_iec(total)}"
        return gb_iec(used) if used is not None else ""

    @staticmethod
    def _address(host: str, port: int | None) -> str:
        return f"{host}:{port}" if port is not None else host

    @staticmethod
    def _cpu_value(stats: dict[str, Any]) -> float | None:
        return safe_float(stats.get("cpu"))

    @staticmethod
    def _capacity_history(
        samples: Iterable[tuple[datetime, Any, Any, Any]],
        *,
        allow_used_without_total: bool = False,
    ) -> _CapacityHistory:
        """Normalize GiB usage and percentage samples to one chart unit."""
        normalized = tuple(
            (
                created,
                safe_float(used),
                safe_float(total),
                safe_float(percentage),
            )
            for created, used, total, percentage in samples
        )
        totals = [
            gb_to_bytes(total)
            for _, _, total, _ in normalized
            if total is not None and total > 0
        ]
        maximum = max(totals, default=None)
        if maximum is not None:
            points: list[tuple[datetime, float]] = []
            for created, used, total, percentage in normalized:
                sample_total = (
                    gb_to_bytes(total) if total is not None and total > 0 else maximum
                )
                value: float | None = None
                if used is not None:
                    value = gb_to_bytes(used)
                elif percentage is not None:
                    value = percentage / 100.0 * sample_total
                if value is not None:
                    points.append((created, min(max(0.0, value), sample_total)))
            return _CapacityHistory(
                points=tuple(points),
                unit=ChartUnit.BYTES,
                maximum=maximum,
            )

        if allow_used_without_total:
            used_points = tuple(
                (created, gb_to_bytes(used))
                for created, used, _, _ in normalized
                if used is not None
            )
            if used_points:
                return _CapacityHistory(
                    points=used_points,
                    unit=ChartUnit.BYTES,
                    maximum=None,
                )

        return _CapacityHistory(
            points=tuple(
                (created, percentage)
                for created, _, _, percentage in normalized
                if percentage is not None
            ),
            unit=ChartUnit.PERCENT,
            maximum=None,
        )

    @staticmethod
    def _usage_percent(
        source: dict[str, Any],
        percent_key: str,
        *,
        used_key: str,
        total_key: str,
    ) -> float | None:
        explicit = safe_float(source.get(percent_key))
        if explicit is not None:
            return explicit
        used = safe_float(source.get(used_key))
        total = safe_float(source.get(total_key))
        if used is not None and total is not None and total > 0:
            return used / total * 100.0
        return None

    @staticmethod
    def _extract_load_avg(value: Any) -> str | None:
        if isinstance(value, (list, tuple)) and value:
            values = [safe_float(item) for item in value]
            finite_values = [item for item in values if item is not None]
            if finite_values:
                return " ".join(f"{item:.2f}" for item in finite_values)
        return None

    @staticmethod
    def _battery_value(value: Any) -> float | None:
        if isinstance(value, (list, tuple)):
            return safe_float(value[0]) if value else None
        return None

    @staticmethod
    def _bandwidth_values(
        value: Any,
    ) -> tuple[float | None, float | None, float | None]:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            tx = safe_float(value[0])
            rx = safe_float(value[1])
            return rx, tx, rx + tx if rx is not None and tx is not None else None
        return None, None, None

    @staticmethod
    def _extract_gpus(value: Any) -> tuple[_GpuMetric, ...]:
        scalar_usage = safe_float(value)
        if scalar_usage is not None:
            return (_GpuMetric("GPU", scalar_usage, None, None, None),)
        if not isinstance(value, dict):
            return ()

        def from_mapping(name: str, data: dict[str, Any]) -> _GpuMetric:
            usage = safe_float(data.get("u"))
            return _GpuMetric(
                name=str(data.get("n") or name),
                usage=usage,
                memory_used_mib=safe_float(data.get("mu")),
                memory_total_mib=safe_float(data.get("mt")),
                power_watts=safe_float(data.get("p")),
            )

        if "u" in value:
            return (from_mapping("GPU", value),)
        result: list[_GpuMetric] = []
        for key in sorted(value, key=str.casefold):
            item = value[key]
            if isinstance(item, dict):
                result.append(from_mapping(str(key), item))
            else:
                usage = safe_float(item)
                if usage is not None:
                    result.append(_GpuMetric(f"GPU {key}", usage, None, None, None))
        return tuple(result)

    @staticmethod
    def _extract_efs_items(info: dict[str, Any]) -> tuple[_DiskMetric, ...]:
        raw = info.get("efs")
        if not isinstance(raw, dict):
            return ()
        result: list[_DiskMetric] = []
        for name in sorted(raw, key=str.casefold):
            data = raw[name]
            scalar = safe_float(data)
            if scalar is not None:
                result.append(_DiskMetric(str(name), scalar, ""))
                continue
            if not isinstance(data, dict):
                continue
            total = safe_float(data.get("d"))
            used = safe_float(data.get("du"))
            secondary = ""
            if used is not None and total is not None and total > 0:
                value = used / total * 100.0
                secondary = f"{gb_iec(used)} / {gb_iec(total)}"
            else:
                value = safe_float(data.get("dp"))
                if used is not None:
                    secondary = gb_iec(used)
            if value is not None:
                result.append(_DiskMetric(str(name), value, secondary))
        return tuple(result)
