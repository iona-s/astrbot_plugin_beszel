"""Map validated Beszel models to immutable presentation documents."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any

from ..beszel.models import (
    SystemDetailView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemSummary,
)
from ..formatters import StatusState, format_clock, format_datetime, status_state
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
    DetailRow,
    DetailSection,
    DocumentFooter,
    DocumentHeader,
    HistoryChartCard,
    HistoryDocument,
    MetadataItem,
    OverviewCard,
    OverviewDocument,
    ProgressMetric,
    StatusBadge,
    StatusDocument,
)
from .styles import metric_color, series_color, threshold_color

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
        plugin_name: str = "Beszel",
        show_connection_address: bool = False,
        display_timezone: tzinfo | None = None,
        gap_factor: float = 1.5,
    ) -> None:
        self.plugin_name = plugin_name
        self.show_connection_address = show_connection_address
        self.display_timezone = display_timezone or UTC
        self.gap_factor = max(1.0, float(gap_factor))

    def build_overview(
        self,
        systems: Iterable[SystemSummary],
        *,
        page_number: int = 1,
        page_count: int = 1,
        summary_systems: Iterable[SystemSummary] | None = None,
        summary_counts: tuple[int, int] | None = None,
    ) -> OverviewDocument:
        systems_tuple = tuple(systems)
        summary_tuple = (
            systems_tuple if summary_systems is None else tuple(summary_systems)
        )
        page_number = max(1, page_number)
        page_count = max(page_number, page_count)
        if summary_counts is None:
            online_count = sum(
                status_state(item.status) == "up" for item in summary_tuple
            )
            offline_count = sum(
                status_state(item.status) == "down" for item in summary_tuple
            )
        else:
            online_count, offline_count = summary_counts
        header = DocumentHeader(
            title="探针概览",
            subtitle=f"共 {len(summary_tuple)} 个探针节点",
            metadata=(
                MetadataItem("在线", str(online_count)),
                MetadataItem("离线", str(offline_count)),
            ),
        )
        return OverviewDocument(
            header=header,
            cards=tuple(self._overview_card(item) for item in systems_tuple),
            online_count=online_count,
            offline_count=offline_count,
            page_number=page_number,
            page_count=page_count,
            footer=DocumentFooter(
                f"{self.plugin_name} · 第 {page_number}/{page_count} 页"
            ),
        )

    def build_status(self, view: SystemDetailView) -> StatusDocument:
        summary = view.summary
        stats = view.metrics.stats if view.metrics else {}
        status = self._status_badge(summary.status)

        cpu_value = self._first(stats, "cpu", "cpu_percent", "cpus")
        memory_percent = self._usage_percent(
            stats,
            ("mp", "memory_percent"),
            used_key="mu",
            total_key="m",
        )
        memory_used = safe_float(stats.get("mu"))
        memory_total = safe_float(stats.get("m"))
        if memory_total is None and view.details and view.details.memory:
            memory_total = view.details.memory / (1024**3)
        memory_secondary = self._capacity_text(memory_used, memory_total)

        disk_percent = self._usage_percent(
            stats,
            ("dp", "disk_percent"),
            used_key="du",
            total_key="d",
        )
        disk_used = safe_float(stats.get("du"))
        disk_total = safe_float(stats.get("d"))

        bandwidth = self._first(stats, "b", "bandwidth")
        rx_speed, tx_speed, total_bandwidth = self._bandwidth_values(bandwidth)
        load_average = self._extract_load_avg(self._first(stats, "la"))
        if load_average is None:
            load_average = self._extract_load_avg(self._first(summary.info, "la"))

        metric_cards = (
            ProgressMetric(
                label="CPU 使用率",
                value=safe_float(cpu_value),
                value_text=percent(cpu_value),
                secondary_text=f"负载: {load_average}" if load_average else "",
                metric_key="cpu",
                color=threshold_color(safe_float(cpu_value), summary.status),
            ),
            ProgressMetric(
                label="内存使用率",
                value=memory_percent,
                value_text=percent(memory_percent),
                secondary_text=memory_secondary,
                metric_key="mem",
                color=threshold_color(memory_percent, summary.status),
            ),
            ProgressMetric(
                label="根磁盘 ( / )",
                value=disk_percent,
                value_text=percent(disk_percent),
                secondary_text=self._capacity_text(disk_used, disk_total),
                metric_key="disk",
                color=threshold_color(disk_percent, summary.status),
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
                metric_key="net",
                color=metric_color("net"),
                maximum=0.0,
            ),
        )

        sections = self._status_sections(view, stats)
        header_metadata = [MetadataItem("探针 ID", summary.id)]
        if view.details and view.details.os:
            header_metadata.append(MetadataItem("系统", view.details.os))
        if view.details and view.details.arch:
            header_metadata.append(MetadataItem("架构", view.details.arch))
        return StatusDocument(
            header=DocumentHeader(
                title=summary.name,
                subtitle=f"探针 ID: {summary.id}",
                status=status,
                metadata=tuple(header_metadata),
            ),
            metric_cards=metric_cards,
            sections=sections,
            footer=DocumentFooter(f"{self.plugin_name} · {summary.name}"),
        )

    def build_history(self, view: SystemHistoryView) -> HistoryDocument:
        points = tuple(sorted(view.points, key=lambda point: point.created))
        first_time = points[0].created if points else None
        last_time = points[-1].created if points else None
        time_span = ""
        if first_time is not None and last_time is not None:
            time_span = f"{format_clock(first_time, self.display_timezone)}"
            time_span += f" ~ {format_clock(last_time, self.display_timezone)}"

        summary = view.summary
        metadata = []
        if self.show_connection_address and summary.host:
            metadata.append(
                MetadataItem("连接地址", self._address(summary.host, summary.port))
            )
        info = summary.info or {}
        host_name = self._first(info, "h", "hostname")
        if host_name:
            metadata.append(MetadataItem("主机名", str(host_name)))
        if info.get("u") is not None:
            metadata.append(MetadataItem("运行时间", uptime_cn(info["u"])))
        if info.get("os"):
            metadata.append(MetadataItem("系统", str(info["os"])))
        if info.get("cpu"):
            metadata.append(MetadataItem("处理器", str(info["cpu"])))
        metadata.append(MetadataItem("采样点", str(len(points))))

        range_value = view.range.value
        range_label = HISTORY_RANGE_LABELS.get(range_value, range_value)
        conn_addr = (
            self._address(summary.host, summary.port)
            if (self.show_connection_address and summary.host)
            else ""
        )
        details = view.details
        uptime_str = uptime_cn(info.get("u")) if info.get("u") is not None else ""
        os_str = (
            details.os
            if (details and details.os)
            else str(info.get("os", ""))
            if isinstance(info.get("os"), str)
            else ""
        )
        cpu_str = (
            details.cpu
            if (details and details.cpu)
            else str(info.get("cpu", ""))
            if (
                isinstance(info.get("cpu"), str)
                # Beszel sometimes exposes the aggregate CPU percentage in
                # ``info.cpu``; numeric-only values are not model names.
                and not str(info.get("cpu", "")).replace(".", "").isdigit()
            )
            else ""
        )
        mem_val = info.get("m")
        memory_str = (
            gb_iec(details.memory / (1024**3))
            if (details and details.memory is not None)
            else gb_iec(mem_val)
            if (mem_val is not None and isinstance(mem_val, (int, float)))
            else ""
        )

        header = DocumentHeader(
            title=summary.name,
            subtitle=f"跨度: {range_label} ({time_span})",
            status=self._status_badge(summary.status),
            metadata=tuple(metadata),
            range_label=range_label,
            connection_address=conn_addr,
            uptime_text=uptime_str,
            os_text=os_str,
            cpu_text=cpu_str,
            memory_text=memory_str,
        )
        cards = tuple(
            self._history_cards(
                points,
                gap_seconds=view.range.expected_interval.total_seconds()
                * self.gap_factor,
            )
        )
        return HistoryDocument(
            header=header,
            cards=cards,
            sample_count=len(points),
            has_gaps=view.has_gaps,
            footer=DocumentFooter(f"{self.plugin_name} · {summary.name}"),
        )

    def _overview_card(self, system: SystemSummary) -> OverviewCard:
        info = system.info or {}
        state = status_state(system.status)
        metadata: list[MetadataItem] = []
        if state == "up":
            uptime = self._first(info, "u", "uptime", "up")
            uptime_text = uptime_cn(uptime) if uptime is not None else "N/A"
            if uptime_text != "N/A":
                metadata.append(MetadataItem("运行", uptime_text))
            temperature = self._extract_temp(info)
            if temperature is not None:
                metadata.append(MetadataItem("温度", f"{temperature:.1f}°C"))
            bandwidth = format_bandwidth(
                self._first(info, "bb", "b", "bw", "bandwidth", "net")
            )
            if bandwidth != "N/A":
                metadata.append(MetadataItem("网络", bandwidth))
            load_average = self._extract_load_avg(self._first(info, "la"))
            if load_average:
                metadata.append(MetadataItem("负载", load_average))
            battery = self._battery_value(self._first(info, "bat", "battery"))
            if battery is not None:
                metadata.append(MetadataItem("电量", f"{int(battery)}%"))
            services = self._first(info, "sv", "s", "services")
            if services is not None and not isinstance(services, bool):
                metadata.append(MetadataItem("服务", self._services_text(services)))
        else:
            metadata.append(MetadataItem("网络", "N/A"))

        metrics: list[ProgressMetric] = []
        metrics.append(
            self._progress_metric(
                "CPU",
                self._first(info, "cpu", "cpu_percent", "cpus"),
                "cpu",
                status=system.status,
            )
        )
        memory_percent = self._usage_percent(
            info,
            ("mp", "memory_percent"),
            used_key="mu",
            total_key="m",
        )
        metrics.append(
            self._progress_metric(
                "内存",
                memory_percent,
                "mem",
                self._capacity_text(
                    safe_float(info.get("mu")), safe_float(info.get("m"))
                ),
                status=system.status,
            )
        )
        metrics.extend(
            self._progress_metric(
                f"显卡 ({gpu.name})" if gpu.name != "GPU" else "GPU",
                gpu.usage,
                "gpu",
                status=system.status,
            )
            for gpu in self._extract_gpus(self._first(info, "g", "gpu"))
        )
        disk_percent = self._usage_percent(
            info,
            ("dp", "disk_percent"),
            used_key="du",
            total_key="d",
        )
        metrics.append(
            self._progress_metric(
                "根磁盘 ( / )",
                disk_percent,
                "disk",
                self._capacity_text(
                    safe_float(info.get("du")), safe_float(info.get("d"))
                ),
                status=system.status,
            )
        )
        metrics.extend(
            self._progress_metric(
                f"磁盘 ({disk.name})",
                disk.percent_used,
                "disk",
                disk.secondary_text,
                status=system.status,
            )
            for disk in self._extract_efs_items(info)
        )

        return OverviewCard(
            name=system.name,
            status=self._status_badge(system.status),
            updated_text=(
                format_datetime(system.updated, self.display_timezone, seconds=True)
                if system.updated
                else ""
            ),
            metadata=tuple(metadata),
            metrics=tuple(metrics),
            agent_version=(f"v{str(info['v']).lstrip('v')}" if info.get("v") else None),
        )

    def _status_sections(
        self, view: SystemDetailView, stats: dict[str, Any]
    ) -> tuple[DetailSection, ...]:
        summary = view.summary
        gpus = self._extract_gpus(self._first(stats, "g", "gpu"))
        if not gpus:
            gpus = self._extract_gpus(self._first(summary.info, "g", "gpu"))

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
            ("dp", "disk_percent"),
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
        if len(all_disks) > 1:
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
                        )
                        for disk in all_disks
                    ),
                )
            )

        sensor_rows = self._sensor_rows(stats, summary)
        if sensor_rows:
            sections.append(DetailSection("硬件传感器与环境监控", tuple(sensor_rows)))

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

    def _sensor_rows(
        self, stats: dict[str, Any], summary: SystemSummary
    ) -> list[DetailRow]:
        rows: list[DetailRow] = []
        gpu_names = {
            gpu.name.casefold()
            for gpu in self._extract_gpus(self._first(stats, "g", "gpu"))
        }
        raw_temps = stats.get("t")
        if isinstance(raw_temps, dict):
            for name, raw_value in sorted(raw_temps.items()):
                value = safe_float(raw_value)
                folded_name = str(name).casefold()
                if value is None:
                    continue
                # Exact GPU-name matches are common; keep this O(1) fast path
                # before scanning for sensor names that contain a GPU name.
                if folded_name in gpu_names:
                    continue
                if any(gpu_name in folded_name for gpu_name in gpu_names):
                    continue
                rows.append(
                    DetailRow(
                        label=f"温度 ({name})",
                        value=f"{value:.1f} °C",
                    )
                )
        elif raw_temps is None:
            value = self._extract_temp(stats)
            if value is None:
                value = self._extract_temp(summary.info)
            if value is not None:
                rows.append(
                    DetailRow(
                        label="设备主温度",
                        value=f"{value:.1f} °C",
                    )
                )

        raw_fans = stats.get("f")
        if isinstance(raw_fans, dict):
            for name, raw_value in sorted(raw_fans.items()):
                value = safe_float(raw_value)
                if value is not None and value > 0:
                    rows.append(
                        DetailRow(
                            label=f"风扇 ({name})",
                            value=f"{int(value)} RPM",
                        )
                    )

        battery = self._battery_value(self._first(stats, "bat", "battery"))
        if battery is not None:
            rows.append(
                DetailRow(
                    label="电池电量",
                    value=f"{int(battery)}%",
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
        self, points: tuple[SystemHistoryPoint, ...], *, gap_seconds: float
    ) -> list[HistoryChartCard]:
        valid_points = [
            (point.created, point.stats)
            for point in points
            if point.created is not None and isinstance(point.stats, dict)
        ]
        cards: list[HistoryChartCard] = []

        cpu_points = self._metric_points(valid_points, "cpu", "cpu_percent", "cpus")
        self._append_chart(
            cards,
            "CPU 使用率",
            "系统范围内的平均 CPU 使用率",
            ChartUnit.PERCENT,
            [("使用率", cpu_points, "cpu")],
            gap_seconds,
        )

        mu_history = self._capacity_history(
            (
                (
                    created,
                    stats.get("mu"),
                    stats.get("m"),
                    self._first(stats, "mp", "memory_percent"),
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

        disk_history = self._capacity_history(
            (
                (
                    created,
                    stats.get("du"),
                    stats.get("d"),
                    self._first(stats, "dp", "disk_percent"),
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
                read_value = mib_rate_to_bytes(
                    safe_float(self._first(stats, "dr", "r")) or 0.0
                )
                write_value = mib_rate_to_bytes(
                    safe_float(self._first(stats, "dw", "w")) or 0.0
                )
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
            bandwidth = self._first(stats, "b", "bandwidth")
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

        load_one: list[tuple[datetime, float]] = []
        load_five: list[tuple[datetime, float]] = []
        load_fifteen: list[tuple[datetime, float]] = []
        for created, stats in valid_points:
            raw_load = self._first(stats, "la", "load")
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
            self._append_chart(
                cards,
                "温度",
                "系统传感器的温度",
                ChartUnit.TEMPERATURE,
                [(name, values, "temp") for name, values in top_temperatures],
                gap_seconds,
                extra_series_count=extra_count,
            )

        gpu_samples: dict[str, list[tuple[datetime, _GpuMetric]]] = {}
        for created, stats in valid_points:
            for gpu in self._extract_gpus(self._first(stats, "g", "gpu")):
                gpu_samples.setdefault(gpu.name, []).append((created, gpu))
        gpu_names = sorted(gpu_samples, key=str.casefold)
        for gpu_name in gpu_names:
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
                    [("功耗", power, "gpu")],
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
            self._append_chart(
                cards,
                "风扇",
                "系统风扇转速 (RPM)",
                ChartUnit.RPM,
                [(name, values, "fan") for name, values in ordered_fans],
                gap_seconds,
            )

        battery_values = [
            (created, value)
            for created, stats in valid_points
            if (value := self._battery_value(stats.get("bat"))) is not None
        ]
        self._append_chart(
            cards,
            "电池电量",
            "系统电池剩余电量",
            ChartUnit.PERCENT,
            [("电量", battery_values, "battery")],
            gap_seconds,
        )

        swap_samples: list[tuple[datetime, Any, Any, Any]] = []
        for created, stats in valid_points:
            used = self._extract_metric_float(stats.get("su"))
            if used is None:
                used = self._extract_metric_float(stats.get("swap"))
            swap_samples.append(
                (
                    created,
                    used,
                    stats.get("s"),
                    self._first(stats, "sp", "swap_percent"),
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
        return cards

    def _append_chart(
        self,
        cards: list[HistoryChartCard],
        title: str,
        subtitle: str,
        unit: ChartUnit,
        series_specs: list[tuple[str, list[tuple[datetime, float]], str]],
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
            series.append(
                ChartSeries(
                    name=name,
                    unit=unit,
                    color=series_color(index, metric_key=metric_key),
                    points=normalized_points,
                    segments=segments,
                    current_value=values[-1],
                    minimum=min(values),
                    maximum=max(values),
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
                maximum_override=maximum_override,
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
    def _first(mapping: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None

    @staticmethod
    def _capacity_text(used: float | None, total: float | None) -> str:
        if used is not None and total is not None and total > 0:
            return f"{gb_iec(used)} / {gb_iec(total)}"
        return gb_iec(used) if used is not None else ""

    @staticmethod
    def _address(host: str, port: int | None) -> str:
        return f"{host}:{port}" if port is not None else host

    @staticmethod
    def _services_text(value: Any) -> str:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return f"{value[0]} 运行 (失败: {value[1]})"
        if isinstance(value, (list, tuple)) and value:
            return f"{value[0]} 运行"
        return f"{value} 运行"

    @staticmethod
    def _metric_points(
        points: list[tuple[datetime, dict[str, Any]]], *keys: str
    ) -> list[tuple[datetime, float]]:
        result: list[tuple[datetime, float]] = []
        for created, stats in points:
            value = PresentationBuilder._extract_metric_float(
                PresentationBuilder._first(stats, *keys)
            )
            if value is not None:
                result.append((created, value))
        return result

    @staticmethod
    def _extract_metric_float(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value) if math.isfinite(value) else None
        if isinstance(value, (list, tuple)) and value:
            numeric_values = [
                item for raw in value if (item := safe_float(raw)) is not None
            ]
            if numeric_values:
                return float(sum(numeric_values))
        if isinstance(value, dict) and value:
            numeric_values = [
                item for raw in value.values() if (item := safe_float(raw)) is not None
            ]
            if numeric_values:
                return float(sum(numeric_values) / len(numeric_values))
        return None

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
                PresentationBuilder._extract_metric_float(used),
                PresentationBuilder._extract_metric_float(total),
                PresentationBuilder._extract_metric_float(percentage),
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
        percent_keys: tuple[str, ...],
        *,
        used_key: str,
        total_key: str,
    ) -> float | None:
        explicit = safe_float(PresentationBuilder._first(source, *percent_keys))
        if explicit is not None:
            return explicit
        used = safe_float(source.get(used_key))
        total = safe_float(source.get(total_key))
        if used is not None and total is not None and total > 0:
            return used / total * 100.0
        return None

    @staticmethod
    def _extract_temp(info: dict[str, Any]) -> float | None:
        direct = safe_float(info.get("dt"))
        if direct is not None:
            return direct
        direct = safe_float(info.get("temp"))
        if direct is not None:
            return direct
        direct = safe_float(info.get("temperature"))
        if direct is not None:
            return direct
        raw_values = info.get("t")
        if isinstance(raw_values, dict):
            values = [safe_float(value) for value in raw_values.values()]
            finite_values = [value for value in values if value is not None]
            return max(finite_values) if finite_values else None
        if isinstance(raw_values, (list, tuple)):
            values = [safe_float(value) for value in raw_values]
            finite_values = [value for value in values if value is not None]
            return max(finite_values) if finite_values else None
        return safe_float(raw_values)

    @staticmethod
    def _extract_load_avg(value: Any) -> str | None:
        if isinstance(value, (list, tuple)) and value:
            values = [safe_float(item) for item in value]
            finite_values = [item for item in values if item is not None]
            if finite_values:
                return " / ".join(f"{item:.2f}" for item in finite_values)
        return None

    @staticmethod
    def _battery_value(value: Any) -> float | None:
        if isinstance(value, (list, tuple)):
            return safe_float(value[0]) if value else None
        return safe_float(value)

    @staticmethod
    def _bandwidth_values(
        value: Any,
    ) -> tuple[float | None, float | None, float | None]:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            tx = safe_float(value[0])
            rx = safe_float(value[1])
            return rx, tx, rx + tx if rx is not None and tx is not None else None
        scalar = safe_float(value)
        return None, None, scalar

    @staticmethod
    def _extract_gpus(value: Any) -> tuple[_GpuMetric, ...]:
        scalar_usage = safe_float(value)
        if scalar_usage is not None:
            return (_GpuMetric("GPU", scalar_usage, None, None, None),)
        if not isinstance(value, dict):
            return ()

        def from_mapping(name: str, data: dict[str, Any]) -> _GpuMetric:
            usage = safe_float(data.get("u"))
            if usage is None:
                usage = safe_float(data.get("usage"))
            return _GpuMetric(
                name=str(data.get("n") or data.get("name") or name),
                usage=usage,
                memory_used_mib=safe_float(data.get("mu")),
                memory_total_mib=safe_float(data.get("mt")),
                power_watts=safe_float(data.get("p")),
            )

        if "u" in value or "usage" in value:
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
        raw = PresentationBuilder._first(info, "efs", "extra_filesystems", "disks")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                return ()
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

    @staticmethod
    def _progress_metric(
        label: str,
        value: Any,
        metric_key: str,
        secondary_text: str = "",
        status: str = "up",
    ) -> ProgressMetric:
        numeric = safe_float(value)
        return ProgressMetric(
            label=label,
            value=numeric,
            value_text=percent(numeric),
            secondary_text=secondary_text,
            metric_key=metric_key,
            color=threshold_color(numeric, status),
        )
