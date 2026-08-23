"""Pillow renderer with modern Beszel 0.18.8 UI design, progress bars, and area-fill charts."""

from __future__ import annotations

import asyncio
import colorsys
import io
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from astrbot.api import logger
from PIL import Image, ImageDraw, ImageFont

from ..beszel.models import SystemDetailView, SystemHistoryView, SystemSummary
from ..formatters import resolve_timezone, status_state
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
from .style import BeszelStyle


class BeszelRenderer:
    """Render internal models to in-memory PNG bytes matching Beszel 0.18.8 visual style."""

    _bundled_font_path = (
        Path(__file__).resolve().parent.parent
        / "assets"
        / "fonts"
        / "NotoSansSC-Regular.otf"
    )

    def __init__(
        self,
        style: BeszelStyle | None = None,
        *,
        plugin_name: str = "Beszel",
        show_connection_address: bool = False,
        display_timezone: tzinfo | None = None,
        font_path: str | Path | None = None,
    ) -> None:
        self.style = style or BeszelStyle()
        self.plugin_name = plugin_name
        self.show_connection_address = show_connection_address
        self.display_timezone = display_timezone or resolve_timezone("")
        self._font_warning_logged = False
        self._font_path = self._select_font_path(font_path)
        self._font_cache: dict[
            tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont
        ] = {}

    async def render_overview(
        self, systems: list[SystemSummary], page_size: int = 10
    ) -> list[bytes]:
        """Render one page per ``page_size`` systems; empty input yields no pages."""
        pages = [
            systems[index : index + page_size]
            for index in range(0, len(systems), page_size)
        ]
        return await asyncio.to_thread(self._render_pages, pages)

    async def render_status(self, view: SystemDetailView) -> bytes:
        return await asyncio.to_thread(self._render_status, view)

    async def render_history(self, view: SystemHistoryView) -> bytes:
        return await asyncio.to_thread(self._render_history, view)

    def _select_font_path(self, configured_path: str | Path | None) -> Path | None:
        if configured_path and str(configured_path).strip():
            custom_path = Path(str(configured_path).strip()).expanduser()
            if self._font_file_loads(custom_path):
                return custom_path
            logger.warning(
                "Configured render.font_path could not be loaded; using bundled font"
            )

        if self._font_file_loads(self._bundled_font_path):
            return self._bundled_font_path

        logger.warning(
            "Bundled font could not be loaded; image text may contain missing glyphs"
        )
        self._font_warning_logged = True
        return None

    @staticmethod
    def _font_file_loads(path: str | Path) -> bool:
        try:
            ImageFont.truetype(path, 12)
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _hsl_color(
        hue: float, saturation: float, lightness: float
    ) -> tuple[int, int, int]:
        red, green, blue = colorsys.hls_to_rgb(
            (hue % 360.0) / 360.0,
            lightness / 100.0,
            saturation / 100.0,
        )
        return tuple(round(channel * 255) for channel in (red, green, blue))

    @classmethod
    def _temperature_series(
        cls,
        points_by_sensor: dict[str, list[tuple[datetime, float]]],
    ) -> list[HistoryChartSeries]:
        sorted_sensors = sorted(
            points_by_sensor.items(),
            key=lambda item: -sum(value for _, value in item[1]),
        )
        sensor_count = len(sorted_sensors)
        return [
            HistoryChartSeries(
                name=name,
                points=points,
                color=cls._hsl_color(
                    index * 360.0 / sensor_count,
                    60.0,
                    55.0,
                ),
            )
            for index, (name, points) in enumerate(sorted_sensors)
        ]

    def _font(
        self, size: int, *, bold: bool = False
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        cache_key = (size, bold)
        if cached := self._font_cache.get(cache_key):
            return cached

        if self._font_path:
            try:
                font = ImageFont.truetype(self._font_path, size)
                self._font_cache[cache_key] = font
                return font
            except (OSError, ValueError):
                pass

        if not self._font_warning_logged:
            logger.warning(
                "Selected font became unavailable; image text may contain missing glyphs"
            )
            self._font_warning_logged = True
        font = ImageFont.load_default()
        self._font_cache[cache_key] = font
        return font

    def _canvas(self, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        image = Image.new(
            "RGB", (self.style.width, max(240, height)), self.style.background
        )
        return image, ImageDraw.Draw(image)

    def _save(self, image: Image.Image) -> bytes:
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    @staticmethod
    def _draw_rounded_rect(
        base_img: Image.Image,
        xy: tuple[int, int, int, int],
        radius: int,
        fill: tuple[int, int, int] | tuple[int, int, int, int] | None,
        outline: tuple[int, int, int] | None = None,
        width: int = 1,
    ) -> None:
        left, top, right, bottom = int(xy[0]), int(xy[1]), int(xy[2]), int(xy[3])
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            return
        r = min(radius, w // 2, h // 2)
        if r <= 0:
            draw = ImageDraw.Draw(base_img)
            if fill:
                draw.rectangle((left, top, right - 1, bottom - 1), fill=fill)
            if outline:
                draw.rectangle(
                    (left, top, right - 1, bottom - 1),
                    outline=outline,
                    width=width,
                )
            return

        scale = 3
        sw, sh = w * scale, h * scale
        sr = r * scale
        swidth = max(1, width * scale)

        shape = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shape)
        if fill:
            sdraw.rounded_rectangle((0, 0, sw - 1, sh - 1), radius=sr, fill=fill)
        if outline:
            sdraw.rounded_rectangle(
                (0, 0, sw - 1, sh - 1),
                radius=sr,
                fill=None,
                outline=outline,
                width=swidth,
            )

        shape = shape.resize((w, h), Image.Resampling.BILINEAR)
        base_img.paste(shape, (left, top), shape)

    def _card(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int, int, int],
        *,
        radius: int = 12,
        fill: tuple[int, int, int] | None = None,
        outline: tuple[int, int, int] | None = None,
    ) -> None:
        self._draw_rounded_rect(
            draw._image,
            xy,
            radius=radius,
            fill=fill or self.style.card,
            outline=outline or self.style.border,
            width=1,
        )

    def _badge(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int],
        text: str,
        *,
        bg: tuple[int, int, int] | None = None,
        fg: tuple[int, int, int] | None = None,
        border: tuple[int, int, int] | None = None,
        font_size: int = 13,
        bold: bool = False,
    ) -> int:
        font = self._font(font_size, bold=bold)
        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        pad_x, pad_y = 10, 4
        left, top = xy
        right = int(left + text_w + pad_x * 2)
        bottom = int(top + font_size + pad_y * 2 + 2)
        badge_h = bottom - top
        self._draw_rounded_rect(
            draw._image,
            (left, top, right, bottom),
            radius=badge_h // 2,
            fill=bg or self.style.card_secondary,
            outline=border or self.style.border,
            width=1,
        )
        mid_x = (left + right) / 2.0
        mid_y = (top + bottom) / 2.0
        txt_x = mid_x - (bbox[0] + bbox[2]) / 2.0
        txt_y = mid_y - (bbox[1] + bbox[3]) / 2.0
        draw.text(
            (txt_x, txt_y),
            text,
            fill=fg or self.style.muted,
            font=font,
        )
        return right

    def _status_pill(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int],
        status: str,
        text_override: str | None = None,
    ) -> int:
        status_color = self.style.status_color(status)
        halo_color = self.style.status_halo(status)
        text = text_override or status.upper()
        font = self._font(13, bold=True)
        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        left, top = xy
        pad_x, pad_y = 10, 4
        right = int(left + text_w + pad_x * 2 + 14)
        bottom = int(top + 13 + pad_y * 2 + 2)
        pill_h = bottom - top
        self._draw_rounded_rect(
            draw._image,
            (left, top, right, bottom),
            radius=pill_h // 2,
            fill=halo_color,
            outline=status_color,
            width=1,
        )
        mid_y = (top + bottom) / 2.0
        draw.ellipse(
            (left + pad_x, int(mid_y - 4), left + pad_x + 8, int(mid_y + 4)),
            fill=status_color,
        )
        txt_y = mid_y - (bbox[1] + bbox[3]) / 2.0
        draw.text(
            (left + pad_x + 14, txt_y),
            text,
            fill=status_color,
            font=font,
        )
        return right

    def _progress_bar(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int, int, int],
        value: float | None,
        color: tuple[int, int, int],
        *,
        radius: int = 4,
    ) -> None:
        left, top, right, bottom = xy
        bar_h = bottom - top
        r = bar_h // 2
        self._draw_rounded_rect(
            draw._image,
            (left, top, right, bottom),
            radius=r,
            fill=self.style.track,
        )
        if value is None or value <= 0:
            return
        clamped = min(100.0, max(0.0, float(value)))
        bar_width = max(bar_h, int((right - left) * (clamped / 100.0)))
        self._draw_rounded_rect(
            draw._image,
            (left, top, min(right, left + bar_width), bottom),
            radius=r,
            fill=color,
        )

    def _header(
        self,
        draw: ImageDraw.ImageDraw,
        title: str,
        subtitle: str | None = None,
        *,
        tags: (
            list[tuple[str, tuple[int, int, int], tuple[int, int, int]]] | None
        ) = None,
    ) -> int:
        left = 48
        top = 36
        badge_w, badge_h = 68, 24
        brand_font = self._font(12, bold=True)
        self._draw_rounded_rect(
            draw._image,
            (left, top, left + badge_w, top + badge_h),
            radius=6,
            fill=self.style.chart_cpu,
        )
        b_bbox = brand_font.getbbox("BESZEL")
        b_x = left + (badge_w - (b_bbox[0] + b_bbox[2])) / 2.0
        b_y = top + (badge_h - (b_bbox[1] + b_bbox[3])) / 2.0
        draw.text((b_x, b_y), "BESZEL", fill=(255, 255, 255), font=brand_font)

        title_font = self._font(26, bold=True)
        t_bbox = title_font.getbbox(title)
        t_y = (top + badge_h / 2.0) - (t_bbox[1] + t_bbox[3]) / 2.0
        draw.text(
            (left + badge_w + 12, t_y),
            title,
            fill=self.style.foreground,
            font=title_font,
        )

        next_x = left
        tag_top = top + 38
        if subtitle:
            draw.text(
                (next_x, tag_top), subtitle, fill=self.style.muted, font=self._font(15)
            )
            next_x += int(draw.textlength(subtitle, font=self._font(15))) + 16

        if tags:
            for text, bg, fg in tags:
                next_x = (
                    self._badge(
                        draw,
                        (next_x, tag_top - 2),
                        text,
                        bg=bg,
                        fg=fg,
                        font_size=12,
                        bold=True,
                    )
                    + 8
                )

        divider_y = top + 68
        draw.line(
            (left, divider_y, self.style.width - left, divider_y),
            fill=self.style.border_subtle,
            width=1,
        )
        return divider_y + 24

    def _footer(
        self, draw: ImageDraw.ImageDraw, y: int, page: str | None = None
    ) -> None:
        stamp = datetime.now(self.display_timezone).strftime("%Y-%m-%d %H:%M:%S")
        text = f"{self.plugin_name} · 查询时间 {stamp}"
        if page:
            text += f" · {page}"
        draw.text((48, y), text, fill=self.style.subtle, font=self._font(13))

    def _dynamic_bar_color(
        self,
        metric_name: str,
        value: float | None,
        is_online: bool,
    ) -> tuple[int, int, int]:
        if not is_online:
            return (148, 163, 184)
        if value is None:
            return (203, 213, 225)
        val = float(value)
        if val >= 90.0:
            return self.style.down
        if val >= 65.0:
            return self.style.unknown
        norm = metric_name.casefold()
        if "cpu" in norm:
            return self.style.chart_cpu
        if "mem" in norm or "内存" in norm:
            return self.style.chart_mem
        if "gpu" in norm:
            return self.style.chart_gpu
        return (14, 165, 233)

    @staticmethod
    def _extract_temp(info: dict[str, Any]) -> float | None:
        dt_val = safe_float(info.get("dt"))
        if dt_val is None:
            dt_val = safe_float(info.get("temp"))
        if dt_val is None:
            dt_val = safe_float(info.get("temperature"))
        if dt_val is not None:
            return dt_val
        t_val = info.get("t")
        if isinstance(t_val, dict) and t_val:
            temps = [val for v in t_val.values() if (val := safe_float(v)) is not None]
            return max(temps) if temps else None
        return None

    def _extract_efs_items(
        self, info: dict[str, Any]
    ) -> list[tuple[str, float | None, str]]:
        efs_dict: dict[str, Any] = {}
        raw_efs = BeszelRenderer._first(info, "efs", "extra_filesystems", "disks")
        if isinstance(raw_efs, str):
            try:
                raw_efs = json.loads(raw_efs)
            except Exception:
                raw_efs = {}
        if isinstance(raw_efs, dict):
            efs_dict = raw_efs

        results: list[tuple[str, float | None, str]] = []
        for disk_name, disk_data in efs_dict.items():
            scalar_pct = safe_float(disk_data)
            if scalar_pct is not None:
                d_pct: float | None = scalar_pct
                d_sub = ""
            elif isinstance(disk_data, dict):
                d_total = safe_float(disk_data.get("d"))
                d_used = safe_float(disk_data.get("du"))
                if d_total is not None and d_used is not None and d_total > 0:
                    d_pct = (d_used / d_total) * 100.0
                    d_sub = f"{gb_iec(d_used)} / {gb_iec(d_total)}"
                else:
                    d_pct = safe_float(disk_data.get("dp"))
                    d_sub = f"{gb_iec(d_used)}" if d_used is not None else ""
            else:
                continue
            results.append((str(disk_name), d_pct, d_sub))
        return results

    @staticmethod
    def _extract_load_avg(la_val: Any) -> str | None:
        if isinstance(la_val, (list, tuple)) and la_val:
            values = [v for x in la_val if (v := safe_float(x)) is not None]
            if values:
                return " / ".join(f"{v:.2f}" for v in values)
        return None

    @staticmethod
    def _extract_gpus(g_val: Any) -> list[dict[str, Any]]:
        scalar_usage = safe_float(g_val)
        if scalar_usage is not None:
            return [
                {
                    "name": "GPU",
                    "usage": scalar_usage,
                    "mem_used": None,
                    "mem_total": None,
                    "power": None,
                }
            ]
        if isinstance(g_val, dict):

            def _gpu_entry(name: str, data: dict[str, Any]) -> dict[str, Any]:
                usage = safe_float(data.get("u"))
                if usage is None:
                    usage = safe_float(data.get("usage"))
                return {
                    "name": name,
                    "usage": usage if usage is not None else 0.0,
                    "mem_used": safe_float(data.get("mu")),
                    "mem_total": safe_float(data.get("mt")),
                    "power": safe_float(data.get("p")),
                }

            if "u" in g_val or "usage" in g_val:
                return [
                    _gpu_entry(str(g_val.get("n") or g_val.get("name") or "GPU"), g_val)
                ]
            gpus = []
            for k, v in g_val.items():
                if isinstance(v, dict):
                    gpus.append(
                        _gpu_entry(str(v.get("n") or v.get("name") or f"GPU {k}"), v)
                    )
                else:
                    usage = safe_float(v)
                    if usage is not None:
                        gpus.append(
                            {
                                "name": f"GPU {k}",
                                "usage": usage,
                                "mem_used": None,
                                "mem_total": None,
                                "power": None,
                            }
                        )
            return gpus
        return []

    def _get_overview_card_height(self, system: SystemSummary) -> int:
        info = system.info
        efs_count = len(self._extract_efs_items(info))
        gpu_count = len(self._extract_gpus(self._first(info, "g", "gpu")))
        total_bars = 3 + gpu_count + efs_count
        num_bar_rows = math.ceil(total_bars / 2)
        has_services = self._first(info, "sv", "s", "services") is not None
        base_h = 98 if has_services else 78
        return base_h + num_bar_rows * 38 + 14

    def _render_pages(self, pages: list[list[SystemSummary]]) -> list[bytes]:
        result: list[bytes] = []
        all_systems = [sys for page in pages for sys in page]
        total_systems = len(all_systems)
        up_count = sum(1 for sys in all_systems if status_state(sys.status) == "up")
        down_count = sum(1 for sys in all_systems if status_state(sys.status) == "down")

        for page_index, systems in enumerate(pages, start=1):
            gap = 16
            rows: list[list[SystemSummary]] = []
            for i in range(0, len(systems), 2):
                rows.append(systems[i : i + 2])

            row_heights = [
                max(self._get_overview_card_height(s) for s in row) for row in rows
            ]
            height = 136 + sum(row_heights) + max(0, len(rows) - 1) * gap + 48
            image, draw = self._canvas(height)

            tags = [
                (f"{up_count} 在线", self.style.up_halo, self.style.up),
                (f"{down_count} 离线", self.style.down_halo, self.style.down),
            ]
            y = self._header(
                draw,
                "探针概览",
                f"共 {total_systems} 个探针节点",
                tags=tags,
            )

            col_width = 556
            col0_left = 36
            col0_right = col0_left + col_width
            col1_left = col0_right + gap
            col1_right = col1_left + col_width

            for row_idx, row in enumerate(rows):
                row_h = row_heights[row_idx]
                self._draw_overview_card(
                    draw, row[0], col0_left, y, col0_right, y + row_h
                )
                if len(row) > 1:
                    self._draw_overview_card(
                        draw, row[1], col1_left, y, col1_right, y + row_h
                    )
                y += row_h + gap

            self._footer(draw, height - 32, f"第 {page_index}/{len(pages)} 页")
            result.append(self._save(image))
        return result

    def _draw_overview_card(
        self,
        draw: ImageDraw.ImageDraw,
        system: SystemSummary,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> None:
        is_online = status_state(system.status) == "up"
        self._card(draw, (left, top, right, bottom), radius=12)

        info = system.info
        header_y = top + 14

        status_right = self._status_pill(draw, (left + 16, header_y), system.status)

        agent_v = info.get("v")
        v_badge_width = 0
        if agent_v:
            v_text = f"v{agent_v}"
            v_font = self._font(12, bold=True)
            v_len = int(draw.textlength(v_text, font=v_font))
            v_badge_width = v_len + 20
            self._badge(
                draw,
                (right - 16 - v_badge_width, header_y),
                v_text,
                font_size=12,
                bold=True,
            )

        name_max_w = right - status_right - 20 - v_badge_width - 16
        name_font = self._font(17, bold=True)
        name = self._ellipsize(draw, system.name, name_font, max(120, name_max_w))
        n_bbox = name_font.getbbox(name)
        pill_mid_y = header_y + 11.5
        n_y = pill_mid_y - (n_bbox[1] + n_bbox[3]) / 2.0
        draw.text(
            (status_right + 10, n_y),
            name,
            fill=self.style.foreground if is_online else self.style.muted,
            font=name_font,
        )

        meta_y = header_y + 28
        meta_font = self._font(12)

        meta_items: list[str] = []
        up_val = self._first(info, "u", "uptime", "up")
        if up_val is not None and is_online:
            up_str = uptime_cn(up_val)
            if up_str != "N/A":
                meta_items.append(up_str)

        temp_val = self._extract_temp(info)
        if temp_val is not None and is_online:
            meta_items.append(f"{temp_val:.1f}°C")

        b_val = self._first(info, "bb", "b", "bw", "bandwidth", "net")
        b_str = format_bandwidth(b_val)
        if is_online and b_str != "N/A":
            meta_items.append(f"网络: {b_str}")

        la_str = self._extract_load_avg(self._first(info, "la"))
        if la_str and is_online:
            meta_items.append(f"负载: {la_str}")

        bat_val = self._first(info, "bat", "battery")
        if bat_val is not None and is_online:
            bat_raw = (
                bat_val[0]
                if isinstance(bat_val, (list, tuple)) and bat_val
                else bat_val
            )
            bat_num = safe_float(bat_raw)
            if bat_num is not None:
                meta_items.append(f"电量: {int(bat_num)}%")

        meta_text = " · ".join(meta_items) if is_online else "网络: N/A"
        date_str = (
            system.updated.astimezone(self.display_timezone).strftime("%m-%d %H:%M:%S")
            if system.updated
            else ""
        )

        date_w = 0
        if date_str:
            upd_text = f"更新: {date_str}"
            date_w = int(draw.textlength(upd_text, font=meta_font))
            draw.text(
                (right - 16 - date_w, meta_y),
                upd_text,
                fill=self.style.subtle,
                font=meta_font,
            )

        max_meta_w = right - left - 32 - (date_w + 16 if date_w else 0)
        meta_disp = self._ellipsize(draw, meta_text, meta_font, max(80, max_meta_w))
        draw.text((left + 16, meta_y), meta_disp, fill=self.style.muted, font=meta_font)

        next_y = meta_y + 18
        sv_val = self._first(info, "sv", "s", "services")
        if sv_val is not None and is_online and not isinstance(sv_val, bool):
            if isinstance(sv_val, (list, tuple)) and len(sv_val) >= 2:
                svc_text = f"服务: {sv_val[0]} 运行 (失败: {sv_val[1]})"
            elif isinstance(sv_val, (list, tuple)) and len(sv_val) >= 1:
                svc_text = f"服务: {sv_val[0]} 运行"
            else:
                svc_text = f"服务: {sv_val} 运行"
            draw.text(
                (left + 16, next_y), svc_text, fill=self.style.muted, font=meta_font
            )
            next_y += 18

        divider_y = next_y + 2
        draw.line(
            (left + 16, divider_y, right - 16, divider_y),
            fill=self.style.border_subtle,
            width=1,
        )

        bars: list[tuple[str, Any, str, str, str]] = []
        cpu_val = self._first(info, "cpu", "cpu_percent", "cpus")
        bars.append(("CPU", cpu_val, percent(cpu_val), "", "cpu"))

        mem_pct = self._usage_percent(
            info, ("mp", "memory_percent"), used_key="mu", total_key="m"
        )
        mem_used = safe_float(info.get("mu"))
        mem_total = safe_float(info.get("m"))
        mem_sub = ""
        if mem_used is not None and mem_total is not None and mem_total > 0:
            mem_sub = f"{gb_iec(mem_used)} / {gb_iec(mem_total)}"
        elif mem_used is not None:
            mem_sub = f"{gb_iec(mem_used)}"
        bars.append(("内存", mem_pct, percent(mem_pct), mem_sub, "mem"))

        for gpu in self._extract_gpus(self._first(info, "g", "gpu")):
            bars.append(
                (
                    f"显卡 ({gpu['name']})" if gpu["name"] != "GPU" else "GPU",
                    gpu["usage"],
                    percent(gpu["usage"]),
                    "",
                    "gpu",
                )
            )

        root_pct = self._usage_percent(
            info, ("dp", "disk_percent"), used_key="du", total_key="d"
        )
        root_used = safe_float(info.get("du"))
        root_total = safe_float(info.get("d"))
        root_sub = ""
        if root_used is not None and root_total is not None and root_total > 0:
            root_sub = f"{gb_iec(root_used)} / {gb_iec(root_total)}"
        elif root_used is not None:
            root_sub = f"{gb_iec(root_used)}"
        bars.append(("根磁盘 (/)", root_pct, percent(root_pct), root_sub, "disk"))

        for disk_name, d_pct, d_sub in self._extract_efs_items(info):
            bars.append((f"磁盘 ({disk_name})", d_pct, percent(d_pct), d_sub, "disk"))

        inner_gap = 14
        half_w = (right - left - 32 - inner_gap) // 2
        col0_bar_left = left + 16
        col0_bar_right = col0_bar_left + half_w
        col1_bar_left = col0_bar_right + inner_gap
        col1_bar_right = right - 16

        bar_y = divider_y + 8
        for i in range(0, len(bars), 2):
            item0 = bars[i]
            self._draw_overview_bar(
                draw,
                col0_bar_left,
                bar_y,
                col0_bar_right,
                item0[0],
                item0[1],
                item0[2],
                item0[3],
                item0[4],
                is_online,
            )
            if i + 1 < len(bars):
                item1 = bars[i + 1]
                self._draw_overview_bar(
                    draw,
                    col1_bar_left,
                    bar_y,
                    col1_bar_right,
                    item1[0],
                    item1[1],
                    item1[2],
                    item1[3],
                    item1[4],
                    is_online,
                )
            bar_y += 38

    def _draw_overview_bar(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        top: int,
        right: int,
        label: str,
        value_raw: Any,
        value_display: str,
        sub_text: str,
        metric_type: str,
        is_online: bool,
    ) -> None:
        label_color = self.style.muted if is_online else self.style.subtle
        label_font = self._font(12)
        lbl_max_w = right - left - 70
        lbl_disp = self._ellipsize(draw, label, label_font, max(60, lbl_max_w))
        draw.text((left, top), lbl_disp, fill=label_color, font=label_font)

        val_font = self._font(12, bold=True)
        val_len = draw.textlength(value_display, font=val_font)
        val_color = self.style.foreground if is_online else self.style.subtle
        draw.text(
            (right - val_len, top),
            value_display,
            fill=val_color,
            font=val_font,
        )

        bar_top = top + 18
        num_val = safe_float(value_raw)
        bar_color = self._dynamic_bar_color(metric_type, num_val, is_online)
        self._progress_bar(
            draw,
            (left, bar_top, right, bar_top + 7),
            num_val,
            bar_color,
            radius=3,
        )

    def _render_status(self, view: SystemDetailView) -> bytes:
        stats = view.metrics.stats if view.metrics else {}
        is_online = status_state(view.summary.status) == "up"

        # 1. KPI Metric values
        cpu_val = self._first(stats, "cpu", "cpu_percent", "cpus")
        mem_pct = self._usage_percent(
            stats, ("mp", "memory_percent"), used_key="mu", total_key="m"
        )
        mem_used = safe_float(stats.get("mu"))
        mem_total = safe_float(stats.get("m"))
        if mem_total is None and view.details and view.details.memory:
            mem_total = view.details.memory / (1024**3)
        mem_buf = safe_float(stats.get("mb"))
        mem_sub_parts = []
        if mem_used is not None and mem_total is not None:
            mem_sub_parts.append(f"已用: {gb_iec(mem_used)} / {gb_iec(mem_total)}")
        elif mem_used is not None:
            mem_sub_parts.append(f"已用: {gb_iec(mem_used)}")
        if mem_buf is not None:
            mem_sub_parts.append(f"缓存: {gb_iec(mem_buf)}")
        mem_sub_text = " · ".join(mem_sub_parts)

        disk_pct = self._usage_percent(
            stats, ("dp", "disk_percent"), used_key="du", total_key="d"
        )
        disk_used = safe_float(stats.get("du"))
        disk_total = safe_float(stats.get("d"))

        b_val = self._first(stats, "b", "bandwidth")
        rx_speed, tx_speed, total_b_speed = None, None, None
        if isinstance(b_val, (list, tuple)) and len(b_val) >= 2:
            tx_speed = safe_float(b_val[0])
            rx_speed = safe_float(b_val[1])
            if tx_speed is not None and rx_speed is not None:
                total_b_speed = rx_speed + tx_speed
        else:
            total_b_speed = safe_float(b_val)

        la_raw = self._first(stats, "la")
        if la_raw is None and view.summary.info:
            la_raw = self._first(view.summary.info, "la")
        la_str = self._extract_load_avg(la_raw)

        # 2. Extract Disks
        all_disks: list[tuple[str, float | None, str]] = []
        if disk_pct is not None or disk_used is not None:
            root_sub = ""
            if disk_used is not None and disk_total is not None and disk_total > 0:
                root_sub = f"{gb_iec(disk_used)} / {gb_iec(disk_total)}"
            all_disks.append(("根磁盘 (/)", disk_pct, root_sub))

        efs_source = (
            stats if stats.get("efs") is not None else (view.summary.info or {})
        )
        for disk_name, d_pct, d_sub in self._extract_efs_items(efs_source):
            all_disks.append((f"磁盘 ({disk_name})", d_pct, d_sub))

        # 3. Extract GPUs & Dedicated GPU Card
        g_raw = self._first(stats, "g")
        if g_raw is None and view.summary.info:
            g_raw = self._first(view.summary.info, "g")
        gpus = self._extract_gpus(g_raw)
        gpu_names_lower = {gpu["name"].casefold() for gpu in gpus}

        # 4. Extract Hardware Sensors (Temperatures, Fans, Battery)
        all_sensors: list[tuple[str, str]] = []
        raw_temps = stats.get("t")
        if isinstance(raw_temps, dict):
            for s_name, s_val in raw_temps.items():
                temp_val = safe_float(s_val)
                if temp_val is not None:
                    s_str = str(s_name).casefold()
                    if s_str in gpu_names_lower or any(
                        gn in s_str for gn in gpu_names_lower
                    ):
                        continue  # Displayed in dedicated GPU card
                    clean_name = (
                        str(s_name).replace("coretemp_", "").replace("_", " ").title()
                    )
                    all_sensors.append((f"温度 ({clean_name})", f"{temp_val:.1f} °C"))
        elif raw_temps is None:
            dt_val = self._extract_temp(stats)
            if dt_val is None:
                dt_val = self._extract_temp(view.summary.info or {})
            if dt_val is not None:
                all_sensors.append(("设备主温度", f"{dt_val:.1f} °C"))

        raw_fans = stats.get("f")
        if isinstance(raw_fans, dict):
            for f_name, f_rpm in raw_fans.items():
                fan_val = safe_float(f_rpm)
                if fan_val is not None and fan_val > 0:
                    clean_f = (
                        str(f_name).replace("nct6793_", "").replace("_", " ").title()
                    )
                    all_sensors.append((f"风扇 ({clean_f})", f"{int(fan_val)} RPM"))

        bat_val = self._first(stats, "bat", "battery")
        if bat_val is not None:
            bat_raw = (
                bat_val[0]
                if isinstance(bat_val, (list, tuple)) and bat_val
                else bat_val
            )
            bat_num = safe_float(bat_raw)
            if bat_num is not None:
                all_sensors.append(("电池电量", f"{int(bat_num)}%"))

        # 5. Extract Network Interfaces
        all_net_ifs: list[tuple[str, str, str]] = []
        raw_ni = stats.get("ni")
        if isinstance(raw_ni, dict):
            for if_name, if_data in raw_ni.items():
                if isinstance(if_data, (list, tuple)) and len(if_data) >= 2:
                    tx_s = format_bandwidth(if_data[0])
                    rx_s = format_bandwidth(if_data[1])
                    rate_str = f"↓ {rx_s} · ↑ {tx_s}"
                    total_str = ""
                    if len(if_data) >= 4:
                        total_tx = bytes_iec(if_data[2])
                        total_rx = bytes_iec(if_data[3])
                        total_str = f"总下行: {total_rx} · 总上行: {total_tx}"
                    all_net_ifs.append((str(if_name), rate_str, total_str))

        # 6. Extract System Info
        sections: list[tuple[str, str]] = []
        if view.details:
            sections.extend(
                (label, str(value) if value is not None else "N/A")
                for label, value in (
                    ("主机名", view.details.hostname),
                    ("操作系统", view.details.os),
                    ("内核版本", view.details.kernel),
                    ("系统架构", view.details.arch),
                    ("处理器型号", view.details.cpu),
                    (
                        "核心 / 线程",
                        f"{view.details.cores or 'N/A'} 核 / {view.details.threads or 'N/A'} 线程",
                    ),
                    ("物理总内存", bytes_iec(view.details.memory)),
                    (
                        "Agent 版本",
                        f"v{view.summary.info.get('v')}"
                        if view.summary.info and view.summary.info.get("v")
                        else "N/A",
                    ),
                    (
                        "系统运行时间",
                        uptime_cn(view.summary.info.get("u"))
                        if view.summary.info and view.summary.info.get("u") is not None
                        else "N/A",
                    ),
                )
            )

        if self.show_connection_address and view.summary.host:
            address = view.summary.host
            if view.summary.port is not None:
                address += f":{view.summary.port}"
            sections.append(("连接地址", address))

        # Dynamic layout height calculation
        left = 48
        right = self.style.width - 48
        usable_w = right - left
        gap = 18

        top_cards_h = 136
        total_height = 140 + top_cards_h + gap

        # Dedicated GPU Card
        gpu_card_h = 0
        if gpus:
            gpu_card_h = 56 + len(gpus) * 72 + 10
            total_height += gpu_card_h + gap

        # Disk Card
        disk_card_h = 0
        if len(all_disks) > 1:
            num_disk_rows = math.ceil(len(all_disks) / 2)
            disk_card_h = 56 + num_disk_rows * 44 + 14
            total_height += disk_card_h + gap

        # Sensor Card
        sensor_card_h = 0
        if all_sensors:
            num_sensor_rows = math.ceil(len(all_sensors) / 2)
            sensor_card_h = 56 + num_sensor_rows * 36 + 14
            total_height += sensor_card_h + gap

        # Network Interfaces Card
        net_card_h = 0
        if all_net_ifs:
            net_card_h = 56 + len(all_net_ifs) * 40 + 14
            total_height += net_card_h + gap

        # System Config Card
        cfg_rows = math.ceil(len(sections) / 2)
        cfg_card_h = 56 + cfg_rows * 42 + 16
        total_height += cfg_card_h + 48

        # Create Canvas
        image, draw = self._canvas(total_height)

        header_tags = [
            (
                view.summary.status.upper(),
                self.style.status_halo(view.summary.status),
                self.style.status_color(view.summary.status),
            )
        ]
        if view.details and view.details.os:
            header_tags.append(
                (
                    f"{view.details.os}",
                    self.style.card_secondary,
                    self.style.text_secondary,
                )
            )
        if view.details and view.details.arch:
            header_tags.append(
                (
                    f"{view.details.arch}",
                    self.style.card_secondary,
                    self.style.text_secondary,
                )
            )

        y = self._header(
            draw,
            f"{view.summary.name}",
            f"探针 ID: {view.summary.id}",
            tags=header_tags,
        )

        # Draw Top 4 KPI Cards
        kpi_w = (usable_w - 3 * 16) // 4
        kpis = [
            (
                "CPU 使用率",
                cpu_val,
                percent(cpu_val),
                f"负载: {la_str}" if la_str else "",
                "cpu",
            ),
            (
                "内存使用率",
                mem_pct,
                percent(mem_pct),
                mem_sub_text,
                "mem",
            ),
            (
                "根磁盘 (/)",
                disk_pct,
                percent(disk_pct),
                f"已用: {gb_iec(disk_used)} / {gb_iec(disk_total)}"
                if disk_used is not None and disk_total is not None
                else "",
                "disk",
            ),
            (
                "网络实时带宽",
                None,
                format_bandwidth(total_b_speed) if total_b_speed is not None else "N/A",
                f"↓ {format_bandwidth(rx_speed)} · ↑ {format_bandwidth(tx_speed)}"
                if rx_speed is not None and tx_speed is not None
                else "",
                "net",
            ),
        ]

        for idx, (lbl, num_v, val_disp, sub_txt, m_type) in enumerate(kpis):
            c_left = left + idx * (kpi_w + 16)
            c_right = c_left + kpi_w
            self._card(draw, (c_left, y, c_right, y + top_cards_h), radius=10)

            draw.text(
                (c_left + 16, y + 14),
                lbl,
                fill=self.style.muted,
                font=self._font(13, bold=True),
            )
            draw.text(
                (c_left + 16, y + 36),
                val_disp,
                fill=self.style.foreground if is_online else self.style.subtle,
                font=self._font(24, bold=True),
            )

            num_val = safe_float(num_v)
            if num_val is not None:
                bar_top = y + 78
                bar_color = self._dynamic_bar_color(m_type, num_val, is_online)
                self._progress_bar(
                    draw,
                    (c_left + 16, bar_top, c_right - 16, bar_top + 7),
                    num_val,
                    bar_color,
                    radius=3,
                )

            if sub_txt:
                draw.text(
                    (c_left + 16, y + 98),
                    self._ellipsize(draw, sub_txt, self._font(11), kpi_w - 32),
                    fill=self.style.subtle,
                    font=self._font(11),
                )

        y += top_cards_h + gap

        # Draw Dedicated GPU Card
        if gpus:
            self._card(draw, (left, y, right, y + gpu_card_h), radius=12)
            draw.text(
                (left + 20, y + 16),
                "独立显卡监控 (GPU)",
                fill=self.style.foreground,
                font=self._font(15, bold=True),
            )
            draw.line(
                (left + 20, y + 46, right - 20, y + 46),
                fill=self.style.border_subtle,
                width=1,
            )

            half_gpu_w = (usable_w - 40 - 18) // 2
            gpu_y = y + 54
            for gpu in gpus:
                gpu_name = gpu["name"]
                u_pct = gpu["usage"]

                draw.text(
                    (left + 20, gpu_y),
                    gpu_name,
                    fill=self.style.foreground,
                    font=self._font(13, bold=True),
                )

                badges: list[str] = []
                if gpu["power"] is not None:
                    badges.append(f"功耗: {gpu['power']:.0f}W")
                if isinstance(raw_temps, dict):
                    for t_k, t_v in raw_temps.items():
                        gpu_temp = safe_float(t_v)
                        if (
                            gpu_name.casefold() in str(t_k).casefold()
                            and gpu_temp is not None
                        ):
                            badges.append(f"温度: {gpu_temp:.1f} °C")
                            break
                if badges:
                    b_text = " · ".join(badges)
                    b_len = draw.textlength(b_text, font=self._font(12))
                    draw.text(
                        (right - 20 - b_len, gpu_y),
                        b_text,
                        fill=self.style.subtle,
                        font=self._font(12),
                    )

                bar_top_y = gpu_y + 26
                self._draw_overview_bar(
                    draw,
                    left + 20,
                    bar_top_y,
                    left + 20 + half_gpu_w,
                    "核心占用",
                    u_pct,
                    percent(u_pct),
                    "",
                    "gpu",
                    is_online,
                )

                vram_u = gpu["mem_used"]
                vram_t = gpu["mem_total"]
                vram_pct = (
                    (vram_u / vram_t * 100.0)
                    if (vram_u is not None and vram_t is not None and vram_t > 0)
                    else None
                )
                vram_sub = (
                    f"{mb_iec(vram_u)} / {mb_iec(vram_t)}"
                    if (vram_u is not None and vram_t is not None)
                    else ""
                )
                vram_disp = (
                    f"{percent(vram_pct)} ({vram_sub})"
                    if vram_sub
                    else percent(vram_pct)
                )
                self._draw_overview_bar(
                    draw,
                    left + 20 + half_gpu_w + 18,
                    bar_top_y,
                    right - 20,
                    "显存占用",
                    vram_pct,
                    vram_disp,
                    "",
                    "mem",
                    is_online,
                )
                gpu_y += 72
            y += gpu_card_h + gap

        # Draw Disks Section (if multiple disks)
        if len(all_disks) > 1:
            self._card(draw, (left, y, right, y + disk_card_h), radius=12)
            draw.text(
                (left + 20, y + 16),
                "存储与挂载磁盘",
                fill=self.style.foreground,
                font=self._font(15, bold=True),
            )
            draw.line(
                (left + 20, y + 46, right - 20, y + 46),
                fill=self.style.border_subtle,
                width=1,
            )

            half_d_w = (usable_w - 40 - 18) // 2
            d_y = y + 56
            for d_idx in range(0, len(all_disks), 2):
                item0 = all_disks[d_idx]
                self._draw_overview_bar(
                    draw,
                    left + 20,
                    d_y,
                    left + 20 + half_d_w,
                    item0[0],
                    item0[1],
                    f"{percent(item0[1])} ({item0[2]})"
                    if item0[2]
                    else percent(item0[1]),
                    "",
                    "disk",
                    is_online,
                )
                if d_idx + 1 < len(all_disks):
                    item1 = all_disks[d_idx + 1]
                    self._draw_overview_bar(
                        draw,
                        left + 20 + half_d_w + 18,
                        d_y,
                        right - 20,
                        item1[0],
                        item1[1],
                        f"{percent(item1[1])} ({item1[2]})"
                        if item1[2]
                        else percent(item1[1]),
                        "",
                        "disk",
                        is_online,
                    )
                d_y += 44
            y += disk_card_h + gap

        # Draw Hardware Sensors Section
        if all_sensors:
            self._card(draw, (left, y, right, y + sensor_card_h), radius=12)
            draw.text(
                (left + 20, y + 16),
                "硬件传感器与环境监控",
                fill=self.style.foreground,
                font=self._font(15, bold=True),
            )
            draw.line(
                (left + 20, y + 46, right - 20, y + 46),
                fill=self.style.border_subtle,
                width=1,
            )

            half_s_w = (usable_w - 40 - 18) // 2
            s_y = y + 56
            val_font = self._font(13, bold=True)
            for s_idx in range(0, len(all_sensors), 2):
                item0 = all_sensors[s_idx]
                val_len0 = draw.textlength(item0[1], font=val_font)
                lbl_max0 = half_s_w - val_len0 - 16
                lbl0 = self._ellipsize(
                    draw, item0[0], self._font(13), max(60, lbl_max0)
                )
                draw.text(
                    (left + 20, s_y),
                    lbl0,
                    fill=self.style.muted,
                    font=self._font(13),
                )
                draw.text(
                    (left + 20 + half_s_w - val_len0, s_y),
                    item0[1],
                    fill=self.style.foreground,
                    font=val_font,
                )

                if s_idx + 1 < len(all_sensors):
                    item1 = all_sensors[s_idx + 1]
                    s_left1 = left + 20 + half_s_w + 18
                    val_len1 = draw.textlength(item1[1], font=val_font)
                    lbl_max1 = half_s_w - val_len1 - 16
                    lbl1 = self._ellipsize(
                        draw, item1[0], self._font(13), max(60, lbl_max1)
                    )
                    draw.text(
                        (s_left1, s_y),
                        lbl1,
                        fill=self.style.muted,
                        font=self._font(13),
                    )
                    draw.text(
                        (right - 20 - val_len1, s_y),
                        item1[1],
                        fill=self.style.foreground,
                        font=val_font,
                    )
                s_y += 36
            y += sensor_card_h + gap

        # Draw Network Interfaces Section
        if all_net_ifs:
            self._card(draw, (left, y, right, y + net_card_h), radius=12)
            draw.text(
                (left + 20, y + 16),
                "网络接口流量明细",
                fill=self.style.foreground,
                font=self._font(15, bold=True),
            )
            draw.line(
                (left + 20, y + 46, right - 20, y + 46),
                fill=self.style.border_subtle,
                width=1,
            )

            n_y = y + 56
            for if_name, rate_str, total_str in all_net_ifs:
                draw.text(
                    (left + 20, n_y),
                    f"网卡: {if_name}",
                    fill=self.style.foreground,
                    font=self._font(13, bold=True),
                )
                draw.text(
                    (left + 220, n_y),
                    rate_str,
                    fill=self.style.muted,
                    font=self._font(13),
                )
                if total_str:
                    t_len = draw.textlength(total_str, font=self._font(12))
                    draw.text(
                        (right - 20 - t_len, n_y + 1),
                        total_str,
                        fill=self.style.subtle,
                        font=self._font(12),
                    )
                n_y += 40
            y += net_card_h + gap

        # Draw System & Hardware Config Section
        self._card(draw, (left, y, right, y + cfg_card_h), radius=12)
        draw.text(
            (left + 20, y + 16),
            "硬件与系统配置",
            fill=self.style.foreground,
            font=self._font(15, bold=True),
        )
        draw.line(
            (left + 20, y + 46, right - 20, y + 46),
            fill=self.style.border_subtle,
            width=1,
        )

        cfg_y = y + 56
        half_cfg_w = (usable_w - 40 - 24) // 2
        for index, (label, value) in enumerate(sections):
            col = index % 2
            row = index // 2
            item_x = left + 20 + col * (half_cfg_w + 24)
            item_y = cfg_y + row * 40

            draw.text(
                (item_x, item_y), label, fill=self.style.muted, font=self._font(13)
            )
            val_font = self._font(13, bold=True)
            val_str = self._ellipsize(draw, value, val_font, half_cfg_w - 120)
            draw.text(
                (item_x + 110, item_y),
                val_str,
                fill=self.style.foreground,
                font=val_font,
            )

        self._footer(draw, total_height - 32)
        return self._save(image)

    def _render_history(self, view: SystemHistoryView) -> bytes:
        cards = self._build_history_chart_cards(view.points)
        chart_height = 240
        gap = 18
        left, right = 48, self.style.width - 48
        usable_w = right - left
        card_w = (usable_w - gap) // 2

        num_rows = math.ceil(len(cards) / 2) if cards else 1
        height = 140 + num_rows * (chart_height + gap) + 40
        image, draw = self._canvas(height)

        time_span = ""
        if view.points:
            t_first = (
                view.points[0]
                .created.astimezone(self.display_timezone)
                .strftime("%H:%M")
            )
            t_last = (
                view.points[-1]
                .created.astimezone(self.display_timezone)
                .strftime("%H:%M")
            )
            time_span = f"{t_first} ~ {t_last}"

        range_cn_map = {
            "1h": "1 小时",
            "12h": "12 小时",
            "24h": "24 小时",
            "1w": "1 周",
            "30d": "30 天",
        }
        range_label = range_cn_map.get(view.range.value, view.range.value)

        status_tag = {
            "up": ("在线", self.style.up, (255, 255, 255)),
            "down": ("离线", self.style.down, (255, 255, 255)),
            "unknown": ("未知", self.style.unknown, (255, 255, 255)),
        }[status_state(view.summary.status)]
        tags: list[tuple[str, tuple[int, int, int], tuple[int, int, int]]] = [
            status_tag
        ]

        info = view.summary.info or {}
        if self.show_connection_address and view.summary.host:
            host_text = (
                f"{view.summary.host}:{view.summary.port}"
                if view.summary.port
                else str(view.summary.host)
            )
            tags.append((host_text, self.style.card_secondary, self.style.muted))
        if info.get("h") or info.get("hostname"):
            tags.append(
                (
                    str(info.get("h") or info.get("hostname")),
                    self.style.card_secondary,
                    self.style.muted,
                )
            )
        u_val = info.get("u")
        if u_val is not None:
            tags.append((uptime_cn(u_val), self.style.card_secondary, self.style.muted))
        if info.get("os"):
            tags.append((str(info["os"]), self.style.card_secondary, self.style.muted))
        if info.get("cpu"):
            tags.append((str(info["cpu"]), self.style.card_secondary, self.style.muted))
        tags.append(
            (
                f"{len(view.points)} 采样点",
                self.style.card_secondary,
                self.style.muted,
            )
        )

        y = self._header(
            draw,
            f"{view.summary.name} · 历史监控",
            f"跨度: {range_label} ({time_span})",
            tags=tags,
        )

        if not cards:
            self._card(draw, (left, y, right, y + 140))
            draw.text(
                (left + 20, y + 54),
                "该时间范围内暂无历史时序数据",
                fill=self.style.muted,
                font=self._font(18),
            )
            self._footer(draw, height - 32)
            return self._save(image)

        for index, card in enumerate(cards):
            col = index % 2
            row = index // 2
            c_left = left + col * (card_w + gap)
            c_right = c_left + card_w
            c_top = y + row * (chart_height + gap)
            c_bottom = c_top + chart_height

            self._card(draw, (c_left, c_top, c_right, c_bottom), radius=12)

            t_font = self._font(14, bold=True)
            draw.text(
                (c_left + 16, c_top + 13),
                card.title,
                fill=self.style.foreground,
                font=t_font,
            )
            draw.text(
                (c_left + 16, c_top + 32),
                card.subtitle,
                fill=self.style.muted,
                font=self._font(11),
            )

            if card.series_list and card.series_list[0].points:
                prim_pts = card.series_list[0].points
                vals = [v for _, v in prim_pts]
                cur_val = vals[-1] if vals else 0.0
                cur_str = self._format_history_metric_value(cur_val, card.unit_type)
                b_text = f"当前: {cur_str}"
                cur_font = self._font(11)
                b_len = draw.textlength(b_text, font=cur_font)
                t_bbox = t_font.getbbox(card.title)
                c_bbox = cur_font.getbbox(b_text)
                title_baseline = (c_top + 13) + t_bbox[3]
                cur_y = title_baseline - c_bbox[3]
                draw.text(
                    (c_right - 16 - b_len, cur_y),
                    b_text,
                    fill=self.style.subtle,
                    font=cur_font,
                )

            has_legend = len(card.series_list) > 1
            plot_bottom = c_bottom - (30 if has_legend else 22)
            plot = (c_left + 64, c_top + 52, c_right - 16, plot_bottom)

            self._draw_area_chart(
                image,
                draw,
                plot,
                card.series_list,
                gap_seconds=view.range.expected_interval.total_seconds() * 1.5,
                unit_type=card.unit_type,
                max_val_override=card.max_val_override,
                has_legend=has_legend,
                card_bottom=c_bottom,
            )

        self._footer(draw, height - 32)
        return self._save(image)

    @staticmethod
    def _split_series_points(
        points: Iterable[tuple[datetime, float]], gap_seconds: float
    ) -> list[list[tuple[datetime, float]]]:
        """Split a series into contiguous segments, breaking across gaps.

        A new segment starts when the distance to the previous valid point
        exceeds ``gap_seconds`` (the expected sampling interval times the
        shared 1.5 gap factor). Non-finite values are treated as missing
        samples and dropped, which also breaks the line across them.
        """
        segments: list[list[tuple[datetime, float]]] = []
        previous: datetime | None = None
        for pt_time, value in points:
            if not math.isfinite(value):
                continue
            if (
                previous is not None
                and (pt_time - previous).total_seconds() > gap_seconds
            ):
                segments.append([])
            if not segments:
                segments.append([])
            segments[-1].append((pt_time, value))
            previous = pt_time
        return segments

    @staticmethod
    def _format_history_metric_value(val: float, unit_type: str) -> str:
        if unit_type == "percent":
            return f"{val:.1f}%"
        if unit_type == "bytes":
            return bytes_iec(val)
        if unit_type == "bytes_sec":
            return f"{bytes_iec(val)}/s"
        if unit_type == "watts":
            return f"{int(val)}W"
        if unit_type == "temp":
            return f"{val:.1f} °C"
        if unit_type == "rpm":
            return f"{int(val)} RPM"
        if unit_type == "load":
            return f"{val:.2f}"
        return f"{val:.1f}"

    def _draw_area_chart(
        self,
        base_image: Image.Image,
        draw: ImageDraw.ImageDraw,
        plot: tuple[int, int, int, int],
        series_list: list[HistoryChartSeries],
        gap_seconds: float,
        unit_type: str = "percent",
        max_val_override: float | None = None,
        has_legend: bool = False,
        card_bottom: int = 0,
    ) -> None:
        left, top, right, bottom = plot
        all_values = [v for s in series_list for _, v in s.points]

        if unit_type == "percent":
            low = 0.0
            max_v = max(all_values) if all_values else 100.0
            high = (
                100.0
                if max_v > 50.0
                else (max(33.0, max_v * 1.15) if max_v > 0 else 100.0)
            )
        elif unit_type == "bytes":
            low = 0.0
            max_v = max(all_values) if all_values else 1.0
            if max_val_override and max_val_override > 0:
                high = float(max_val_override)
            else:
                high = max(1.0, max_v * 1.1)
        elif unit_type == "bytes_sec":
            low = 0.0
            max_v = max(all_values) if all_values else 1.0
            high = max(1.0, max_v * 1.15)
        elif unit_type == "watts":
            low = 0.0
            max_v = max(all_values) if all_values else 1.0
            high = max(10.0, max_v * 1.15)
        elif unit_type == "temp":
            min_v = min(all_values) if all_values else 30.0
            max_v = max(all_values) if all_values else 70.0
            low = max(0.0, min_v - 5.0)
            high = max(low + 10.0, max_v + 5.0)
        elif unit_type == "rpm":
            low = 0.0
            max_v = max(all_values) if all_values else 1000.0
            high = max(100.0, max_v * 1.15)
        elif unit_type == "load":
            low = 0.0
            max_v = max(all_values) if all_values else 1.0
            high = max(0.2, max_v * 1.15)
        else:
            min_v = min(all_values) if all_values else 0.0
            max_v = max(all_values) if all_values else 1.0
            low = min_v if min_v < 0 else 0.0
            high = max_v if max_v > low else low + 1.0

        grid_font = self._font(10)
        for step in range(4):
            ratio = step / 3.0
            y = int(bottom - (bottom - top) * ratio)
            draw.line((left, y, right, y), fill=self.style.grid, width=1)
            val_at_grid = low + (high - low) * ratio

            if unit_type == "percent":
                tick_text = f"{int(val_at_grid)}%"
            elif unit_type == "bytes":
                tick_text = bytes_iec(val_at_grid)
            elif unit_type == "bytes_sec":
                tick_text = f"{bytes_iec(val_at_grid)}/s"
            elif unit_type == "watts":
                tick_text = f"{int(val_at_grid)}W"
            elif unit_type == "temp":
                tick_text = f"{int(val_at_grid)} °C"
            elif unit_type == "rpm":
                tick_text = f"{int(val_at_grid)}"
            elif unit_type == "load":
                tick_text = f"{val_at_grid:.2f}" if high < 1.0 else f"{val_at_grid:.1f}"
            else:
                tick_text = f"{val_at_grid:.1f}"

            t_len = draw.textlength(tick_text, font=grid_font)
            draw.text(
                (left - t_len - 6, y - 5),
                tick_text,
                fill=self.style.subtle,
                font=grid_font,
            )

        plot_w = right - left
        plot_h = bottom - top
        if plot_w <= 0 or plot_h <= 0 or not series_list:
            return

        scale = 2
        hr_w = plot_w * scale
        hr_h = plot_h * scale

        series_coords: list[
            tuple[HistoryChartSeries, list[list[tuple[float, float]]]]
        ] = []
        for series in series_list:
            points = series.points
            if not points:
                continue
            time_first = points[0][0].timestamp()
            time_last = points[-1][0].timestamp()
            time_span = max(1.0, time_last - time_first)

            segments: list[list[tuple[float, float]]] = []
            for raw_segment in self._split_series_points(points, gap_seconds):
                coords: list[tuple[float, float]] = []
                for pt_time, value in raw_segment:
                    x_hr = ((pt_time.timestamp() - time_first) / time_span) * (hr_w - 1)
                    clamped_val = min(high, max(low, value))
                    y_hr = (
                        (hr_h - 1) - ((clamped_val - low) / (high - low)) * (hr_h - 1)
                        if high > low
                        else (hr_h - 1)
                    )
                    coords.append((x_hr, y_hr))
                segments.append(coords)
            series_coords.append((series, segments))

        fill_layer = Image.new("RGBA", (hr_w, hr_h), (0, 0, 0, 0))
        for series, segments in series_coords:
            fill_rgba = (series.color[0], series.color[1], series.color[2], 38)
            temp_fill = Image.new("RGBA", (hr_w, hr_h), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_fill)
            for coords in segments:
                if len(coords) > 1:
                    poly = [
                        (coords[0][0], float(hr_h - 1)),
                        *coords,
                        (coords[-1][0], float(hr_h - 1)),
                    ]
                    temp_draw.polygon(poly, fill=fill_rgba)
            fill_layer = Image.alpha_composite(fill_layer, temp_fill)

        line_layer = Image.new("RGBA", (hr_w, hr_h), (0, 0, 0, 0))
        line_draw = ImageDraw.Draw(line_layer)
        for series, segments in series_coords:
            for coords in segments:
                if len(coords) > 1:
                    line_draw.line(
                        coords,
                        fill=(*series.color, 255),
                        width=2 * scale,
                        joint="curve",
                    )
                    line_draw.ellipse(
                        (
                            coords[-1][0] - 3 * scale,
                            coords[-1][1] - 3 * scale,
                            coords[-1][0] + 3 * scale,
                            coords[-1][1] + 3 * scale,
                        ),
                        fill=(*series.color, 255),
                    )
                elif coords:
                    line_draw.ellipse(
                        (
                            coords[0][0] - 3 * scale,
                            coords[0][1] - 3 * scale,
                            coords[0][0] + 3 * scale,
                            coords[0][1] + 3 * scale,
                        ),
                        fill=(*series.color, 255),
                    )

        combined_hr = Image.alpha_composite(fill_layer, line_layer)
        smooth_plot = combined_hr.resize(
            (plot_w, plot_h), resample=Image.Resampling.LANCZOS
        )
        base_image.paste(smooth_plot, (left, top), smooth_plot)

        ref_points = series_list[0].points if series_list else []
        if ref_points:
            time_first = ref_points[0][0].timestamp()
            time_last = ref_points[-1][0].timestamp()
            time_span = max(1.0, time_last - time_first)
            tick_count = min(5, len(ref_points))
            step = (len(ref_points) - 1) / max(1, tick_count - 1)
            for i in range(tick_count):
                idx = round(i * step)
                time_str = (
                    ref_points[idx][0]
                    .astimezone(self.display_timezone)
                    .strftime("%H:%M")
                )
                x = left + (right - left) * (
                    (ref_points[idx][0].timestamp() - time_first) / time_span
                )
                t_len = draw.textlength(time_str, font=grid_font)
                tx = max(left, min(right - t_len, x - t_len / 2))
                draw.text(
                    (tx, bottom + 4),
                    time_str,
                    fill=self.style.subtle,
                    font=grid_font,
                )

        if has_legend:
            legend_font = self._font(10)
            card_min_x = left - 48
            card_max_x = right
            avail_w = max(100, card_max_x - card_min_x)

            raw_items = []
            for s in series_list:
                t_len = draw.textlength(s.name, font=legend_font)
                raw_items.append((s.name, s.color, t_len, t_len + 9))

            tot_all_w = sum(w for _, _, _, w in raw_items) + (len(raw_items) - 1) * 12

            if tot_all_w <= avail_w:
                visible_items = [
                    (name, col, t_len) for name, col, t_len, _ in raw_items
                ]
                extra_text = ""
                actual_w = tot_all_w
            else:
                visible_items = []
                extra_text = ""
                actual_w = 0.0
                for k in range(len(raw_items) - 1, 0, -1):
                    rem = len(raw_items) - k
                    cand_text = f"+{rem}"
                    cand_extra_w = draw.textlength(cand_text, font=legend_font) + 4
                    cand_w = (
                        sum(w for _, _, _, w in raw_items[:k]) + k * 12 + cand_extra_w
                    )
                    if cand_w <= avail_w:
                        visible_items = [
                            (name, col, t_len) for name, col, t_len, _ in raw_items[:k]
                        ]
                        extra_text = cand_text
                        actual_w = cand_w
                        break
                if not visible_items and raw_items:
                    visible_items = [
                        (raw_items[0][0], raw_items[0][1], raw_items[0][2])
                    ]
                    extra_text = f"+{len(raw_items) - 1}"
                    actual_w = (
                        raw_items[0][3]
                        + 12
                        + draw.textlength(extra_text, font=legend_font)
                    )

            cur_lx = card_min_x + max(0, int((avail_w - actual_w) // 2))
            ly = card_bottom - 15
            for name, s_col, t_len in visible_items:
                draw.ellipse((cur_lx, ly + 2, cur_lx + 6, ly + 8), fill=s_col)
                draw.text(
                    (cur_lx + 9, ly), name, fill=self.style.muted, font=legend_font
                )
                cur_lx += int(t_len) + 9 + 12

            if extra_text:
                extra_font = self._font(10, bold=True)
                draw.text(
                    (cur_lx, ly),
                    extra_text,
                    fill=self.style.muted,
                    font=extra_font,
                )

    @classmethod
    def _build_history_chart_cards(
        cls,
        points: Iterable[Any],
    ) -> list[HistoryChartCard]:
        raw_points = list(points)
        if not raw_points:
            return []

        valid_points: list[tuple[datetime, dict[str, Any]]] = []
        for p in raw_points:
            c = getattr(p, "created", None)
            s = getattr(p, "stats", {})
            if c is not None and isinstance(s, dict):
                valid_points.append((c, s))
        if not valid_points:
            return []
        valid_points.sort(key=lambda x: x[0])

        cards: list[HistoryChartCard] = []

        cpu_pts = []
        for c, s in valid_points:
            v = cls._first(s, "cpu", "cpu_percent", "cpus")
            f = cls._extract_metric_float(v)
            if f is not None:
                cpu_pts.append((c, f))
        if cpu_pts:
            cards.append(
                HistoryChartCard(
                    title="CPU 使用率",
                    subtitle="系统范围内的平均 CPU 使用率",
                    series_list=[
                        HistoryChartSeries(
                            name="CPU", points=cpu_pts, color=(59, 130, 246)
                        )
                    ],
                    unit_type="percent",
                )
            )

        mem_pts = []
        total_ram_bytes: float | None = None
        for _, s in valid_points:
            m_t = safe_float(s.get("m"))
            if m_t is not None and m_t > 0:
                total_ram_bytes = gb_to_bytes(m_t)
                break

        for c, s in valid_points:
            m_u = safe_float(s.get("mu"))
            m_p = cls._extract_metric_float(cls._first(s, "mp", "memory_percent"))
            m_total = safe_float(s.get("m"))
            if total_ram_bytes is not None:
                if m_u is not None:
                    mem_pts.append((c, min(gb_to_bytes(m_u), total_ram_bytes)))
                elif m_p is not None:
                    mem_pts.append((c, (m_p / 100.0) * total_ram_bytes))
            else:
                if m_p is not None:
                    mem_pts.append((c, m_p))
                elif m_u is not None and m_total is not None and m_total > 0:
                    mem_pts.append((c, (m_u / m_total) * 100.0))

        if mem_pts:
            cards.append(
                HistoryChartCard(
                    title="内存使用率",
                    subtitle="采集时间下的精确内存使用率",
                    series_list=[
                        HistoryChartSeries(
                            name="内存", points=mem_pts, color=(16, 185, 129)
                        )
                    ],
                    unit_type="bytes" if total_ram_bytes else "percent",
                    max_val_override=total_ram_bytes,
                )
            )

        disk_pts = []
        total_root_disk_bytes: float | None = None
        for _, s in valid_points:
            d_t = safe_float(s.get("d"))
            if d_t is not None and d_t > 0:
                total_root_disk_bytes = gb_to_bytes(d_t)
                break

        for c, s in valid_points:
            d_u = safe_float(s.get("du"))
            d_p = cls._extract_metric_float(cls._first(s, "dp", "disk_percent"))
            d_total = safe_float(s.get("d"))
            if total_root_disk_bytes is not None:
                if d_u is not None:
                    disk_pts.append((c, min(gb_to_bytes(d_u), total_root_disk_bytes)))
                elif d_p is not None:
                    disk_pts.append((c, (d_p / 100.0) * total_root_disk_bytes))
            else:
                if d_p is not None:
                    disk_pts.append((c, d_p))
                elif d_u is not None and d_total is not None and d_total > 0:
                    disk_pts.append((c, (d_u / d_total) * 100.0))

        if disk_pts:
            cards.append(
                HistoryChartCard(
                    title="磁盘使用",
                    subtitle="根分区的使用",
                    series_list=[
                        HistoryChartSeries(
                            name="根分区",
                            points=disk_pts,
                            color=(168, 85, 247),
                        )
                    ],
                    unit_type="bytes" if total_root_disk_bytes else "percent",
                    max_val_override=total_root_disk_bytes,
                )
            )

        dr_pts, dw_pts = [], []
        for c, s in valid_points:
            dio = s.get("dio")
            r_bytes: float = 0.0
            w_bytes: float = 0.0
            if isinstance(dio, (list, tuple)) and len(dio) >= 2:
                r_bytes = safe_float(dio[0]) or 0.0
                w_bytes = safe_float(dio[1]) or 0.0
            else:
                dr = safe_float(cls._first(s, "dr", "r"))
                dw = safe_float(cls._first(s, "dw", "w"))
                if dr is not None:
                    r_bytes = mib_rate_to_bytes(dr)
                if dw is not None:
                    w_bytes = mib_rate_to_bytes(dw)

            dr_pts.append((c, r_bytes))
            dw_pts.append((c, w_bytes))

        if any(p[1] > 0 for p in dr_pts) or any(p[1] > 0 for p in dw_pts):
            cards.append(
                HistoryChartCard(
                    title="磁盘 I/O",
                    subtitle="根文件系统的吞吐量",
                    series_list=[
                        HistoryChartSeries(
                            name="读取", points=dr_pts, color=(59, 130, 246)
                        ),
                        HistoryChartSeries(
                            name="写入", points=dw_pts, color=(244, 63, 94)
                        ),
                    ],
                    unit_type="bytes_sec",
                )
            )

        rx_pts, tx_pts = [], []
        for c, s in valid_points:
            b = cls._first(s, "b", "bandwidth")
            if isinstance(b, (list, tuple)) and len(b) >= 2:
                tx_val = safe_float(b[0])
                rx_val = safe_float(b[1])
                if tx_val is not None:
                    tx_pts.append((c, tx_val))
                if rx_val is not None:
                    rx_pts.append((c, rx_val))
            else:
                b_scalar = safe_float(b)
                if b_scalar is not None:
                    rx_pts.append((c, b_scalar))
        if rx_pts or tx_pts:
            cards.append(
                HistoryChartCard(
                    title="带宽",
                    subtitle="公共接口的网络流量",
                    series_list=[
                        HistoryChartSeries(
                            name="下行 (Rx)",
                            points=rx_pts,
                            color=(16, 185, 129),
                        ),
                        HistoryChartSeries(
                            name="上行 (Tx)",
                            points=tx_pts,
                            color=(168, 85, 247),
                        ),
                    ],
                    unit_type="bytes_sec",
                )
            )

        la1_pts, la5_pts, la15_pts = [], [], []
        for c, s in valid_points:
            la = cls._first(s, "la", "load")
            if isinstance(la, (list, tuple)):
                for index, bucket in enumerate((la1_pts, la5_pts, la15_pts)):
                    if len(la) > index:
                        la_val = safe_float(la[index])
                        if la_val is not None:
                            bucket.append((c, la_val))
            else:
                la_scalar = safe_float(la)
                if la_scalar is not None:
                    la1_pts.append((c, la_scalar))
        if la1_pts:
            load_series = [
                HistoryChartSeries(name="1 分钟", points=la1_pts, color=(59, 130, 246))
            ]
            if la5_pts:
                load_series.append(
                    HistoryChartSeries(
                        name="5 分钟", points=la5_pts, color=(168, 85, 247)
                    )
                )
            if la15_pts:
                load_series.append(
                    HistoryChartSeries(
                        name="15 分钟", points=la15_pts, color=(245, 158, 11)
                    )
                )
            cards.append(
                HistoryChartCard(
                    title="系统负载",
                    subtitle="系统负载平均值随时间变化",
                    series_list=load_series,
                    unit_type="load",
                )
            )

        t_pts_map: dict[str, list[tuple[datetime, float]]] = {}
        for c, s in valid_points:
            dt = safe_float(s.get("dt"))
            if dt is not None:
                t_pts_map.setdefault("主温度", []).append((c, dt))
            else:
                raw_t = s.get("t")
                t_scalar = safe_float(raw_t)
                if t_scalar is not None:
                    t_pts_map.setdefault("主温度", []).append((c, t_scalar))
                elif isinstance(raw_t, list) and raw_t:
                    nums = [v for x in raw_t if (v := safe_float(x)) is not None]
                    if nums:
                        t_pts_map.setdefault("主温度", []).append((c, max(nums)))
                elif isinstance(raw_t, dict) and raw_t:
                    for k, v in raw_t.items():
                        t_val = safe_float(v)
                        if t_val is not None:
                            t_pts_map.setdefault(str(k), []).append((c, t_val))
        if t_pts_map:
            cards.append(
                HistoryChartCard(
                    title="温度",
                    subtitle="系统传感器的温度",
                    series_list=cls._temperature_series(t_pts_map),
                    unit_type="temp",
                )
            )

        gpu_names: set[str] = set()
        for _, s in valid_points:
            for gpu in cls._extract_gpus(cls._first(s, "g", "gpu")):
                gpu_names.add(gpu["name"])
        for gpu_name in sorted(gpu_names):
            g_u_pts, g_vram_pts, g_pwr_pts = [], [], []
            total_vram_bytes: float | None = None
            for c, s in valid_points:
                for gpu in cls._extract_gpus(cls._first(s, "g", "gpu")):
                    if gpu["name"] == gpu_name:
                        g_u_pts.append((c, gpu["usage"]))
                        if (
                            gpu["mem_used"] is not None
                            and gpu["mem_total"] is not None
                            and gpu["mem_total"] > 0
                        ):
                            g_vram_pts.append((c, gpu["mem_used"] * (1024**2)))
                            if total_vram_bytes is None:
                                total_vram_bytes = gpu["mem_total"] * (1024**2)
                        if gpu["power"] is not None:
                            g_pwr_pts.append((c, gpu["power"]))

            if g_pwr_pts:
                cards.append(
                    HistoryChartCard(
                        title="GPU 功耗",
                        subtitle="GPU 平均能耗",
                        series_list=[
                            HistoryChartSeries(
                                name="功耗",
                                points=g_pwr_pts,
                                color=(139, 92, 246),
                            )
                        ],
                        unit_type="watts",
                    )
                )

            if g_u_pts:
                cards.append(
                    HistoryChartCard(
                        title=f"{gpu_name} 使用",
                        subtitle=f"{gpu_name} 平均利用率",
                        series_list=[
                            HistoryChartSeries(
                                name="利用率",
                                points=g_u_pts,
                                color=(59, 130, 246),
                            )
                        ],
                        unit_type="percent",
                    )
                )

            if g_vram_pts:
                cards.append(
                    HistoryChartCard(
                        title=f"{gpu_name} VRAM",
                        subtitle="采集时间下的精确内存使用率",
                        series_list=[
                            HistoryChartSeries(
                                name="VRAM",
                                points=g_vram_pts,
                                color=(16, 185, 129),
                            )
                        ],
                        unit_type="bytes" if total_vram_bytes else "percent",
                        max_val_override=total_vram_bytes,
                    )
                )

        efs_keys: set[str] = set()
        for _, s in valid_points:
            efs = s.get("efs")
            if isinstance(efs, dict):
                efs_keys.update(str(k) for k in efs)
        for disk_name in sorted(efs_keys):
            d_pts = []
            d_rb_pts, d_wb_pts = [], []
            total_disk_bytes: float | None = None
            for c, s in valid_points:
                efs = s.get("efs")
                if isinstance(efs, dict) and disk_name in efs:
                    d_data = efs[disk_name]
                    d_scalar = safe_float(d_data)
                    if d_scalar is not None:
                        d_pts.append((c, d_scalar))
                    elif isinstance(d_data, dict):
                        d_u = safe_float(d_data.get("du"))
                        d_t = safe_float(d_data.get("d"))
                        if d_u is not None and d_t is not None and d_t > 0:
                            d_pts.append((c, gb_to_bytes(d_u)))
                            if total_disk_bytes is None:
                                total_disk_bytes = gb_to_bytes(d_t)
                        else:
                            d_p = safe_float(d_data.get("dp"))
                            if d_p is not None:
                                d_pts.append((c, d_p))

                        r_bytes: float = 0.0
                        w_bytes: float = 0.0
                        rb_val = safe_float(d_data.get("rb"))
                        wb_val = safe_float(d_data.get("wb"))
                        if rb_val is not None:
                            r_bytes = rb_val
                        else:
                            r_val = safe_float(d_data.get("r"))
                            if r_val is not None:
                                r_bytes = mib_rate_to_bytes(r_val)

                        if wb_val is not None:
                            w_bytes = wb_val
                        else:
                            w_val = safe_float(d_data.get("w"))
                            if w_val is not None:
                                w_bytes = mib_rate_to_bytes(w_val)

                        d_rb_pts.append((c, r_bytes))
                        d_wb_pts.append((c, w_bytes))

            if d_pts:
                cards.append(
                    HistoryChartCard(
                        title=f"{disk_name} 使用",
                        subtitle=f"{disk_name}的磁盘使用",
                        series_list=[
                            HistoryChartSeries(
                                name=disk_name,
                                points=d_pts,
                                color=(168, 85, 247),
                            )
                        ],
                        unit_type="bytes" if total_disk_bytes else "percent",
                        max_val_override=total_disk_bytes,
                    )
                )

            if any(p[1] > 0 for p in d_rb_pts) or any(p[1] > 0 for p in d_wb_pts):
                cards.append(
                    HistoryChartCard(
                        title=f"{disk_name} I/O",
                        subtitle=f"{disk_name}的吞吐量",
                        series_list=[
                            HistoryChartSeries(
                                name="读取",
                                points=d_rb_pts,
                                color=(59, 130, 246),
                            ),
                            HistoryChartSeries(
                                name="写入",
                                points=d_wb_pts,
                                color=(244, 63, 94),
                            ),
                        ],
                        unit_type="bytes_sec",
                    )
                )

        fan_names: set[str] = set()
        for _, s in valid_points:
            raw_f = s.get("f")
            if isinstance(raw_f, dict):
                fan_names.update(str(k) for k in raw_f)
        for f_name in sorted(fan_names):
            f_pts = []
            for c, s in valid_points:
                raw_f = s.get("f")
                if isinstance(raw_f, dict) and f_name in raw_f:
                    f_val = safe_float(raw_f[f_name])
                    if f_val is not None:
                        f_pts.append((c, f_val))
            if f_pts and max(p[1] for p in f_pts) > 0:
                clean_f = str(f_name).replace("nct6793_", "").replace("_", " ").title()
                cards.append(
                    HistoryChartCard(
                        title=f"风扇 ({clean_f})",
                        subtitle="风扇实时转速",
                        series_list=[
                            HistoryChartSeries(
                                name="转速", points=f_pts, color=(20, 184, 166)
                            )
                        ],
                        unit_type="rpm",
                    )
                )

        bat_pts = []
        for c, s in valid_points:
            bat = s.get("bat")
            bat_val = safe_float(
                bat[0] if isinstance(bat, (list, tuple)) and bat else bat
            )
            if bat_val is not None:
                bat_pts.append((c, bat_val))
        if bat_pts:
            cards.append(
                HistoryChartCard(
                    title="电池电量",
                    subtitle="系统电池剩余电量",
                    series_list=[
                        HistoryChartSeries(
                            name="电量", points=bat_pts, color=(132, 204, 22)
                        )
                    ],
                    unit_type="percent",
                )
            )

        swap_pts = []
        for c, s in valid_points:
            v = cls._first(s, "su", "s", "swap")
            f = cls._extract_metric_float(v)
            if f is not None:
                swap_pts.append((c, f))
        if swap_pts and max(p[1] for p in swap_pts) > 0:
            cards.append(
                HistoryChartCard(
                    title="Swap 交换空间",
                    subtitle="交换分区使用率",
                    series_list=[
                        HistoryChartSeries(
                            name="Swap",
                            points=swap_pts,
                            color=(139, 92, 246),
                        )
                    ],
                    unit_type="percent",
                )
            )

        return cards

    @staticmethod
    def _extract_metric_float(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value if math.isfinite(value) else None
        if isinstance(value, list) and value:
            numeric_items = [v for x in value if (v := safe_float(x)) is not None]
            if numeric_items:
                return float(sum(numeric_items))
        if isinstance(value, dict) and value:
            numeric_vals = [
                v for x in value.values() if (v := safe_float(x)) is not None
            ]
            if numeric_vals:
                return float(sum(numeric_vals) / len(numeric_vals))
        return None

    @staticmethod
    def _first(mapping: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None

    @staticmethod
    def _usage_percent(
        source: dict[str, Any],
        pct_keys: tuple[str, ...],
        *,
        used_key: str,
        total_key: str,
    ) -> float | None:
        """Read an explicit percentage, or derive it from used/total.

        Only unambiguous percentage fields (e.g. ``mp``/``memory_percent``)
        are read directly; capacity totals (``m``/``d``) are never shown as
        percentages. The ratio is derived only when both values are finite
        and total > 0.
        """
        pct = safe_float(BeszelRenderer._first(source, *pct_keys))
        if pct is not None:
            return pct
        used = safe_float(source.get(used_key))
        total = safe_float(source.get(total_key))
        if used is not None and total is not None and total > 0:
            return used / total * 100.0
        return None

    @staticmethod
    def _ellipsize(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: float,
    ) -> str:
        if draw.textlength(text, font=font) <= max_width:
            return text
        suffix = "..."
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if draw.textlength(text[:middle] + suffix, font=font) <= max_width:
                low = middle
            else:
                high = middle - 1
        return text[:low] + suffix


@dataclass
class HistoryChartSeries:
    name: str
    points: list[tuple[datetime, float]]
    color: tuple[int, int, int]


@dataclass
class HistoryChartCard:
    title: str
    subtitle: str
    series_list: list[HistoryChartSeries]
    unit_type: str = (
        "percent"  # "percent", "bytes", "bytes_sec", "watts", "temp", "rpm", "load"
    )
    max_val_override: float | None = None
