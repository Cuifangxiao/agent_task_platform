"""Render compact chart specs as inline SVG, without scripts."""

from __future__ import annotations

import html
import math
from typing import Any


CHART_TYPES = {"bar", "line", "pie"}
MAX_SERIES_ITEMS = 8
LINE_AXIS_Y = 300


def chart_is_valid(chart: Any) -> bool:
    """Return True when a chart object matches the supported runtime schema."""

    return normalize_chart(chart) is not None


def normalize_chart(chart: Any) -> dict[str, Any] | None:
    if not isinstance(chart, dict):
        return None
    chart_type = str(chart.get("type") or "").strip().lower()
    if chart_type not in CHART_TYPES:
        return None
    series = []
    for item in chart.get("series") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = _number(item.get("value"))
        if not label or value is None:
            continue
        series.append({"label": label, "value": value})
    if not series:
        return None
    return {
        "type": chart_type,
        "series": series[:MAX_SERIES_ITEMS],
        "unit": str(chart.get("unit") or "").strip(),
    }


def render_chart_svg(chart: Any, *, title: str = "") -> str:
    normalized = normalize_chart(chart)
    if normalized is None:
        return ""
    chart_type = normalized["type"]
    if chart_type == "line":
        return _line_svg(normalized, title=title)
    if chart_type == "pie":
        return _pie_svg(normalized, title=title)
    return _bar_svg(normalized, title=title)


def _bar_svg(chart: dict[str, Any], *, title: str) -> str:
    series = chart["series"]
    unit = chart["unit"]
    max_value = max(abs(item["value"]) for item in series) or 1
    chart_left = 54
    chart_top = 54
    chart_height = 250
    slot = 520 / max(1, len(series))
    bar_width = min(56, slot * 0.5)
    bars = []
    labels = []
    for index, item in enumerate(series):
        value = item["value"]
        bar_height = max(3, abs(value) / max_value * chart_height)
        x = chart_left + index * slot + (slot - bar_width) / 2
        y = chart_top + chart_height - bar_height
        bars.append(
            '<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" '
            'fill="var(--color-accent)" />'.format(x=x, y=y, w=bar_width, h=bar_height)
        )
        labels.append(
            '<text data-text x="{x:.1f}" y="{y:.1f}" class="chart-value">{value}</text>'
            '<text data-text x="{x:.1f}" y="356" class="chart-label">{label}</text>'.format(
                x=x + bar_width / 2,
                y=max(28, y - 10),
                value=_escape(_format_value(value, unit)),
                label=_escape(item["label"]),
            )
        )
    return _svg_frame(title, "".join(bars) + "".join(labels))


def _line_svg(chart: dict[str, Any], *, title: str) -> str:
    series = chart["series"]
    unit = chart["unit"]
    values = [item["value"] for item in series]
    min_value = min(values)
    max_value = max(values)
    span = max(max_value - min_value, 1)
    chart_left = 58
    chart_top = 60
    chart_width = 510
    chart_height = 240
    step = chart_width / max(1, len(series) - 1)
    points: list[tuple[float, float]] = []
    for index, item in enumerate(series):
        x = chart_left + index * step
        y = chart_top + chart_height - ((item["value"] - min_value) / span * chart_height)
        points.append((x, y))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    marks = []
    for (x, y), item in zip(points, series, strict=False):
        marks.append(
            '<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="var(--color-accent2)" />'
            '<text data-text x="{x:.1f}" y="{value_y:.1f}" class="chart-value">{value}</text>'
            '<text data-text x="{x:.1f}" y="356" class="chart-label">{label}</text>'.format(
                x=x,
                y=y,
                value_y=max(28, y - 14),
                value=_escape(_format_value(item["value"], unit)),
                label=_escape(item["label"]),
            )
        )
    body = (
        f'<line x1="58" y1="{LINE_AXIS_Y}" x2="568" y2="{LINE_AXIS_Y}" class="chart-axis" />'
        f'<polyline points="{polyline}" fill="none" stroke="var(--color-accent)" stroke-width="5" '
        'stroke-linecap="round" stroke-linejoin="round" />'
        + "".join(marks)
    )
    return _svg_frame(title, body)


def _pie_svg(chart: dict[str, Any], *, title: str) -> str:
    series = chart["series"][:6]
    unit = chart["unit"]
    total = sum(max(0, item["value"]) for item in series)
    if total <= 0:
        return _bar_svg(chart, title=title)
    center_x = 210
    center_y = 202
    radius = 118
    start_angle = -90.0
    slices = []
    legend = []
    palette = [
        "var(--color-accent)",
        "var(--color-accent2)",
        "color-mix(in srgb, var(--color-accent) 70%, white)",
        "color-mix(in srgb, var(--color-accent2) 72%, white)",
        "color-mix(in srgb, var(--color-ink) 42%, var(--color-accent))",
        "color-mix(in srgb, var(--color-muted) 50%, var(--color-accent2))",
    ]
    for index, item in enumerate(series):
        angle = max(0, item["value"]) / total * 360
        end_angle = start_angle + angle
        slices.append(_pie_path(center_x, center_y, radius, start_angle, end_angle, palette[index % len(palette)]))
        legend_y = 112 + index * 40
        legend.append(
            '<rect x="386" y="{y}" width="18" height="18" rx="4" fill="{color}" />'
            '<text data-text x="416" y="{text_y}" class="chart-legend">{label}</text>'
            '<text data-text x="548" y="{text_y}" class="chart-legend value">{value}</text>'.format(
                y=legend_y,
                text_y=legend_y + 15,
                color=palette[index % len(palette)],
                label=_escape(item["label"]),
                value=_escape(_format_value(item["value"], unit)),
            )
        )
        start_angle = end_angle
    return _svg_frame(title, "".join(slices) + "".join(legend))


def _svg_frame(title: str, body: str) -> str:
    title_text = _escape(title)
    heading = f'<text data-text x="34" y="34" class="chart-title">{title_text}</text>' if title_text else ""
    return f"""
<svg class="inline-chart" viewBox="0 0 640 400" role="img" aria-label="{title_text}">
  <style>
    .chart-title {{ fill: var(--color-ink); font: 700 22px var(--font-display); }}
    .chart-value {{ fill: var(--color-ink); font: 700 15px var(--font-body); text-anchor: middle; }}
    .chart-label {{ fill: var(--color-muted); font: 13px var(--font-body); text-anchor: middle; }}
    .chart-legend {{ fill: var(--color-ink); font: 15px var(--font-body); }}
    .chart-legend.value {{ fill: var(--color-muted); text-anchor: end; }}
    .chart-axis {{ stroke: var(--color-line); stroke-width: 2; }}
  </style>
  {heading}
  {body}
</svg>""".strip()


def _pie_path(cx: int, cy: int, radius: int, start_angle: float, end_angle: float, fill: str) -> str:
    start_x, start_y = _polar_to_cartesian(cx, cy, radius, end_angle)
    end_x, end_y = _polar_to_cartesian(cx, cy, radius, start_angle)
    large_arc = 1 if end_angle - start_angle > 180 else 0
    return (
        '<path d="M {cx} {cy} L {start_x:.2f} {start_y:.2f} '
        'A {radius} {radius} 0 {large_arc} 0 {end_x:.2f} {end_y:.2f} Z" fill="{fill}" />'
    ).format(
        cx=cx,
        cy=cy,
        start_x=start_x,
        start_y=start_y,
        radius=radius,
        large_arc=large_arc,
        end_x=end_x,
        end_y=end_y,
        fill=fill,
    )


def _polar_to_cartesian(cx: int, cy: int, radius: int, angle_degrees: float) -> tuple[float, float]:
    angle = math.radians(angle_degrees)
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _format_value(value: float, unit: str) -> str:
    rounded = int(value) if value.is_integer() else round(value, 1)
    return f"{rounded}{unit}"


def _escape(value: Any) -> str:
    return html.escape(str(value or "").strip())
