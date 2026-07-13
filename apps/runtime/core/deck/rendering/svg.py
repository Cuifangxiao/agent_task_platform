"""Render a DeckSpec page as a restricted, PowerPoint-safe SVG document."""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

from core.deck.rendering.charts import normalize_chart
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, DeckSpec, SlideSpec, ThemeTokens


SVG_ALLOWED_TAGS = frozenset({"svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "image"})


class SVGSlideRenderer:
    """Create deterministic SVG pages from structured slide content.

    The generated subset intentionally avoids foreignObject, CSS, filters, masks,
    external resources, and arbitrary paths. This makes each page safe to inspect
    and straightforward to translate into native DrawingML shapes.
    """

    def render(self, deck: DeckSpec, slide: SlideSpec) -> str:
        canvas = _Canvas(deck.theme, slide)
        canvas.base()
        renderer = {
            "cover": _cover,
            "section": _section,
            "agenda": _agenda,
            "content-basic": _content_basic,
            "kpi-cards": _kpi_cards,
            "timeline": _timeline,
            "steps": _steps,
            "comparison": _comparison,
            "quadrant": _summary_grid,
            "chart-takeaway": _chart_takeaway,
            "data-table": _data_table,
            "quote": _quote,
            "summary": _summary_grid,
            "end": _end,
        }.get(slide.component, _content_basic)
        renderer(canvas, slide)
        canvas.footer()
        return canvas.document()


def svg_preview_html(svg: str, *, language: str = "zh") -> str:
    """Wrap a page SVG in the same fixed canvas used by browser QA and PNG previews."""

    return "\n".join(
        [
            "<!doctype html>",
            f'<html lang="{_attr(language or "zh")}">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=1280,height=720,initial-scale=1">',
            "<style>",
            "*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden}",
            ".slide{position:relative;width:1280px;height:720px;overflow:hidden}",
            ".slide>svg{display:block;width:1280px;height:720px}",
            "</style>",
            "</head><body>",
            '<section class="slide">',
            svg,
            "</section>",
            "</body></html>",
        ]
    )


@dataclass(frozen=True)
class _Palette:
    bg: str
    surface: str
    ink: str
    muted: str
    accent: str
    accent2: str
    line: str


class _Canvas:
    def __init__(self, theme: ThemeTokens, slide: SlideSpec) -> None:
        palette = theme.palette
        self.palette = _Palette(
            bg=_hex(palette.get("bg"), "#F7F3EA"),
            surface=_hex(palette.get("surface"), "#FFFFFF"),
            ink=_hex(palette.get("ink"), "#14213D"),
            muted=_hex(palette.get("muted"), "#58677A"),
            accent=_hex(palette.get("accent"), "#2A9D8F"),
            accent2=_hex(palette.get("accent2"), "#E76F51"),
            line=_hex(palette.get("line"), "#D8D3C8"),
        )
        self.display_font = str(theme.fonts.get("display") or "Noto Sans SC").split(",", 1)[0].strip(" '\"")
        self.body_font = str(theme.fonts.get("body") or "Noto Sans SC").split(",", 1)[0].strip(" '\"")
        self.display_weight = int(theme.fonts.get("display_weight") or 700)
        self.h1 = max(36, int(theme.type_scale.get("h1") or 44))
        self.h2 = max(26, int(theme.type_scale.get("h2") or 30))
        self.body = max(16, int(theme.type_scale.get("body") or 18))
        self.caption = max(11, int(theme.type_scale.get("caption") or 13))
        self.radius = max(4, min(28, int(theme.radius or 12)))
        self.slide = slide
        self.parts: list[str] = []

    def document(self) -> str:
        metadata = (
            f'data-component="{_attr(self.slide.component)}" '
            f'data-layout-variant="{_attr(self.slide.layout_variant)}" '
            f'data-page="{self.slide.index}" '
            f'data-spec-hash="{_attr(self.slide.spec_hash)}"'
        )
        return "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}" '
                f'viewBox="0 0 {CANVAS_WIDTH} {CANVAS_HEIGHT}" {metadata}>',
                *self.parts,
                "</svg>",
            ]
        )

    def base(self) -> None:
        self.rect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT, self.palette.bg, element_id="page-background")
        self.rect(0, 0, CANVAS_WIDTH, 8, self.palette.accent, element_id="top-accent")
        self.rect(68, 674, 138, 3, self.palette.accent, element_id="footer-accent")
        tint = self.palette.surface
        self.circle(1188, 72, 30, tint, element_id="motif-circle")
        self.polygon([(1168, 54), (1190, 42), (1212, 60), (1188, 78)], self.palette.accent2, element_id="motif-diamond")

    def footer(self) -> None:
        self.text(
            str(self.slide.index),
            1150,
            646,
            58,
            30,
            size=self.caption,
            color=self.palette.muted,
            align="right",
            editable=False,
            element_id="page-number",
        )

    def begin(self, element_id: str) -> None:
        self.parts.append(f'<g id="{_attr(element_id)}">')

    def end(self) -> None:
        self.parts.append("</g>")

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        fill: str,
        *,
        stroke: str = "none",
        stroke_width: float = 0,
        radius: float = 0,
        element_id: str = "",
    ) -> None:
        attrs = _id_attr(element_id)
        self.parts.append(
            f'<rect{attrs} x="{_num(x)}" y="{_num(y)}" width="{_num(width)}" height="{_num(height)}" '
            f'rx="{_num(radius)}" fill="{_attr(fill)}" stroke="{_attr(stroke)}" stroke-width="{_num(stroke_width)}"/>'
        )

    def panel(self, x: float, y: float, width: float, height: float, *, fill: str | None = None, element_id: str = "") -> None:
        self.rect(
            x,
            y,
            width,
            height,
            fill or self.palette.surface,
            stroke=self.palette.line,
            stroke_width=1.5,
            radius=self.radius,
            element_id=element_id,
        )

    def circle(self, cx: float, cy: float, radius: float, fill: str, *, stroke: str = "none", stroke_width: float = 0, element_id: str = "") -> None:
        self.parts.append(
            f'<circle{_id_attr(element_id)} cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(radius)}" '
            f'fill="{_attr(fill)}" stroke="{_attr(stroke)}" stroke-width="{_num(stroke_width)}"/>'
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, *, color: str, width: float = 2, element_id: str = "") -> None:
        self.parts.append(
            f'<line{_id_attr(element_id)} x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}" '
            f'stroke="{_attr(color)}" stroke-width="{_num(width)}" stroke-linecap="round"/>'
        )

    def polyline(self, points: Iterable[tuple[float, float]], *, color: str, width: float = 3, element_id: str = "") -> None:
        value = " ".join(f"{_num(x)},{_num(y)}" for x, y in points)
        self.parts.append(
            f'<polyline{_id_attr(element_id)} points="{value}" fill="none" stroke="{_attr(color)}" '
            f'stroke-width="{_num(width)}" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    def polygon(self, points: Iterable[tuple[float, float]], fill: str, *, stroke: str = "none", element_id: str = "") -> None:
        value = " ".join(f"{_num(x)},{_num(y)}" for x, y in points)
        self.parts.append(f'<polygon{_id_attr(element_id)} points="{value}" fill="{_attr(fill)}" stroke="{_attr(stroke)}"/>')

    def text(
        self,
        value: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        size: float,
        color: str,
        bold: bool = False,
        align: str = "left",
        valign: str = "top",
        font: str = "body",
        editable: bool = True,
        element_id: str = "",
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return
        family = self.display_font if font == "display" else self.body_font
        fitted_size, lines = _fit_lines(text, width, height, float(size), min_size=12.0)
        line_height = fitted_size * 1.25
        total_height = len(lines) * line_height
        if valign == "middle":
            baseline = y + max(0, (height - total_height) / 2) + fitted_size
        elif valign == "bottom":
            baseline = y + max(fitted_size, height - total_height + fitted_size)
        else:
            baseline = y + fitted_size
        anchor = {"center": "middle", "right": "end"}.get(align, "start")
        text_x = x + width / 2 if align == "center" else x + width if align == "right" else x
        attrs = [
            _id_attr(element_id).strip(),
            f'x="{_num(text_x)}"',
            f'y="{_num(baseline)}"',
            f'fill="{_attr(color)}"',
            f'font-family="{_attr(family)}"',
            f'font-size="{_num(fitted_size)}"',
            f'font-weight="{700 if bold else 400}"',
            f'text-anchor="{anchor}"',
            f'data-box="{_num(x)},{_num(y)},{_num(width)},{_num(height)}"',
            f'data-value="{_attr(text)}"',
            f'data-align="{_attr(align)}"',
            f'data-valign="{_attr(valign)}"',
            f'data-font-size="{_num(fitted_size)}"',
            f'data-font-family="{_attr(family)}"',
            f'data-bold="{str(bool(bold)).lower()}"',
            f'data-editable="{str(bool(editable)).lower()}"',
        ]
        if editable:
            attrs.append('data-text="true"')
        tspans = "".join(
            f'<tspan x="{_num(text_x)}" y="{_num(baseline + index * line_height)}">{_text(line)}</tspan>'
            for index, line in enumerate(lines)
        )
        self.parts.append(f"<text {' '.join(item for item in attrs if item)}>{tspans}</text>")

    def header(self, slide: SlideSpec, *, subtitle: str = "") -> None:
        self.text(
            slide.title,
            70,
            54,
            1060,
            94,
            size=self.h2 + 3,
            color=self.palette.ink,
            bold=True,
            font="display",
            element_id="title",
        )
        if subtitle:
            self.text(subtitle, 72, 135, 940, 45, size=self.body, color=self.palette.muted, element_id="subtitle")
        self.rect(70, 161 if subtitle else 145, 96, 5, self.palette.accent, radius=3, element_id="title-rule")


def _cover(canvas: _Canvas, slide: SlideSpec) -> None:
    palette = canvas.palette
    canvas.begin("cover-layout")
    canvas.rect(925, 70, 286, 574, palette.accent, radius=canvas.radius * 1.4, element_id="cover-panel")
    canvas.rect(970, 116, 92, 404, palette.accent2, radius=canvas.radius, element_id="cover-stripe")
    canvas.circle(1120, 530, 58, palette.surface, element_id="cover-orbit")
    canvas.text(slide.content.get("eyebrow") or "PRESENTATION", 72, 104, 720, 38, size=canvas.caption + 2, color=palette.accent, bold=True, element_id="eyebrow")
    canvas.text(slide.title, 72, 170, 775, 238, size=canvas.h1 + 10, color=palette.ink, bold=True, font="display", valign="middle", element_id="title")
    canvas.text(slide.content.get("subtitle"), 76, 448, 710, 108, size=canvas.body + 3, color=palette.muted, element_id="subtitle")
    canvas.rect(76, 588, 210, 6, palette.accent2, radius=3, element_id="cover-rule")
    canvas.end()


def _end(canvas: _Canvas, slide: SlideSpec) -> None:
    palette = canvas.palette
    canvas.begin("end-layout")
    canvas.rect(0, 160, 1280, 400, palette.accent, element_id="end-band")
    canvas.circle(1065, 360, 132, palette.accent2, element_id="end-circle")
    canvas.text(slide.title, 88, 210, 820, 150, size=canvas.h1 + 8, color=_contrast_text(palette.accent, palette.bg, palette.ink), bold=True, font="display", valign="middle", element_id="title")
    canvas.text(slide.content.get("subtitle"), 92, 390, 740, 84, size=canvas.body + 3, color=_contrast_text(palette.accent, palette.bg, palette.ink), element_id="subtitle")
    canvas.end()


def _section(canvas: _Canvas, slide: SlideSpec) -> None:
    palette = canvas.palette
    canvas.begin("section-layout")
    canvas.rect(0, 164, 1280, 382, palette.accent, element_id="section-band")
    canvas.rect(72, 202, 16, 254, palette.accent2, radius=8, element_id="section-marker")
    foreground = _contrast_text(palette.accent, palette.bg, palette.ink)
    canvas.text(slide.title, 124, 222, 980, 138, size=canvas.h1 + 2, color=foreground, bold=True, font="display", valign="middle", element_id="title")
    canvas.text(slide.content.get("subtitle"), 128, 388, 880, 80, size=canvas.body + 2, color=foreground, element_id="subtitle")
    canvas.end()


def _content_basic(canvas: _Canvas, slide: SlideSpec) -> None:
    palette = canvas.palette
    body = str(slide.content.get("body") or "").strip()
    bullets = _items(slide.content.get("bullets"))
    canvas.header(slide)
    canvas.begin("content-layout")
    canvas.panel(68, 188, 742, 426, element_id="evidence-panel")
    side_fill = palette.surface
    canvas.panel(842, 188, 368, 426, fill=side_fill, element_id="takeaway-panel")
    if bullets:
        slot = 352 / max(1, len(bullets))
        for index, item in enumerate(bullets):
            y = 226 + index * slot
            canvas.circle(112, y + 17, 8, palette.accent2, element_id=f"bullet-{index + 1}-marker")
            canvas.text(item, 140, y, 612, max(54, slot - 12), size=canvas.body + 1, color=palette.ink, bold=index == 0, element_id=f"bullet-{index + 1}")
    else:
        canvas.text(body or slide.title, 112, 244, 620, 250, size=canvas.h2, color=palette.ink, bold=True, valign="middle", element_id="body")
    if body and bullets:
        canvas.text("KEY TAKEAWAY", 886, 234, 270, 30, size=canvas.caption, color=palette.accent, bold=True, element_id="takeaway-label", editable=False)
        canvas.text(body, 886, 288, 272, 230, size=canvas.body + 2, color=palette.ink, bold=True, valign="middle", element_id="takeaway")
    else:
        canvas.text(f"{slide.index:02d}", 886, 250, 270, 210, size=96, color=palette.accent, bold=True, font="display", valign="middle", element_id="visual-index", editable=False)
    canvas.end()


def _agenda(canvas: _Canvas, slide: SlideSpec) -> None:
    items = _items(slide.content.get("items"))
    canvas.header(slide)
    columns = 3 if len(items) >= 5 else 2
    rows = max(1, math.ceil(len(items) / columns))
    gap = 20
    width = (1140 - gap * (columns - 1)) / columns
    height = min(178, (432 - gap * (rows - 1)) / rows)
    for index, item in enumerate(items):
        col, row = index % columns, index // columns
        x, y = 70 + col * (width + gap), 188 + row * (height + gap)
        canvas.begin(f"agenda-{index + 1}")
        canvas.panel(x, y, width, height, element_id=f"agenda-{index + 1}-panel")
        canvas.rect(x, y, 10, height, canvas.palette.accent if index % 2 == 0 else canvas.palette.accent2, radius=5, element_id=f"agenda-{index + 1}-accent")
        canvas.text(f"{index + 1:02d}", x + 30, y + 26, 64, 45, size=canvas.h2, color=canvas.palette.accent, bold=True, editable=False, element_id=f"agenda-{index + 1}-number")
        canvas.text(item, x + 108, y + 26, width - 136, height - 48, size=canvas.body + 2, color=canvas.palette.ink, bold=True, valign="middle", element_id=f"agenda-{index + 1}-text")
        canvas.end()


def _kpi_cards(canvas: _Canvas, slide: SlideSpec) -> None:
    cards = [item for item in slide.content.get("cards") or [] if isinstance(item, dict)][:4]
    canvas.header(slide)
    count = max(1, len(cards))
    gap = 22
    width = (1140 - gap * (count - 1)) / count
    for index, card in enumerate(cards):
        x, y, height = 70 + index * (width + gap), 205, 390
        canvas.begin(f"metric-{index + 1}")
        canvas.panel(x, y, width, height, element_id=f"metric-{index + 1}-panel")
        canvas.rect(x, y, width, 12, canvas.palette.accent if index % 2 == 0 else canvas.palette.accent2, radius=6, element_id=f"metric-{index + 1}-accent")
        canvas.text(card.get("value"), x + 28, y + 62, width - 56, 105, size=canvas.h1 + 4, color=canvas.palette.accent, bold=True, align="center", font="display", valign="middle", element_id=f"metric-{index + 1}-value")
        canvas.text(card.get("label"), x + 28, y + 190, width - 56, 62, size=canvas.body + 2, color=canvas.palette.ink, bold=True, align="center", valign="middle", element_id=f"metric-{index + 1}-label")
        canvas.line(x + 42, y + 274, x + width - 42, y + 274, color=canvas.palette.line, width=1.5, element_id=f"metric-{index + 1}-rule")
        canvas.text(card.get("caption"), x + 30, y + 296, width - 60, 62, size=canvas.caption + 1, color=canvas.palette.muted, align="center", valign="middle", element_id=f"metric-{index + 1}-caption")
        canvas.end()


def _timeline(canvas: _Canvas, slide: SlideSpec) -> None:
    items = _items(slide.content.get("items"))[:6]
    canvas.header(slide)
    if not items:
        return
    left, right, axis_y = 110.0, 1170.0, 374.0
    canvas.line(left, axis_y, right, axis_y, color=canvas.palette.line, width=5, element_id="timeline-axis")
    step = (right - left) / max(1, len(items) - 1)
    for index, item in enumerate(items):
        x = left + index * step
        above = index % 2 == 0
        canvas.circle(x, axis_y, 17, canvas.palette.accent if above else canvas.palette.accent2, stroke=canvas.palette.bg, stroke_width=5, element_id=f"timeline-{index + 1}-marker")
        box_y = 205 if above else 420
        canvas.panel(max(70, x - 100), box_y, 200, 118, element_id=f"timeline-{index + 1}-panel")
        canvas.text(item, max(86, x - 84), box_y + 18, 168, 82, size=canvas.body, color=canvas.palette.ink, bold=True, align="center", valign="middle", element_id=f"timeline-{index + 1}-text")
        canvas.line(x, box_y + (118 if above else 0), x, axis_y + (-20 if above else 20), color=canvas.palette.line, width=2, element_id=f"timeline-{index + 1}-connector")


def _steps(canvas: _Canvas, slide: SlideSpec) -> None:
    items = _items(slide.content.get("steps") or slide.content.get("items"))[:5]
    canvas.header(slide)
    count = max(1, len(items))
    gap = 18
    width = (1140 - gap * (count - 1)) / count
    for index, item in enumerate(items):
        x, y = 70 + index * (width + gap), 226
        step_color = canvas.palette.accent if index % 2 == 0 else canvas.palette.accent2
        if index:
            canvas.line(x - gap + 2, 386, x - 2, 386, color=canvas.palette.accent, width=4, element_id=f"step-{index}-connector")
        canvas.begin(f"step-{index + 1}")
        canvas.panel(x, y, width, 324, element_id=f"step-{index + 1}-panel")
        canvas.circle(x + width / 2, y + 70, 34, step_color, element_id=f"step-{index + 1}-number-bg")
        canvas.text(str(index + 1), x + width / 2 - 25, y + 44, 50, 52, size=canvas.h2, color=_contrast_text(step_color, canvas.palette.bg, canvas.palette.ink), bold=True, align="center", valign="middle", editable=False, element_id=f"step-{index + 1}-number")
        canvas.text(item, x + 24, y + 142, width - 48, 130, size=canvas.body + 1, color=canvas.palette.ink, bold=True, align="center", valign="middle", element_id=f"step-{index + 1}-text")
        canvas.end()


def _comparison(canvas: _Canvas, slide: SlideSpec) -> None:
    canvas.header(slide)
    panels = [
        ("left", slide.content.get("left_title") or "Current", _items(slide.content.get("left")), canvas.palette.accent),
        ("right", slide.content.get("right_title") or "Target", _items(slide.content.get("right")), canvas.palette.accent2),
    ]
    for index, (key, title, items, accent) in enumerate(panels):
        x, y, width, height = 70 + index * 582, 190, 558, 420
        canvas.begin(f"comparison-{key}")
        canvas.panel(x, y, width, height, element_id=f"comparison-{key}-panel")
        canvas.rect(x, y, width, 72, accent, radius=canvas.radius, element_id=f"comparison-{key}-header")
        canvas.rect(x, y + 54, width, 18, accent, element_id=f"comparison-{key}-header-square")
        canvas.text(title, x + 30, y + 15, width - 60, 45, size=canvas.h2 - 2, color=_contrast_text(accent, canvas.palette.bg, canvas.palette.ink), bold=True, element_id=f"comparison-{key}-title")
        slot = 292 / max(1, len(items))
        for item_index, item in enumerate(items[:5]):
            item_y = y + 102 + item_index * slot
            canvas.circle(x + 46, item_y + 13, 7, accent, element_id=f"comparison-{key}-{item_index + 1}-marker")
            canvas.text(item, x + 68, item_y, width - 102, max(46, slot - 8), size=canvas.body, color=canvas.palette.ink, element_id=f"comparison-{key}-{item_index + 1}")
        canvas.end()


def _summary_grid(canvas: _Canvas, slide: SlideSpec) -> None:
    items = _items(slide.content.get("items"))[:6]
    canvas.header(slide)
    columns = 2 if len(items) <= 4 else 3
    rows = max(1, math.ceil(len(items) / columns))
    gap = 22
    width = (1140 - gap * (columns - 1)) / columns
    height = (410 - gap * (rows - 1)) / rows
    for index, item in enumerate(items):
        col, row = index % columns, index // columns
        x, y = 70 + col * (width + gap), 192 + row * (height + gap)
        canvas.begin(f"summary-{index + 1}")
        canvas.panel(x, y, width, height, fill=canvas.palette.surface, element_id=f"summary-{index + 1}-panel")
        canvas.rect(x, y, 12, height, canvas.palette.accent if index % 2 == 0 else canvas.palette.accent2, radius=6, element_id=f"summary-{index + 1}-accent")
        canvas.text(f"{index + 1:02d}", x + 36, y + 28, 60, 42, size=canvas.h2 - 2, color=canvas.palette.accent, bold=True, editable=False, element_id=f"summary-{index + 1}-number")
        canvas.text(item, x + 112, y + 26, width - 146, height - 52, size=canvas.body + 1, color=canvas.palette.ink, bold=True, valign="middle", element_id=f"summary-{index + 1}-text")
        canvas.end()


def _chart_takeaway(canvas: _Canvas, slide: SlideSpec) -> None:
    chart = normalize_chart(slide.chart)
    if chart is None:
        _content_basic(canvas, slide)
        return
    canvas.header(slide)
    canvas.panel(68, 188, 780, 426, element_id="chart-panel")
    canvas.panel(874, 188, 336, 426, fill=canvas.palette.surface, element_id="chart-takeaway-panel")
    canvas.text("INSIGHT", 910, 230, 250, 28, size=canvas.caption, color=canvas.palette.accent, bold=True, editable=False, element_id="chart-takeaway-label")
    canvas.text(slide.content.get("takeaway"), 910, 284, 252, 244, size=canvas.body + 2, color=canvas.palette.ink, bold=True, valign="middle", element_id="chart-takeaway")
    if chart["type"] == "line":
        _line_chart(canvas, chart, 108, 230, 700, 320)
    elif chart["type"] == "pie":
        _pie_chart(canvas, chart, 118, 226, 680, 330)
    else:
        _bar_chart(canvas, chart, 108, 230, 700, 320)


def _bar_chart(canvas: _Canvas, chart: dict[str, Any], x: float, y: float, width: float, height: float) -> None:
    series = chart["series"][:8]
    values = [float(item["value"]) for item in series]
    low, high = min(0.0, min(values)), max(0.0, max(values))
    span = high - low or 1.0
    plot_top, plot_bottom = y + 24, y + height - 48
    plot_height = plot_bottom - plot_top
    baseline = plot_top + (high / span) * plot_height
    canvas.line(x, baseline, x + width, baseline, color=canvas.palette.line, width=2, element_id="chart-axis")
    slot = width / max(1, len(series))
    bar_width = min(58.0, slot * 0.54)
    for index, item in enumerate(series):
        value = float(item["value"])
        value_y = plot_top + ((high - value) / span) * plot_height
        bar_height = max(4.0, abs(value_y - baseline))
        bar_x = x + index * slot + (slot - bar_width) / 2
        bar_y = min(value_y, baseline)
        color = canvas.palette.accent if index % 2 == 0 else canvas.palette.accent2
        canvas.rect(bar_x, bar_y, bar_width, bar_height, color, radius=7, element_id=f"bar-{index + 1}")
        label_inside = value < 0 and bar_height >= 34
        label_y = bar_y + bar_height - 29 if label_inside else max(y, bar_y - 29)
        label_color = _contrast_text(color, canvas.palette.bg, canvas.palette.ink) if label_inside else canvas.palette.ink
        canvas.text(_chart_value(item["value"], chart["unit"]), bar_x - 18, label_y, bar_width + 36, 28, size=canvas.body, color=label_color, bold=True, align="center", element_id=f"bar-{index + 1}-value")
        canvas.text(item["label"], x + index * slot + 4, plot_bottom + 14, slot - 8, 46, size=canvas.body, color=canvas.palette.muted, align="center", element_id=f"bar-{index + 1}-label")


def _line_chart(canvas: _Canvas, chart: dict[str, Any], x: float, y: float, width: float, height: float) -> None:
    series = chart["series"][:8]
    values = [float(item["value"]) for item in series]
    low, high = min(values), max(values)
    span = high - low or 1
    baseline = y + height - 50
    canvas.line(x, baseline, x + width, baseline, color=canvas.palette.line, width=2, element_id="chart-axis")
    step = width / max(1, len(series) - 1)
    points = [(x + index * step, y + 24 + (height - 116) * (1 - (value - low) / span)) for index, value in enumerate(values)]
    if len(points) > 1:
        canvas.polyline(points, color=canvas.palette.accent, width=5, element_id="line-series")
    for index, ((px, py), item) in enumerate(zip(points, series, strict=False)):
        canvas.circle(px, py, 8, canvas.palette.accent2, stroke=canvas.palette.bg, stroke_width=3, element_id=f"line-point-{index + 1}")
        canvas.text(_chart_value(item["value"], chart["unit"]), px - 46, max(y, py - 34), 92, 28, size=canvas.body, color=canvas.palette.ink, bold=True, align="center", element_id=f"line-{index + 1}-value")
        canvas.text(item["label"], px - 52, baseline + 14, 104, 46, size=canvas.body, color=canvas.palette.muted, align="center", element_id=f"line-{index + 1}-label")


def _pie_chart(canvas: _Canvas, chart: dict[str, Any], x: float, y: float, width: float, height: float) -> None:
    series = chart["series"][:6]
    values = [max(0.0, float(item["value"])) for item in series]
    total = sum(values)
    if total <= 0:
        _bar_chart(canvas, chart, x, y, width, height)
        return
    cx, cy, radius = x + 185, y + height / 2, min(128.0, height * 0.39)
    colors = [canvas.palette.accent, canvas.palette.accent2, canvas.palette.muted, canvas.palette.ink, canvas.palette.line, canvas.palette.surface]
    start = -math.pi / 2
    for index, (item, value) in enumerate(zip(series, values, strict=False)):
        end = start + (value / total) * math.tau
        points = [(cx, cy)] + [
            (cx + radius * math.cos(start + (end - start) * step / 24), cy + radius * math.sin(start + (end - start) * step / 24))
            for step in range(25)
        ]
        canvas.polygon(points, colors[index % len(colors)], stroke=canvas.palette.bg, element_id=f"pie-slice-{index + 1}")
        legend_y = y + 32 + index * 50
        canvas.rect(x + 390, legend_y, 18, 18, colors[index % len(colors)], radius=4, element_id=f"pie-legend-{index + 1}-marker")
        canvas.text(item["label"], x + 422, legend_y - 3, 170, 28, size=canvas.body, color=canvas.palette.ink, element_id=f"pie-legend-{index + 1}-label")
        canvas.text(_chart_value(item["value"], chart["unit"]), x + 592, legend_y - 3, 86, 28, size=canvas.body, color=canvas.palette.muted, bold=True, align="right", element_id=f"pie-legend-{index + 1}-value")
        start = end


def _data_table(canvas: _Canvas, slide: SlideSpec) -> None:
    columns = _items(slide.content.get("columns"))
    rows = slide.content.get("rows") or []
    normalized_rows: list[list[str]] = []
    for row in rows[:6]:
        values = list(row.values()) if isinstance(row, dict) else row if isinstance(row, list) else [row]
        normalized_rows.append([str(value or "").strip() for value in values])
    column_count = max(1, len(columns), max((len(row) for row in normalized_rows), default=0))
    columns = (columns + [f"Column {index + 1}" for index in range(column_count)])[:column_count]
    canvas.header(slide)
    x, y, width, height = 70.0, 192.0, 1140.0, 414.0
    col_width = width / column_count
    row_height = height / max(2, len(normalized_rows) + 1)
    canvas.panel(x, y, width, height, element_id="table-panel")
    canvas.rect(x, y, width, row_height, canvas.palette.accent, radius=canvas.radius, element_id="table-header-bg")
    canvas.rect(x, y + row_height - canvas.radius, width, canvas.radius, canvas.palette.accent, element_id="table-header-square")
    for col, label in enumerate(columns):
        cell_x = x + col * col_width
        canvas.text(label, cell_x + 16, y + 10, col_width - 32, row_height - 20, size=canvas.body, color=_contrast_text(canvas.palette.accent, canvas.palette.bg, canvas.palette.ink), bold=True, valign="middle", element_id=f"table-header-{col + 1}")
        if col:
            canvas.line(cell_x, y, cell_x, y + height, color=canvas.palette.line, width=1, element_id=f"table-col-{col}")
    for row_index, row in enumerate(normalized_rows):
        row_y = y + (row_index + 1) * row_height
        if row_index % 2:
            canvas.rect(x, row_y, width, row_height, canvas.palette.surface, element_id=f"table-row-{row_index + 1}-bg")
        canvas.line(x, row_y, x + width, row_y, color=canvas.palette.line, width=1, element_id=f"table-row-{row_index + 1}-rule")
        for col in range(column_count):
            value = row[col] if col < len(row) else ""
            canvas.text(value, x + col * col_width + 16, row_y + 8, col_width - 32, row_height - 16, size=canvas.body, color=canvas.palette.ink, valign="middle", element_id=f"table-{row_index + 1}-{col + 1}")


def _quote(canvas: _Canvas, slide: SlideSpec) -> None:
    canvas.header(slide)
    canvas.begin("quote-layout")
    canvas.panel(134, 198, 1010, 382, fill=canvas.palette.surface, element_id="quote-panel")
    canvas.rect(134, 198, 18, 382, canvas.palette.accent, radius=9, element_id="quote-accent")
    canvas.text("“", 192, 222, 100, 102, size=92, color=canvas.palette.accent2, bold=True, font="display", editable=False, element_id="quote-mark")
    canvas.text(slide.content.get("quote"), 286, 238, 744, 210, size=canvas.h2, color=canvas.palette.ink, bold=True, font="display", valign="middle", element_id="quote-text")
    canvas.text(slide.content.get("speaker"), 292, 480, 680, 44, size=canvas.body, color=canvas.palette.muted, align="right", element_id="quote-speaker")
    canvas.end()


def _fit_lines(text: str, width: float, height: float, size: float, *, min_size: float) -> tuple[float, list[str]]:
    candidate = max(min_size, size)
    while candidate >= min_size:
        lines = _wrap_lines(text, max(1.0, width / candidate))
        if len(lines) * candidate * 1.25 <= height + 1:
            return candidate, lines
        candidate -= 1
    return min_size, _wrap_lines(text, max(1.0, width / min_size))


def _wrap_lines(text: str, em_width: float) -> list[str]:
    raw_lines = str(text or "").replace("\r", "").split("\n")
    result: list[str] = []
    for raw in raw_lines:
        value = raw.strip()
        if not value:
            result.append("")
            continue
        current: list[str] = []
        current_width = 0.0
        last_break = -1
        for char in value:
            char_width = _char_width(char)
            if current and current_width + char_width > em_width:
                split_at = last_break + 1 if last_break >= max(0, len(current) // 2) else len(current)
                line = "".join(current[:split_at]).strip()
                if line:
                    result.append(line)
                current = current[split_at:]
                current_width = sum(_char_width(item) for item in current)
                last_break = -1
            current.append(char)
            current_width += char_width
            if char.isspace() or char in "，。；：、,.!?;:/-":
                last_break = len(current) - 1
        if current:
            result.append("".join(current).strip())
    return result or [""]


def _char_width(char: str) -> float:
    if char.isspace():
        return 0.34
    if ord(char) >= 0x2E80:
        return 1.0
    if char.isupper() or char.isdigit():
        return 0.64
    if char in "MW@#%&":
        return 0.82
    if char in "il.,'|!":
        return 0.3
    return 0.54


def _items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _chart_value(value: Any, unit: str) -> str:
    try:
        number = float(value)
        rendered = str(int(number)) if number.is_integer() else str(round(number, 1))
    except (TypeError, ValueError):
        rendered = str(value or "")
    return f"{rendered}{unit or ''}"


def _hex(value: Any, fallback: str) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"#[0-9A-F]{6}", text) else fallback


def _contrast_text(background: str, first: str, second: str) -> str:
    background_luminance = _luminance(background)
    first_ratio = _contrast_ratio(background_luminance, _luminance(first))
    second_ratio = _contrast_ratio(background_luminance, _luminance(second))
    return first if first_ratio >= second_ratio else second


def _luminance(color: str) -> float:
    channels = []
    for index in (1, 3, 5):
        value = int(color[index : index + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first: float, second: float) -> float:
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _num(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")


def _text(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _attr(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _id_attr(value: str) -> str:
    return f' id="{_attr(value)}"' if value else ""


__all__ = ["SVG_ALLOWED_TAGS", "SVGSlideRenderer", "svg_preview_html"]
