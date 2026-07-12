"""Restricted-SVG visualization references for the presentation Artist."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.deck.design.catalog import get_template_pack
from core.deck.design.sources import _font, _palette, _resolve_theme, _text, _xml
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, ThemeTokens


@dataclass(frozen=True)
class VisualizationSpec:
    visualization_id: str
    name: str
    description: str
    components: tuple[str, ...]
    aliases: tuple[str, ...]
    data_contract: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "visualization_id": self.visualization_id,
            "name": self.name,
            "description": self.description,
            "components": list(self.components),
            "aliases": list(self.aliases),
            "data_contract": list(self.data_contract),
        }


VISUALIZATION_CATALOG: tuple[VisualizationSpec, ...] = (
    VisualizationSpec("bar", "Bar comparison", "Compare magnitudes across a small number of categories.", ("chart-takeaway", "comparison"), ("bar", "column", "ranking", "rank", "柱状", "条形", "排名", "排行"), ("category", "value", "optional baseline")),
    VisualizationSpec("line", "Line trend", "Show change over an ordered time or sequence axis.", ("chart-takeaway", "timeline"), ("line", "trend", "growth", "time series", "折线", "趋势", "走势", "增长曲线", "时间序列"), ("period", "value", "optional comparison series")),
    VisualizationSpec("donut", "Donut composition", "Explain a small set of shares that add to a whole.", ("chart-takeaway", "summary"), ("donut", "pie", "share", "mix", "composition", "ratio", "环形", "饼图", "占比", "构成", "份额", "比例"), ("segment", "share", "total")),
    VisualizationSpec("timeline", "Milestone timeline", "Place milestones, dates, and outcomes on one directional axis.", ("timeline", "agenda"), ("timeline", "roadmap", "milestone", "chronology", "时间轴", "路线图", "里程碑", "阶段计划"), ("date or stage", "milestone", "outcome")),
    VisualizationSpec("process", "Process flow", "Show a finite sequence of actions and hand-offs.", ("steps", "agenda", "content-basic"), ("process", "workflow", "steps", "flow", "流程", "步骤", "工作流", "闭环"), ("step", "action", "output or owner")),
    VisualizationSpec("matrix", "Decision matrix", "Position alternatives on two explicit decision dimensions.", ("quadrant", "comparison"), ("matrix", "quadrant", "2x2", "priority", "矩阵", "四象限", "优先级", "二维"), ("x dimension", "y dimension", "items")),
    VisualizationSpec("funnel", "Conversion funnel", "Show progressive reduction across ordered stages.", ("chart-takeaway", "steps"), ("funnel", "conversion", "pipeline", "漏斗", "转化", "流失", "管道"), ("stage", "volume or rate", "conversion delta")),
    VisualizationSpec("architecture", "System architecture", "Show system boundaries, layers, dependencies, and active paths.", ("content-basic", "steps"), ("architecture", "system", "topology", "layers", "dependency", "架构", "系统", "拓扑", "分层", "依赖"), ("layer", "system role", "direction or dependency")),
    VisualizationSpec("table", "Evidence table", "Compare exact values when precise lookup matters more than shape.", ("data-table", "comparison"), ("table", "data table", "benchmark", "表格", "数据表", "明细", "基准"), ("row label", "measure columns", "highlight rule")),
    VisualizationSpec("kpi", "KPI hierarchy", "Lead with one decision metric and its supporting context.", ("kpi-cards", "summary", "content-basic"), ("kpi", "metric", "number", "scorecard", "指标", "核心数据", "大数字", "计分卡"), ("hero value", "comparison", "supporting metrics")),
    VisualizationSpec("comparison", "Structured comparison", "Contrast two alternatives or states on shared criteria.", ("comparison", "content-basic"), ("comparison", "compare", "before after", "versus", "pros cons", "对比", "比较", "前后", "优劣", "方案选择"), ("shared criterion", "left evidence", "right evidence", "decision")),
)


_BY_ID = {item.visualization_id: item for item in VISUALIZATION_CATALOG}


def list_visualizations() -> tuple[VisualizationSpec, ...]:
    return VISUALIZATION_CATALOG


def public_visualization_catalog() -> list[dict[str, Any]]:
    return [item.to_dict() for item in VISUALIZATION_CATALOG]


def select_visualization(*, component: str = "", intent: str = "") -> VisualizationSpec:
    """Select the best reference using explicit intent before component hints."""

    normalized_intent = _normalize(intent)
    normalized_component = _normalize(component)
    scores: list[tuple[int, int, VisualizationSpec]] = []
    for position, item in enumerate(VISUALIZATION_CATALOG):
        score = 0
        if normalized_intent:
            if _contains(normalized_intent, _normalize(item.visualization_id)):
                score = max(score, 120)
            for alias in item.aliases:
                normalized_alias = _normalize(alias)
                if normalized_alias and _contains(normalized_intent, normalized_alias):
                    score = max(score, 80 + min(30, len(normalized_alias)))
        if normalized_component and any(normalized_component == _normalize(value) for value in item.components):
            score += 25
        if score:
            scores.append((score, -position, item))
    if scores:
        return max(scores, key=lambda value: (value[0], value[1]))[2]
    component_defaults = {
        "chart-takeaway": "bar",
        "data-table": "table",
        "kpi-cards": "kpi",
        "quadrant": "matrix",
        "comparison": "comparison",
        "timeline": "timeline",
        "steps": "process",
        "agenda": "process",
        "summary": "kpi",
    }
    return _BY_ID[component_defaults.get(normalized_component, "kpi")]


def visualization_svg_source(
    *,
    component: str = "",
    intent: str = "",
    visualization_id: str = "",
    template_id: str = "editorial_freeform",
    theme: ThemeTokens | Mapping[str, Any] | None = None,
) -> str:
    """Select and render a visualization reference as one safe 1280x720 SVG."""

    selected = _BY_ID.get(str(visualization_id or "").strip()) or select_visualization(component=component, intent=intent)
    return render_visualization_reference_svg(selected.visualization_id, template_id=template_id, theme=theme)


def render_visualization_reference_svg(
    visualization_id: str,
    *,
    template_id: str = "editorial_freeform",
    theme: ThemeTokens | Mapping[str, Any] | None = None,
) -> str:
    """Render a named visualization grammar using only native-safe SVG tags."""

    spec = _BY_ID.get(str(visualization_id or "").strip()) or _BY_ID["kpi"]
    pack = get_template_pack(template_id)
    resolved_theme = _resolve_theme(theme, pack.theme)
    colors = _palette(resolved_theme)
    display_font = _font(resolved_theme.fonts.get("display"), "Arial")
    body_font = _font(resolved_theme.fonts.get("body"), "Arial")
    renderer = _RENDERERS[spec.visualization_id]
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}" '
            f'viewBox="0 0 {CANVAS_WIDTH} {CANVAS_HEIGHT}" data-reference-kind="visualization" '
            f'data-visualization-id="{_xml(spec.visualization_id)}" data-template-id="{_xml(pack.template_id)}">'
        ),
        '<g id="visualization-canvas">',
        f'<rect id="visualization-background" x="0" y="0" width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}" fill="{colors["bg"]}"/>',
        f'<rect id="visualization-header-rule" x="64" y="126" width="1152" height="3" fill="{colors["accent"]}"/>',
        f'<rect id="visualization-stage" x="64" y="158" width="1152" height="458" fill="{colors["surface"]}" stroke="{colors["line"]}" stroke-width="1"/>',
        "</g>",
        '<g id="visualization-heading">',
        _text("visualization-type", spec.name.upper(), 64, 42, 780, 28, colors["ink"], display_font, 21, weight=700, letter_spacing=0.5),
        _text("visualization-purpose", spec.description, 64, 82, 900, 22, colors["muted"], body_font, 13, weight=400),
        _text("visualization-reference-mark", "VISUAL GRAMMAR", 1016, 48, 200, 20, colors["accent2"], body_font, 12, weight=700, anchor="end"),
        "</g>",
        f'<g id="visualization-{_xml(spec.visualization_id)}">',
        *renderer(colors, display_font, body_font),
        "</g>",
        '<g id="visualization-footer">',
        _text("visualization-data-contract", "DATA  ·  " + "  /  ".join(spec.data_contract), 68, 652, 960, 20, colors["muted"], body_font, 12, weight=600),
        _text("visualization-template-label", pack.name.upper(), 1000, 652, 216, 20, colors["accent"], body_font, 12, weight=700, anchor="end"),
        "</g>",
        "</svg>",
    ]
    return "\n".join(parts)


def _bar(c: dict[str, str], _display: str, body: str) -> list[str]:
    values = (184, 276, 356, 238)
    labels = ("A", "B", "C", "D")
    result = [
        f'<line id="bar-baseline" x1="168" y1="542" x2="905" y2="542" stroke="{c["line"]}" stroke-width="2"/>',
        f'<line id="bar-target" x1="168" y1="298" x2="905" y2="298" stroke="{c["accent2"]}" stroke-width="2" stroke-dasharray="7 7"/>',
    ]
    for index, (height, label) in enumerate(zip(values, labels, strict=True), start=1):
        x = 190 + (index - 1) * 166
        color = c["accent"] if index == 3 else c["muted"]
        result.extend([
            f'<rect id="bar-value-{index}" x="{x}" y="{542 - height}" width="92" height="{height}" fill="{color}"/>',
            _text(f"bar-label-{index}", label, x, 558, 92, 22, c["ink"], body, 13, weight=700),
            _text(f"bar-number-{index}", str((44, 66, 85, 57)[index - 1]), x, 512 - height, 92, 24, c["ink"], body, 14, weight=700),
        ])
    result.extend([
        f'<rect id="bar-takeaway-field" x="958" y="245" width="202" height="220" fill="{c["bg"]}" stroke="{c["accent"]}" stroke-width="2"/>',
        _text("bar-takeaway-label", "TAKEAWAY", 980, 270, 158, 20, c["accent"], body, 12, weight=700),
        _text("bar-takeaway-value", "+29%", 980, 320, 158, 54, c["ink"], body, 42, weight=700),
        _text("bar-takeaway-note", "Highlight one decision-relevant delta", 980, 396, 150, 46, c["muted"], body, 13, weight=400),
    ])
    return result


def _line(c: dict[str, str], _display: str, body: str) -> list[str]:
    result = []
    for index, y in enumerate((250, 330, 410, 490), start=1):
        result.append(f'<line id="line-grid-{index}" x1="150" y1="{y}" x2="1110" y2="{y}" stroke="{c["line"]}" stroke-width="1"/>')
    result.extend([
        f'<polyline id="line-primary" points="165,470 330,430 495,388 660,345 825,270 1000,218" fill="none" stroke="{c["accent"]}" stroke-width="7"/>',
        f'<polyline id="line-baseline" points="165,448 330,425 495,412 660,394 825,372 1000,352" fill="none" stroke="{c["muted"]}" stroke-width="3" stroke-dasharray="10 8"/>',
    ])
    for index, (x, y) in enumerate(((165, 470), (330, 430), (495, 388), (660, 345), (825, 270), (1000, 218)), start=1):
        result.append(f'<circle id="line-point-{index}" cx="{x}" cy="{y}" r="7" fill="{c["bg"]}" stroke="{c["accent"]}" stroke-width="4"/>')
    result.extend([
        f'<line id="line-callout" x1="1000" y1="218" x2="1100" y2="198" stroke="{c["accent2"]}" stroke-width="2"/>',
        _text("line-callout-value", "+42%", 1008, 170, 140, 30, c["accent2"], body, 22, weight=700),
        _text("line-axis-start", "T1", 150, 552, 80, 20, c["muted"], body, 12, weight=600),
        _text("line-axis-end", "T6", 980, 552, 80, 20, c["muted"], body, 12, weight=600),
    ])
    return result


def _donut(c: dict[str, str], _display: str, body: str) -> list[str]:
    return [
        f'<circle id="donut-track" cx="390" cy="385" r="142" fill="none" stroke="{c["line"]}" stroke-width="58"/>',
        f'<circle id="donut-segment-primary" cx="390" cy="385" r="142" fill="none" stroke="{c["accent"]}" stroke-width="58" stroke-dasharray="535 357" stroke-dashoffset="0"/>',
        f'<circle id="donut-segment-secondary" cx="390" cy="385" r="142" fill="none" stroke="{c["accent2"]}" stroke-width="58" stroke-dasharray="205 687" stroke-dashoffset="-535"/>',
        _text("donut-total", "100%", 300, 346, 180, 56, c["ink"], body, 42, weight=700),
        _text("donut-total-label", "TOTAL", 330, 410, 120, 22, c["muted"], body, 12, weight=700),
        f'<rect id="donut-legend-a" x="690" y="265" width="18" height="18" fill="{c["accent"]}"/>',
        _text("donut-legend-a-label", "Primary share", 726, 262, 240, 24, c["ink"], body, 16, weight=600),
        _text("donut-legend-a-value", "60%", 1010, 258, 100, 28, c["accent"], body, 22, weight=700, anchor="end"),
        f'<line id="donut-legend-rule-a" x1="690" y1="316" x2="1110" y2="316" stroke="{c["line"]}" stroke-width="1"/>',
        f'<rect id="donut-legend-b" x="690" y="350" width="18" height="18" fill="{c["accent2"]}"/>',
        _text("donut-legend-b-label", "Growth segment", 726, 347, 240, 24, c["ink"], body, 16, weight=600),
        _text("donut-legend-b-value", "23%", 1010, 343, 100, 28, c["accent2"], body, 22, weight=700, anchor="end"),
        f'<line id="donut-legend-rule-b" x1="690" y1="401" x2="1110" y2="401" stroke="{c["line"]}" stroke-width="1"/>',
        _text("donut-rule", "Use 2–4 segments; label values directly", 690, 448, 420, 28, c["muted"], body, 14, weight=500),
    ]


def _timeline(c: dict[str, str], _display: str, body: str) -> list[str]:
    xs = (160, 390, 620, 850, 1080)
    result = [f'<line id="timeline-axis" x1="160" y1="378" x2="1080" y2="378" stroke="{c["accent"]}" stroke-width="5"/>']
    for index, x in enumerate(xs, start=1):
        color = c["accent2"] if index == 4 else c["accent"]
        top = 225 if index % 2 else 430
        result.extend([
            f'<circle id="timeline-node-{index}" cx="{x}" cy="378" r="14" fill="{c["bg"]}" stroke="{color}" stroke-width="6"/>',
            f'<line id="timeline-stem-{index}" x1="{x}" y1="{364 if index % 2 else 392}" x2="{x}" y2="{302 if index % 2 else 454}" stroke="{color}" stroke-width="2"/>',
            _text(f"timeline-stage-{index}", f"0{index}", x - 28, top, 56, 28, color, body, 20, weight=700),
            _text(f"timeline-label-{index}", "Milestone", x - 72, top + 40, 144, 24, c["ink"], body, 14, weight=600),
            _text(f"timeline-output-{index}", "Outcome", x - 72, top + 70, 144, 20, c["muted"], body, 12, weight=400),
        ])
    return result


def _process(c: dict[str, str], _display: str, body: str) -> list[str]:
    result: list[str] = []
    for index in range(4):
        x = 116 + index * 286
        if index < 3:
            result.extend([
                f'<line id="process-link-{index + 1}" x1="{x + 220}" y1="365" x2="{x + 270}" y2="365" stroke="{c["accent"]}" stroke-width="4"/>',
                f'<polygon id="process-arrow-{index + 1}" points="{x + 270},365 {x + 254},355 {x + 254},375" fill="{c["accent"]}"/>',
            ])
        color = c["accent2"] if index == 3 else c["accent"]
        result.extend([
            f'<rect id="process-node-{index + 1}" x="{x}" y="270" width="220" height="190" fill="{c["bg"]}" stroke="{color}" stroke-width="3"/>',
            f'<circle id="process-number-field-{index + 1}" cx="{x + 34}" cy="304" r="18" fill="{color}"/>',
            _text(f"process-number-{index + 1}", str(index + 1), x + 26, 293, 16, 22, c["bg"], body, 14, weight=700),
            _text(f"process-title-{index + 1}", "Action", x + 28, 338, 164, 28, c["ink"], body, 18, weight=700),
            _text(f"process-output-{index + 1}", "Output / owner", x + 28, 390, 164, 42, c["muted"], body, 13, weight=400),
        ])
    return result


def _matrix(c: dict[str, str], _display: str, body: str) -> list[str]:
    return [
        f'<rect id="matrix-q1" x="220" y="208" width="430" height="160" fill="{c["accent"]}" fill-opacity="0.18"/>',
        f'<rect id="matrix-q2" x="650" y="208" width="430" height="160" fill="{c["accent2"]}" fill-opacity="0.12"/>',
        f'<rect id="matrix-q3" x="220" y="368" width="430" height="160" fill="{c["line"]}" fill-opacity="0.50"/>',
        f'<rect id="matrix-q4" x="650" y="368" width="430" height="160" fill="{c["surface"]}" stroke="{c["line"]}" stroke-width="1"/>',
        f'<line id="matrix-x-axis" x1="190" y1="528" x2="1110" y2="528" stroke="{c["ink"]}" stroke-width="3"/>',
        f'<polygon id="matrix-x-arrow" points="1110,528 1092,518 1092,538" fill="{c["ink"]}"/>',
        f'<line id="matrix-y-axis" x1="220" y1="558" x2="220" y2="184" stroke="{c["ink"]}" stroke-width="3"/>',
        f'<polygon id="matrix-y-arrow" points="220,184 210,202 230,202" fill="{c["ink"]}"/>',
        _text("matrix-q1-label", "INVEST", 250, 238, 180, 24, c["accent"], body, 16, weight=700),
        _text("matrix-q2-label", "PRIORITIZE", 680, 238, 180, 24, c["accent2"], body, 16, weight=700),
        _text("matrix-q3-label", "MONITOR", 250, 398, 180, 24, c["muted"], body, 16, weight=700),
        _text("matrix-q4-label", "DEFER", 680, 398, 180, 24, c["muted"], body, 16, weight=700),
        f'<circle id="matrix-item-a" cx="790" cy="278" r="16" fill="{c["accent2"]}"/>',
        f'<circle id="matrix-item-b" cx="575" cy="320" r="12" fill="{c["accent"]}"/>',
        f'<circle id="matrix-item-c" cx="885" cy="442" r="10" fill="{c["muted"]}"/>',
        _text("matrix-x-label", "DIMENSION X  →", 860, 550, 220, 20, c["muted"], body, 12, weight=700),
        _text("matrix-y-label", "DIMENSION Y", 82, 190, 120, 20, c["muted"], body, 12, weight=700),
    ]


def _funnel(c: dict[str, str], _display: str, body: str) -> list[str]:
    widths = (920, 720, 520, 320)
    ys = (218, 304, 390, 476)
    result: list[str] = []
    for index, (width, y) in enumerate(zip(widths, ys, strict=True), start=1):
        x = 640 - width / 2
        next_width = widths[index] if index < len(widths) else width - 100
        next_x = 640 - next_width / 2
        color = c["accent2"] if index == 4 else c["accent"]
        result.extend([
            f'<polygon id="funnel-stage-{index}" points="{x:g},{y} {x + width:g},{y} {next_x + next_width:g},{y + 68} {next_x:g},{y + 68}" fill="{color}" fill-opacity="{0.26 + index * 0.14:.2f}" stroke="{color}" stroke-width="2"/>',
            _text(f"funnel-label-{index}", f"STAGE {index}", 540, y + 19, 200, 24, c["ink"], body, 14, weight=700),
            _text(f"funnel-value-{index}", ("100%", "72%", "48%", "31%")[index - 1], 820, y + 19, 120, 24, color, body, 16, weight=700, anchor="end"),
        ])
    return result


def _architecture(c: dict[str, str], _display: str, body: str) -> list[str]:
    result: list[str] = []
    layer_y = (210, 340, 470)
    for layer, y in enumerate(layer_y, start=1):
        result.extend([
            f'<rect id="architecture-layer-{layer}" x="138" y="{y}" width="1004" height="92" fill="{c["bg"]}" stroke="{c["line"]}" stroke-width="2"/>',
            _text(f"architecture-layer-label-{layer}", f"LAYER {layer}", 160, y + 30, 112, 22, c["muted"], body, 12, weight=700),
        ])
        for node in range(3):
            x = 310 + node * 265
            color = c["accent2"] if layer == 2 and node == 1 else c["accent"]
            result.extend([
                f'<rect id="architecture-node-{layer}-{node + 1}" x="{x}" y="{y + 18}" width="190" height="56" fill="{c["surface"]}" stroke="{color}" stroke-width="2"/>',
                _text(f"architecture-node-label-{layer}-{node + 1}", "System role", x + 18, y + 36, 154, 22, c["ink"], body, 13, weight=600),
            ])
    for index, x in enumerate((405, 670, 935), start=1):
        result.extend([
            f'<line id="architecture-link-top-{index}" x1="{x}" y1="302" x2="{x}" y2="340" stroke="{c["accent"]}" stroke-width="3"/>',
            f'<polygon id="architecture-arrow-top-{index}" points="{x},340 {x - 8},326 {x + 8},326" fill="{c["accent"]}"/>',
            f'<line id="architecture-link-bottom-{index}" x1="{x}" y1="432" x2="{x}" y2="470" stroke="{c["accent"]}" stroke-width="3"/>',
            f'<polygon id="architecture-arrow-bottom-{index}" points="{x},470 {x - 8},456 {x + 8},456" fill="{c["accent"]}"/>',
        ])
    return result


def _table(c: dict[str, str], _display: str, body: str) -> list[str]:
    result = [
        f'<rect id="table-frame" x="130" y="210" width="1020" height="350" fill="{c["bg"]}" stroke="{c["line"]}" stroke-width="2"/>',
        f'<rect id="table-header" x="130" y="210" width="1020" height="62" fill="{c["accent"]}"/>',
        f'<rect id="table-highlight" x="130" y="416" width="1020" height="72" fill="{c["accent2"]}" fill-opacity="0.16"/>',
    ]
    for index, x in enumerate((380, 625, 870), start=1):
        result.append(f'<line id="table-column-{index}" x1="{x}" y1="210" x2="{x}" y2="560" stroke="{c["line"]}" stroke-width="1"/>')
    for index, y in enumerate((272, 344, 416, 488), start=1):
        result.append(f'<line id="table-row-{index}" x1="130" y1="{y}" x2="1150" y2="{y}" stroke="{c["line"]}" stroke-width="1"/>')
    headers = ("CRITERION", "OPTION A", "OPTION B", "DELTA")
    xs = (154, 404, 649, 894)
    for index, (label, x) in enumerate(zip(headers, xs, strict=True), start=1):
        result.append(_text(f"table-header-label-{index}", label, x, 230, 200, 22, c["bg"], body, 13, weight=700))
    for row in range(4):
        y = 292 + row * 72
        result.extend([
            _text(f"table-row-label-{row + 1}", f"Measure {row + 1}", 154, y, 200, 22, c["ink"], body, 13, weight=600),
            _text(f"table-row-a-{row + 1}", str((42, 58, 73, 81)[row]), 420, y, 150, 22, c["ink"], body, 14, weight=600),
            _text(f"table-row-b-{row + 1}", str((47, 62, 88, 79)[row]), 665, y, 150, 22, c["ink"], body, 14, weight=600),
            _text(f"table-row-delta-{row + 1}", ("+5", "+4", "+15", "−2")[row], 910, y, 150, 22, c["accent2"] if row == 2 else c["muted"], body, 14, weight=700),
        ])
    return result


def _kpi(c: dict[str, str], display: str, body: str) -> list[str]:
    return [
        _text("kpi-hero-value", "82%", 132, 224, 440, 116, c["accent"], display, 88, weight=700),
        _text("kpi-hero-label", "PRIMARY DECISION METRIC", 142, 356, 380, 24, c["ink"], body, 15, weight=700),
        _text("kpi-hero-context", "+12 pts vs baseline", 142, 405, 380, 34, c["accent2"], body, 22, weight=700),
        f'<line id="kpi-divider" x1="608" y1="214" x2="608" y2="548" stroke="{c["line"]}" stroke-width="2"/>',
        _text("kpi-support-a-value", "3.4×", 690, 240, 190, 54, c["ink"], display, 38, weight=700),
        _text("kpi-support-a-label", "Driver A", 690, 306, 210, 22, c["muted"], body, 13, weight=600),
        f'<line id="kpi-support-rule-a" x1="690" y1="350" x2="1100" y2="350" stroke="{c["line"]}" stroke-width="1"/>',
        _text("kpi-support-b-value", "−18%", 690, 386, 190, 54, c["ink"], display, 38, weight=700),
        _text("kpi-support-b-label", "Guardrail", 690, 452, 210, 22, c["muted"], body, 13, weight=600),
        f'<line id="kpi-support-rule-b" x1="690" y1="496" x2="1100" y2="496" stroke="{c["line"]}" stroke-width="1"/>',
        _text("kpi-implication", "State what the metric changes", 690, 520, 370, 24, c["accent"], body, 15, weight=700),
    ]


def _comparison(c: dict[str, str], display: str, body: str) -> list[str]:
    return [
        f'<rect id="comparison-left-field" x="110" y="206" width="480" height="314" fill="{c["bg"]}" stroke="{c["line"]}" stroke-width="2"/>',
        f'<rect id="comparison-right-field" x="690" y="206" width="480" height="314" fill="{c["accent"]}" fill-opacity="0.12" stroke="{c["accent"]}" stroke-width="2"/>',
        _text("comparison-left-label", "CURRENT", 144, 240, 210, 24, c["muted"], body, 13, weight=700),
        _text("comparison-left-value", "42", 144, 292, 220, 66, c["ink"], display, 50, weight=700),
        _text("comparison-left-note", "Shared criterion and baseline", 144, 390, 360, 28, c["muted"], body, 14, weight=400),
        _text("comparison-right-label", "PROPOSED", 724, 240, 210, 24, c["accent"], body, 13, weight=700),
        _text("comparison-right-value", "68", 724, 292, 220, 66, c["accent"], display, 50, weight=700),
        _text("comparison-right-note", "Same criterion, improved state", 724, 390, 360, 28, c["ink"], body, 14, weight=400),
        f'<circle id="comparison-delta-field" cx="640" cy="363" r="55" fill="{c["accent2"]}"/>',
        _text("comparison-delta", "+26", 602, 338, 76, 40, c["bg"], body, 25, weight=700),
        f'<rect id="comparison-answer-band" x="250" y="552" width="780" height="46" fill="{c["ink"]}"/>',
        _text("comparison-answer", "DECISION  ·  Make the implication explicit", 285, 563, 710, 24, c["bg"], body, 14, weight=700),
    ]


_Renderer = Callable[[dict[str, str], str, str], list[str]]
_RENDERERS: dict[str, _Renderer] = {
    "bar": _bar,
    "line": _line,
    "donut": _donut,
    "timeline": _timeline,
    "process": _process,
    "matrix": _matrix,
    "funnel": _funnel,
    "architecture": _architecture,
    "table": _table,
    "kpi": _kpi,
    "comparison": _comparison,
}


def _normalize(value: Any) -> str:
    return re.sub(r"[\s_/\\-]+", " ", str(value or "").casefold()).strip()


def _contains(haystack: str, needle: str) -> bool:
    if not haystack or not needle:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in needle):
        return needle in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


__all__ = [
    "VISUALIZATION_CATALOG",
    "VisualizationSpec",
    "list_visualizations",
    "public_visualization_catalog",
    "render_visualization_reference_svg",
    "select_visualization",
    "visualization_svg_source",
]
