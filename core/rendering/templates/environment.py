"""Cached, autoescaping Jinja environment for Beszel markup."""

from __future__ import annotations

from datetime import tzinfo
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template

from ...errors import RenderingError
from ..models import HistoryDocument, OverviewDocument, StatusDocument
from .charts import build_chart_view


class BeszelTemplateRenderer:
    """Own and reuse compiled HTML templates and their local stylesheet."""

    def __init__(self) -> None:
        directory = Path(__file__).resolve().parent
        try:
            self.stylesheet = (directory / "beszel.css").read_text(encoding="utf-8")
            environment = Environment(
                loader=FileSystemLoader(directory),
                autoescape=True,
                undefined=StrictUndefined,
                auto_reload=False,
                cache_size=8,
                trim_blocks=True,
                lstrip_blocks=True,
            )
            environment.filters.update(
                color_css=_color_css,
                coord=_coordinate,
                percentage_width=_percentage_width,
            )
            self._overview = environment.get_template("overview.html.j2")
            self._status = environment.get_template("status.html.j2")
            self._history = environment.get_template("history.html.j2")
        except Exception as exc:
            raise RenderingError("图片模板初始化失败") from exc

    def render_overview(self, document: OverviewDocument) -> str:
        return self._render(self._overview, document=document)

    def render_status(self, document: StatusDocument) -> str:
        return self._render(self._status, document=document)

    def render_history(self, document: HistoryDocument, *, timezone: tzinfo) -> str:
        charts = tuple(
            build_chart_view(card, timezone=timezone) for card in document.cards
        )
        return self._render(self._history, document=document, charts=charts)

    @staticmethod
    def _render(template: Template, **context: Any) -> str:
        try:
            markup = template.render(**context)
        except Exception as exc:
            raise RenderingError("图片模板生成失败") from exc
        if not markup:
            raise RenderingError("图片模板生成结果为空")
        return markup


def _color_css(color: tuple[int, int, int]) -> str:
    return f"rgb({color[0]}, {color[1]}, {color[2]})"


def _coordinate(value: float | int) -> str:
    return f"{float(value):.2f}"


def _percentage_width(value: float | None, maximum: float) -> str:
    if value is None or maximum <= 0:
        return "0"
    clamped = min(maximum, max(0.0, float(value)))
    return f"{clamped / maximum * 100:.2f}%"
