"""Pytakumi lifecycle and in-memory PNG rendering boundary."""

from __future__ import annotations

from pathlib import Path

import pytakumi
from astrbot.api import logger

from ..errors import RenderingError

DEFAULT_GLYPH_CACHE_BYTES = 32 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PytakumiEngine:
    """Own one native renderer and one registered font set."""

    def __init__(
        self,
        *,
        bundled_font_path: str | Path,
        configured_font_path: str | Path | None = None,
        stylesheet: str = "",
        glyph_cache_bytes: int = DEFAULT_GLYPH_CACHE_BYTES,
    ) -> None:
        if glyph_cache_bytes < 0:
            raise RenderingError("渲染缓存预算不能为负数")
        self.stylesheet = stylesheet
        self.font_family: str
        try:
            pytakumi.set_glyph_cache_max_bytes(glyph_cache_bytes)
            self._renderer = pytakumi.Renderer(cache_max_bytes=glyph_cache_bytes)
            self.font_family = self._register_selected_font(
                bundled_font_path=bundled_font_path,
                configured_font_path=configured_font_path,
            )
        except RenderingError:
            raise
        except Exception as exc:
            raise RenderingError(
                "🖼️ 图片渲染引擎初始化失败，请检查字体文件与系统环境"
            ) from exc

    def render(self, markup: str, *, width: int, height: int | None = None) -> bytes:
        """Render local markup to a non-empty PNG byte string."""
        if not isinstance(markup, str) or not markup:
            raise RenderingError("图片渲染内容为空")
        if width <= 0 or (height is not None and height <= 0):
            raise RenderingError("图片渲染尺寸无效")
        try:
            source = pytakumi.from_html(markup)
            result = self._render_native(
                source,
                width=width,
                height=height,
            )
        except Exception as exc:
            logger.debug("Pytakumi render failed: %s", type(exc).__name__)
            raise RenderingError("🖼️ 图片渲染失败，请查看日志获取详细信息") from exc
        if not isinstance(result, (bytes, bytearray, memoryview)):
            raise RenderingError("图片渲染结果类型无效")
        png = bytes(result)
        if not png.startswith(PNG_SIGNATURE):
            raise RenderingError("图片渲染结果不是有效 PNG")
        return png

    def _render_native(self, source, *, width: int, height: int | None) -> bytes:
        return self._renderer.render(
            source,
            width=width,
            height=height,
            format="png",
            stylesheets=[self.stylesheet] if self.stylesheet else None,
            font_families=[self.font_family],
            lang="zh-CN",
        )

    def _register_selected_font(
        self,
        *,
        bundled_font_path: str | Path,
        configured_font_path: str | Path | None,
    ) -> str:
        bundled_path = Path(bundled_font_path).expanduser()
        configured_path = (
            Path(configured_font_path).expanduser()
            if configured_font_path and str(configured_font_path).strip()
            else None
        )
        if configured_path is not None:
            try:
                font_bytes = configured_path.read_bytes()
                return self._register_font(font_bytes)
            except (OSError, RenderingError):
                logger.warning(
                    "Configured render.font_path could not be registered; using bundled font"
                )
        try:
            font_bytes = bundled_path.read_bytes()
        except OSError as exc:
            raise RenderingError("内置字体文件无法读取") from exc
        try:
            return self._register_font(font_bytes)
        except RenderingError as exc:
            raise RenderingError("内置字体注册失败") from exc

    def _register_font(self, font_bytes: bytes) -> str:
        if not font_bytes:
            raise RenderingError("字体文件为空")
        try:
            families = self._renderer.register_font(font_bytes)
        except Exception as exc:
            raise RenderingError("字体注册失败") from exc
        if not isinstance(families, list) or not families:
            raise RenderingError("字体注册结果为空")
        family = families[0]
        if not isinstance(family, dict) or not isinstance(family.get("name"), str):
            raise RenderingError("字体注册结果无名称")
        return family["name"]
