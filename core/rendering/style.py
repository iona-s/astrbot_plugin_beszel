"""Beszel light-theme rendering tokens."""

from __future__ import annotations

from dataclasses import dataclass

from ..formatters import status_state


@dataclass(frozen=True, slots=True)
class BeszelStyle:
    width: int = 1200
    background: tuple[int, int, int] = (248, 250, 252)  # #f8fafc slate-50
    foreground: tuple[int, int, int] = (15, 23, 42)  # #0f172a slate-900
    text_secondary: tuple[int, int, int] = (30, 41, 59)  # #1e293b slate-800
    muted: tuple[int, int, int] = (71, 85, 105)  # #475569 slate-600
    subtle: tuple[int, int, int] = (100, 116, 139)  # #64748b slate-500
    card: tuple[int, int, int] = (255, 255, 255)  # #ffffff
    card_secondary: tuple[int, int, int] = (241, 245, 249)  # #f1f5f9 slate-100
    border: tuple[int, int, int] = (218, 225, 233)  # #dae1e9 slate-200+
    border_subtle: tuple[int, int, int] = (230, 236, 242)  # #e6ecf2
    track: tuple[int, int, int] = (230, 236, 242)  # #e6ecf2
    grid: tuple[int, int, int] = (226, 232, 240)  # #e2e8f0 slate-200 gridlines

    chart_cpu: tuple[int, int, int] = (59, 130, 246)  # #3b82f6 blue-500
    chart_mem: tuple[int, int, int] = (16, 185, 129)  # #10b981 emerald-500
    chart_disk: tuple[int, int, int] = (245, 158, 11)  # #f59e0b amber-500
    chart_net: tuple[int, int, int] = (139, 92, 246)  # #8b5cf6 purple-500
    chart_temp: tuple[int, int, int] = (244, 63, 94)  # #f43f5e rose-500
    chart_gpu: tuple[int, int, int] = (6, 182, 212)  # #06b6d4 cyan-500

    chart: tuple[tuple[int, int, int], ...] = (
        (59, 130, 246),
        (16, 185, 129),
        (245, 158, 11),
        (139, 92, 246),
        (244, 63, 94),
        (6, 182, 212),
    )

    up: tuple[int, int, int] = (34, 197, 94)  # #22c55e emerald-500
    up_halo: tuple[int, int, int] = (220, 252, 231)  # #dcfce7 emerald-100
    down: tuple[int, int, int] = (239, 68, 68)  # #ef4444 red-500
    down_halo: tuple[int, int, int] = (254, 226, 226)  # #fee2e2 red-100
    unknown: tuple[int, int, int] = (245, 158, 11)  # #f59e0b amber-500
    unknown_halo: tuple[int, int, int] = (254, 243, 199)  # #fef3c7 amber-100

    def status_color(self, status: str) -> tuple[int, int, int]:
        return {"up": self.up, "down": self.down, "unknown": self.unknown}[
            status_state(status)
        ]

    def status_halo(self, status: str) -> tuple[int, int, int]:
        return {
            "up": self.up_halo,
            "down": self.down_halo,
            "unknown": self.unknown_halo,
        }[status_state(status)]

    def color_for_metric(
        self, name: str, default_index: int = 0
    ) -> tuple[int, int, int]:
        normalized = name.casefold()
        if "cpu" in normalized:
            return self.chart_cpu
        if "内存" in normalized or "mem" in normalized:
            return self.chart_mem
        if "磁盘" in normalized or "disk" in normalized:
            return self.chart_disk
        if "网" in normalized or "net" in normalized or "bandwidth" in normalized:
            return self.chart_net
        if "温" in normalized or "temp" in normalized:
            return self.chart_temp
        if "gpu" in normalized:
            return self.chart_gpu
        return self.chart[default_index % len(self.chart)]
