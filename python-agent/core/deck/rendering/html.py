"""Render DeckSpec slides to self-contained HTML documents."""

from __future__ import annotations

import html
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.deck.rendering.charts import chart_is_valid, render_chart_svg
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, DeckSpec, SlideSpec, ThemeTokens, default_theme


DESIGN_SYSTEM_DIR = Path(__file__).resolve().parent.parent / "design_system"
FONT_FALLBACKS = ('"Noto Sans SC"', '"Noto Sans CJK SC"', '"PingFang SC"', '"Microsoft YaHei"', "sans-serif")


class HTMLSlideRenderer:
    def __init__(self, design_system_dir: Path | None = None) -> None:
        self._design_system_dir = design_system_dir or DESIGN_SYSTEM_DIR
        self._css = (self._design_system_dir / "deck.css").read_text(encoding="utf-8")

    def render(self, deck: DeckSpec, slide: SlideSpec) -> str:
        body = self._render_component(slide)
        return "\n".join(
            [
                "<!doctype html>",
                '<html lang="' + _escape_attr(deck.language or "zh") + '">',
                "<head>",
                '<meta charset="utf-8">',
                '<meta name="viewport" content="width=1280,height=720,initial-scale=1">',
                "<style>",
                self._theme_css(deck.theme),
                self._css,
                "</style>",
                "</head>",
                "<body>",
                body,
                "</body>",
                "</html>",
            ]
        )

    def render_cover_sample(self, title: str, narrative: str, theme: ThemeTokens) -> str:
        deck = DeckSpec(
            title=title,
            narrative=narrative,
            audience="",
            goal="",
            language="zh",
            theme=theme,
            slides=[],
        )
        return self.render(
            deck,
            SlideSpec(
                index=1,
                role="hook",
                component="cover",
                title=title or "Untitled presentation",
                content={"eyebrow": theme.name, "subtitle": narrative or theme.motif, "meta": ""},
            ),
        )

    def _render_component(self, slide: SlideSpec) -> str:
        component = slide.component
        if component == "cover":
            return _cover(slide)
        if component == "section":
            return _section(slide)
        if component == "agenda":
            return _agenda(slide)
        if component == "kpi-cards":
            return _kpi_cards(slide)
        if component == "timeline":
            return _timeline(slide)
        if component == "steps":
            return _steps(slide)
        if component == "comparison":
            return _comparison(slide)
        if component == "quadrant":
            return _summary_grid(slide)
        if component == "chart-takeaway":
            return _chart_takeaway(slide)
        if component == "data-table":
            return _data_table(slide)
        if component == "quote":
            return _quote(slide)
        if component == "summary":
            return _summary_grid(slide)
        if component == "end":
            return _end(slide)
        return _content_basic(slide)

    @staticmethod
    def _theme_css(theme: ThemeTokens) -> str:
        palette = theme.palette
        fonts = theme.fonts
        type_scale = theme.type_scale
        variables = {
            "--color-bg": palette.get("bg", "#F7F3EA"),
            "--color-surface": palette.get("surface", "#FFFFFF"),
            "--color-ink": palette.get("ink", "#14213D"),
            "--color-muted": palette.get("muted", "#58677A"),
            "--color-accent": palette.get("accent", "#2A9D8F"),
            "--color-accent2": palette.get("accent2", "#E76F51"),
            "--color-line": palette.get("line", "#D8D3C8"),
            "--font-display": _font_stack(fonts.get("display")),
            "--font-body": _font_stack(fonts.get("body")),
            "--font-display-weight": str(fonts.get("display_weight") or 700),
            "--type-h1": str(type_scale.get("h1") or 44) + "px",
            "--type-h2": str(type_scale.get("h2") or 30) + "px",
            "--type-body": str(type_scale.get("body") or 18) + "px",
            "--type-caption": str(type_scale.get("caption") or 13) + "px",
            "--radius": str(theme.radius) + "px",
        }
        lines = [":root {"]
        lines.extend(f"  {name}: {value};" for name, value in variables.items())
        lines.append("}")
        return "\n".join(lines)


def load_theme_presets() -> list[ThemeTokens]:
    path = DESIGN_SYSTEM_DIR / "presets.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [default_theme()]
    if not isinstance(raw, list):
        return [default_theme()]
    presets = [ThemeTokens.from_dict(item) for item in raw if isinstance(item, dict)]
    return presets or [default_theme()]


def _cover(slide: SlideSpec) -> str:
    content = slide.content
    return f"""
<section class="slide cover" data-component="cover">
  <div class="cover-mark" aria-hidden="true"></div>
  <div class="eyebrow cover-eyebrow" data-text>{_text(content.get("eyebrow") or "Presentation")}</div>
  <h1 class="title cover-title" data-text>{_text(slide.title)}</h1>
  <p class="subtitle cover-subtitle" data-text>{_text(content.get("subtitle"))}</p>
  <div class="motif-line"></div>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _section(slide: SlideSpec) -> str:
    return f"""
<section class="slide" data-component="section">
  <div class="section-band">
    <h1 class="title" data-text>{_text(slide.title)}</h1>
    <p class="subtitle" data-text>{_text(slide.content.get("subtitle"))}</p>
  </div>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _content_basic(slide: SlideSpec) -> str:
    bullets = _list_items(slide.content.get("bullets"), "bullet")
    takeaway = _content_basic_takeaway(slide)
    side_card = (
        f'<p class="body side-takeaway" data-text>{_text(takeaway)}</p>'
        if takeaway
        else '<div class="side-index" aria-hidden="true"></div>'
    )
    return f"""
<section class="slide" data-component="content-basic">
  {_header(slide)}
  <main class="content-grid">
    <ul class="bullet-list">{bullets}</ul>
    <aside class="panel side-card">
      {side_card}
    </aside>
  </main>
  <div class="motif-line"></div>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _agenda(slide: SlideSpec) -> str:
    cards = "".join(
        f'<article class="panel agenda-card"><strong data-text>{_text(item)}</strong></article>'
        for item in _string_items(slide.content.get("items"))
    )
    return f"""
<section class="slide" data-component="agenda">
  {_header(slide, subtitle=False)}
  <main class="agenda-grid">{cards}</main>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _kpi_cards(slide: SlideSpec) -> str:
    cards = []
    for card in slide.content.get("cards") or []:
        if not isinstance(card, dict):
            continue
        cards.append(
            """
<article class="panel metric-card">
  <div class="metric-value" data-text>{value}</div>
  <strong data-text>{label}</strong>
  <p class="caption" data-text>{caption}</p>
</article>""".format(
                value=_text(card.get("value")),
                label=_text(card.get("label")),
                caption=_text(card.get("caption")),
            )
        )
    return f"""
<section class="slide" data-component="kpi-cards">
  {_header(slide, subtitle=False)}
  <main class="metric-grid">{''.join(cards)}</main>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _timeline(slide: SlideSpec) -> str:
    items = "".join(
        f'<article class="panel timeline-item"><p class="item" data-text>{_text(item)}</p></article>'
        for item in _string_items(slide.content.get("items"))
    )
    return f"""
<section class="slide" data-component="timeline">
  {_header(slide, subtitle=False)}
  <main class="timeline">{items}</main>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _steps(slide: SlideSpec) -> str:
    items = "".join(
        f'<article class="panel step-item"><p class="item" data-text>{_text(item)}</p></article>'
        for item in _string_items(slide.content.get("steps") or slide.content.get("items"))
    )
    return f"""
<section class="slide" data-component="steps">
  {_header(slide, subtitle=False)}
  <main class="steps">{items}</main>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _comparison(slide: SlideSpec) -> str:
    left = "".join(f'<p class="item" data-text>{_text(item)}</p>' for item in _string_items(slide.content.get("left")))
    right = "".join(f'<p class="item" data-text>{_text(item)}</p>' for item in _string_items(slide.content.get("right")))
    return f"""
<section class="slide" data-component="comparison">
  {_header(slide, subtitle=False)}
  <main class="comparison-grid">
    <article class="panel comparison-panel">
      <h3 data-text>{_text(slide.content.get("left_title") or "Current")}</h3>
      {left}
    </article>
    <article class="panel comparison-panel">
      <h3 data-text>{_text(slide.content.get("right_title") or "Target")}</h3>
      {right}
    </article>
  </main>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _summary_grid(slide: SlideSpec) -> str:
    items = "".join(
        f'<article class="panel summary-card"><strong data-text>{_text(item)}</strong></article>'
        for item in _string_items(slide.content.get("items"))
    )
    return f"""
<section class="slide" data-component="{_escape_attr(slide.component)}">
  {_header(slide, subtitle=False)}
  <main class="summary-grid">{items}</main>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _chart_takeaway(slide: SlideSpec) -> str:
    if not chart_is_valid(slide.chart):
        fallback = replace(
            slide,
            component="content-basic",
            content={
                "body": str(slide.content.get("takeaway") or slide.content.get("body") or "").strip(),
                "bullets": _string_items(slide.content.get("bullets") or slide.content.get("items")),
            },
        )
        return _content_basic(fallback)

    chart_svg = render_chart_svg(slide.chart, title=slide.title)
    return f"""
<section class="slide" data-component="chart-takeaway">
  {_header(slide, subtitle=False)}
  <main class="content-grid">
    <div class="panel chart-card">{chart_svg}</div>
    <aside class="panel side-card"><p class="body" data-text>{_text(slide.content.get("takeaway"))}</p></aside>
  </main>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _data_table(slide: SlideSpec) -> str:
    rows = []
    columns = _string_items(slide.content.get("columns"))
    if columns:
        rows.append("<tr>" + "".join(f"<th data-text>{_text(col)}</th>" for col in columns) + "</tr>")
    for row in slide.content.get("rows") or []:
        cells = row.values() if isinstance(row, dict) else row if isinstance(row, list) else [row]
        rows.append("<tr>" + "".join(f"<td data-text>{_text(cell)}</td>" for cell in cells) + "</tr>")
    return f"""
<section class="slide" data-component="data-table">
  {_header(slide, subtitle=False)}
  <table class="data-table"><tbody>{''.join(rows)}</tbody></table>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _quote(slide: SlideSpec) -> str:
    return f"""
<section class="slide" data-component="quote">
  {_header(slide, subtitle=False)}
  <blockquote class="panel quote-block">
    <p class="quote-text" data-text>{_text(slide.content.get("quote"))}</p>
    <p class="caption" data-text>{_text(slide.content.get("speaker"))}</p>
  </blockquote>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _end(slide: SlideSpec) -> str:
    return f"""
<section class="slide cover" data-component="end">
  <div class="cover-mark" aria-hidden="true"></div>
  <h1 class="title cover-title" data-text>{_text(slide.title)}</h1>
  <p class="subtitle cover-subtitle" data-text>{_text(slide.content.get("subtitle"))}</p>
  <div class="footer-number">{slide.index}</div>
</section>""".strip()


def _header(slide: SlideSpec, *, subtitle: bool = True) -> str:
    body = str(slide.content.get("body") or slide.content.get("subtitle") or "").strip()
    subtitle_html = f'<p class="subtitle" data-text>{_text(body)}</p>' if subtitle and body else ""
    return f"""
<header class="slide-header">
  <h1 class="title" data-text>{_text(slide.title)}</h1>
  {subtitle_html}
</header>""".strip()


def _list_items(value: Any, class_name: str) -> str:
    return "".join(f'<li class="{class_name}" data-text>{_text(item)}</li>' for item in _string_items(value))


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text(value: Any) -> str:
    return html.escape(str(value or "").strip())


def _escape_attr(value: Any) -> str:
    return html.escape(str(value or "").strip(), quote=True)


def _font_stack(primary: Any) -> str:
    fonts = []
    primary_text = str(primary or "").strip()
    if primary_text:
        fonts.append(f'"{primary_text}"')
    for fallback in FONT_FALLBACKS:
        if fallback not in fonts:
            fonts.append(fallback)
    return ", ".join(fonts)


def _content_basic_takeaway(slide: SlideSpec) -> str:
    content = slide.content
    for key in ("body", "takeaway", "subtitle"):
        value = str(content.get(key) or "").strip()
        if value:
            return value
    bullets = _string_items(content.get("bullets") or content.get("items"))
    return bullets[0] if bullets else ""
