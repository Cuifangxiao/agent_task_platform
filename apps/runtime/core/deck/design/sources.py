"""Generate safe visual references from the active template catalog.

The template JSON is the semantic source of truth.  This module turns its
theme, chrome, composition, and geometry into a complete 1280x720 SVG that can
be injected into the Artist prompt.  References contain no scripts, external
resources, CSS, transforms, or unsupported SVG primitives.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from core.deck.design.catalog import TemplatePack, get_template_pack
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, ThemeTokens


_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")


def resolve_template_layout(pack: TemplatePack, layout_id: str = "") -> dict[str, Any]:
    """Resolve a layout in a pack, falling back to the pack's first layout."""

    requested = str(layout_id or "").strip()
    for layout in pack.layouts:
        if str(layout.get("layout_id") or "").strip() == requested:
            return dict(layout)
    return dict(pack.layouts[0]) if pack.layouts else {
        "layout_id": "freeform",
        "composition": "One dominant idea inside the safe frame",
        "geometry": {"primary": [72, 170, 1136, 420]},
    }


def render_template_reference_svg(
    template_id: str,
    layout_id: str = "",
    *,
    theme: ThemeTokens | Mapping[str, Any] | None = None,
) -> str:
    """Render one catalog layout as a complete, restricted reference SVG.

    ``theme`` lets the active art direction override the pack's default token
    values without changing its geometry or visual language.
    """

    pack = get_template_pack(template_id)
    layout = resolve_template_layout(pack, layout_id)
    resolved_theme = _resolve_theme(theme, pack.theme)
    colors = _palette(resolved_theme)
    display_font = _font(resolved_theme.fonts.get("display"), "Arial")
    body_font = _font(resolved_theme.fonts.get("body"), "Arial")
    resolved_layout_id = str(layout.get("layout_id") or "freeform").strip() or "freeform"
    background = _template_background(pack.template_id, resolved_layout_id, colors)

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}" '
            f'viewBox="0 0 {CANVAS_WIDTH} {CANVAS_HEIGHT}" data-reference-kind="template" '
            f'data-template-id="{_xml(pack.template_id)}" data-template-mode="{_xml(pack.template_mode)}" '
            f'data-layout-id="{_xml(resolved_layout_id)}">'
        ),
        '<g id="template-canvas">',
        f'<rect id="template-background" x="0" y="0" width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}" fill="{background}"/>',
        *_template_chrome(pack.template_id, resolved_layout_id, colors),
        f'<rect id="template-safe-frame" x="56" y="42" width="1168" height="636" fill="none" stroke="{colors["line"]}" stroke-width="1" stroke-dasharray="5 7"/>',
        "</g>",
        '<g id="template-metadata">',
        _text(
            "template-pack-label",
            pack.name.upper(),
            60,
            48,
            460,
            22,
            colors["muted"],
            body_font,
            12,
            weight=700,
            letter_spacing=1.2,
        ),
        _text(
            "template-layout-label",
            resolved_layout_id.replace("_", " / ").upper(),
            700,
            48,
            460,
            22,
            colors["accent"],
            body_font,
            12,
            weight=700,
            anchor="end",
        ),
        _text(
            "template-composition",
            _trim(str(layout.get("composition") or "Use the named geometry as the composition skeleton."), 118),
            64,
            672,
            1080,
            20,
            colors["muted"],
            body_font,
            12,
            weight=400,
        ),
        _text("template-page-mark", "REFERENCE", 1156, 672, 68, 20, colors["accent2"], body_font, 12, weight=700, anchor="end"),
        "</g>",
        '<g id="template-content-zones">',
    ]

    geometry = layout.get("geometry") or {}
    if not isinstance(geometry, Mapping) or not geometry:
        geometry = {"primary": [72, 170, 1136, 420]}
    for index, (name, raw_box) in enumerate(geometry.items(), start=1):
        box = _box(raw_box)
        if box is None:
            continue
        parts.extend(_zone(index, str(name or f"zone-{index}"), box, colors, display_font, body_font))
    parts.extend(["</g>", "</svg>"])
    return "\n".join(parts)


def _zone(
    index: int,
    name: str,
    box: tuple[float, float, float, float],
    colors: dict[str, str],
    display_font: str,
    body_font: str,
) -> list[str]:
    x, y, width, height = box
    slug = _slug(name)
    label = name.replace("_", " ").upper()
    if height <= 18 or width <= 18:
        if width >= height:
            cy = y + height / 2
            return [
                f'<line id="zone-{slug}" data-zone="{_xml(name)}" x1="{x:g}" y1="{cy:g}" x2="{x + width:g}" y2="{cy:g}" stroke="{colors["accent"]}" stroke-width="3"/>',
                f'<circle id="zone-{slug}-start" cx="{x:g}" cy="{cy:g}" r="5" fill="{colors["accent2"]}"/>',
                f'<circle id="zone-{slug}-end" cx="{x + width:g}" cy="{cy:g}" r="5" fill="{colors["accent"]}"/>',
                _text(f"zone-{slug}-label", f"{index:02d}  {label}", x, max(82, y - 30), min(width, 260), 20, colors["ink"], body_font, 13, weight=700),
            ]
        cx = x + width / 2
        return [
            f'<line id="zone-{slug}" data-zone="{_xml(name)}" x1="{cx:g}" y1="{y:g}" x2="{cx:g}" y2="{y + height:g}" stroke="{colors["accent"]}" stroke-width="3"/>',
            _text(f"zone-{slug}-label", f"{index:02d}  {label}", min(1040, x + 18), y, 170, 20, colors["ink"], body_font, 13, weight=700),
        ]

    if height < 96:
        label_width = min(max(132.0, 72.0 + 7.2 * len(label)), max(132.0, width * 0.46))
        return [
            f'<rect id="zone-{slug}" data-zone="{_xml(name)}" x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" fill="{colors["surface"]}" fill-opacity="0.68" stroke="{colors["accent"]}" stroke-width="2"/>',
            f'<rect id="zone-{slug}-rail" x="{x:g}" y="{y:g}" width="6" height="{height:g}" fill="{colors["accent"]}"/>',
            _text(f"zone-{slug}-label", f"{index:02d}  {label}", x + 22, y + max(8, (height - 24) / 2), label_width, 24, colors["accent"], body_font, 13, weight=700),
            _text(f"zone-{slug}-instruction", "Supporting metadata or answer band", x + label_width + 38, y + max(8, (height - 24) / 2), max(100, width - label_width - 58), 24, colors["ink"], display_font, 13, weight=600),
        ]

    label_width = min(max(132.0, 72.0 + 7.2 * len(label)), max(132.0, width - 24.0))
    text_width = max(90.0, min(width - 34.0, 420.0))
    return [
        f'<rect id="zone-{slug}" data-zone="{_xml(name)}" x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" fill="{colors["surface"]}" fill-opacity="0.68" stroke="{colors["accent"]}" stroke-width="2"/>',
        f'<rect id="zone-{slug}-rail" x="{x:g}" y="{y:g}" width="6" height="{height:g}" fill="{colors["accent"]}"/>',
        f'<rect id="zone-{slug}-label-field" x="{x + 18:g}" y="{y + 16:g}" width="{label_width:g}" height="30" fill="{colors["accent"]}"/>',
        _text(f"zone-{slug}-label", f"{index:02d}  {label}", x + 30, y + 21, label_width - 20, 20, colors["bg"], body_font, 12, weight=700),
        _text(f"zone-{slug}-instruction", "Dominant message / evidence", x + 20, y + 64, text_width, 26, colors["ink"], display_font, min(18, max(13, int(width / 25))), weight=600),
        f'<line id="zone-{slug}-baseline" x1="{x + 20:g}" y1="{min(y + height - 24, y + 112):g}" x2="{x + width - 20:g}" y2="{min(y + height - 24, y + 112):g}" stroke="{colors["line"]}" stroke-width="1"/>',
    ]


def _template_background(template_id: str, layout_id: str, colors: dict[str, str]) -> str:
    anchor_layout = any(token in layout_id for token in ("cover", "section", "chapter", "end"))
    if not anchor_layout:
        return colors["bg"]
    if template_id == "government_blue":
        return colors["accent"]
    if template_id == "government_red":
        return colors["muted"]
    if template_id == "medical_university" and "section" in layout_id:
        return colors["accent"]
    if template_id == "psychology_attachment":
        return colors["accent"]
    return colors["bg"]


def _template_chrome(template_id: str, layout_id: str, colors: dict[str, str]) -> list[str]:
    accent, accent2, line = colors["accent"], colors["accent2"], colors["line"]
    if template_id == "consulting_signal":
        return [
            f'<rect id="chrome-title-rule" x="56" y="128" width="1168" height="3" fill="{accent}"/>',
            f'<rect id="chrome-source-rule" x="56" y="654" width="1168" height="1" fill="{line}"/>',
            f'<rect id="chrome-answer-marker" x="56" y="78" width="10" height="32" fill="{accent2}"/>',
        ]
    if template_id == "keynote_canvas":
        return [
            f'<ellipse id="chrome-orbital-outer" cx="1160" cy="168" rx="226" ry="226" fill="none" stroke="{accent}" stroke-width="16"/>',
            f'<ellipse id="chrome-orbital-inner" cx="1160" cy="168" rx="176" ry="176" fill="none" stroke="{line}" stroke-width="2"/>',
            f'<circle id="chrome-focus-dot" cx="1060" cy="590" r="17" fill="{accent2}"/>',
        ]
    if template_id == "technical_blueprint":
        grid: list[str] = []
        for index, x in enumerate(range(80, 1280, 80), start=1):
            grid.append(f'<line id="chrome-grid-v-{index}" x1="{x}" y1="42" x2="{x}" y2="678" stroke="{line}" stroke-width="1"/>')
        for index, y in enumerate(range(80, 720, 80), start=1):
            grid.append(f'<line id="chrome-grid-h-{index}" x1="56" y1="{y}" x2="1224" y2="{y}" stroke="{line}" stroke-width="1"/>')
        grid.append(f'<circle id="chrome-active-endpoint" cx="1190" cy="80" r="7" fill="{accent2}"/>')
        return grid
    if template_id == "academic_defense":
        return [
            f'<rect id="chrome-institutional-rule" x="56" y="42" width="1168" height="5" fill="{accent}"/>',
            f'<line id="chrome-caption-rule" x1="56" y1="646" x2="1224" y2="646" stroke="{line}" stroke-width="1"/>',
            f'<circle id="chrome-reference-dot" cx="1208" cy="64" r="6" fill="{accent2}"/>',
        ]
    if template_id == "ai_ops":
        return [
            f'<rect id="chrome-ops-top" x="0" y="0" width="1280" height="8" fill="{accent}"/>',
            f'<rect id="chrome-ops-rail-primary" x="0" y="0" width="30" height="360" fill="{accent}"/>',
            f'<rect id="chrome-ops-rail-secondary" x="0" y="360" width="30" height="360" fill="{accent2}"/>',
            f'<rect id="chrome-ops-overview" x="56" y="106" width="1168" height="48" fill="{colors["surface"]}"/>',
            f'<rect id="chrome-ops-footer" x="30" y="690" width="1250" height="30" fill="{accent2}"/>',
        ]
    if template_id == "government_blue":
        is_anchor = any(token in layout_id for token in ("cover", "section", "end"))
        return [
            f'<rect id="chrome-civic-blue-top" x="0" y="0" width="1280" height="6" fill="{accent2}"/>',
            f'<rect id="chrome-civic-blue-rail" x="20" y="90" width="12" height="540" fill="{accent2}"/>',
            f'<circle id="chrome-civic-blue-orbit-1" cx="1120" cy="120" r="150" fill="none" stroke="{line}" stroke-width="2"/>',
            f'<circle id="chrome-civic-blue-orbit-2" cx="1120" cy="120" r="105" fill="none" stroke="{accent2}" stroke-width="2"/>',
            f'<rect id="chrome-civic-blue-footer" x="0" y="714" width="1280" height="6" fill="{accent2 if is_anchor else accent}"/>',
        ]
    if template_id == "government_red":
        return [
            f'<rect id="chrome-civic-red-top-left" x="0" y="0" width="760" height="6" fill="{accent}"/>',
            f'<rect id="chrome-civic-red-top-right" x="760" y="0" width="520" height="6" fill="{accent2}"/>',
            f'<rect id="chrome-civic-red-rail" x="58" y="165" width="10" height="390" fill="{accent}"/>',
            f'<line id="chrome-civic-red-axis" x1="92" y1="610" x2="1188" y2="610" stroke="{accent2}" stroke-width="2"/>',
            f'<rect id="chrome-civic-red-footer" x="0" y="714" width="1280" height="6" fill="{accent}"/>',
        ]
    if template_id == "medical_university":
        return [
            f'<rect id="chrome-medical-header" x="0" y="0" width="1280" height="70" fill="{accent}"/>',
            f'<rect id="chrome-medical-signal" x="0" y="0" width="7" height="720" fill="{accent2}"/>',
            f'<polyline id="chrome-medical-pulse" points="42,36 92,36 108,18 126,55 143,29 162,43 205,43" fill="none" stroke="{colors["bg"]}" stroke-width="3"/>',
            f'<rect id="chrome-medical-message-band" x="40" y="88" width="1200" height="46" fill="{colors["surface"]}"/>',
            f'<line id="chrome-medical-footer" x1="40" y1="660" x2="1240" y2="660" stroke="{line}" stroke-width="2"/>',
        ]
    if template_id == "pixel_retro":
        return [
            f'<rect id="chrome-pixel-top-main" x="24" y="18" width="1232" height="5" fill="{accent}"/>',
            f'<rect id="chrome-pixel-top-shadow" x="24" y="27" width="1232" height="2" fill="{line}"/>',
            f'<rect id="chrome-pixel-corner-a" x="24" y="44" width="18" height="18" fill="{accent}"/>',
            f'<rect id="chrome-pixel-corner-b" x="48" y="44" width="12" height="12" fill="{accent}"/>',
            f'<rect id="chrome-pixel-corner-c" x="1220" y="44" width="18" height="18" fill="{accent2}"/>',
            f'<rect id="chrome-pixel-bottom" x="24" y="684" width="1232" height="5" fill="{accent}"/>',
            f'<rect id="chrome-pixel-status" x="1100" y="670" width="138" height="8" fill="{accent2}"/>',
        ]
    if template_id == "psychology_attachment":
        return [
            f'<rect id="chrome-psychology-rail" x="0" y="0" width="9" height="720" fill="{accent}"/>',
            f'<line id="chrome-psychology-divider" x1="48" y1="128" x2="1230" y2="128" stroke="{line}" stroke-width="2"/>',
            f'<circle id="chrome-psychology-orbit-1" cx="1110" cy="130" r="150" fill="none" stroke="{accent2}" stroke-width="2"/>',
            f'<circle id="chrome-psychology-orbit-2" cx="1110" cy="130" r="102" fill="none" stroke="{line}" stroke-width="2"/>',
            f'<circle id="chrome-psychology-orbit-3" cx="1110" cy="130" r="56" fill="none" stroke="{accent2}" stroke-width="2"/>',
            f'<rect id="chrome-psychology-footer" x="48" y="654" width="1182" height="2" fill="{line}"/>',
        ]
    return [
        f'<rect id="chrome-editorial-rail" x="1192" y="42" width="32" height="636" fill="{accent}"/>',
        f'<polygon id="chrome-editorial-cut" points="1118,42 1192,42 1192,116" fill="{accent2}"/>',
        f'<line id="chrome-editorial-rule" x1="56" y1="126" x2="1080" y2="126" stroke="{line}" stroke-width="2"/>',
    ]


def _text(
    element_id: str,
    value: str,
    x: float,
    top: float,
    width: float,
    height: float,
    fill: str,
    font_family: str,
    font_size: float,
    *,
    weight: int = 400,
    anchor: str = "start",
    letter_spacing: float = 0,
) -> str:
    safe_value = _xml(value)
    baseline = top + min(height, font_size)
    visual_x = x + width if anchor == "end" else x
    attrs = (
        f'id="{_xml(element_id)}" x="{visual_x:g}" y="{baseline:g}" fill="{fill}" '
        f'font-family="{_xml(font_family)}" font-size="{font_size:g}" font-weight="{weight}" '
        f'text-anchor="{anchor}" data-text="true" data-value="{safe_value}" '
        f'data-box="{x:g},{top:g},{max(1, width):g},{max(1, height):g}" '
        f'data-font-family="{_xml(font_family)}" data-font-size="{font_size:g}"'
    )
    if letter_spacing:
        attrs += f' letter-spacing="{letter_spacing:g}"'
    return f'<text {attrs}>{safe_value}</text>'


def _resolve_theme(theme: ThemeTokens | Mapping[str, Any] | None, fallback: ThemeTokens) -> ThemeTokens:
    if isinstance(theme, ThemeTokens):
        return theme
    if isinstance(theme, Mapping):
        return ThemeTokens.from_dict(dict(theme))
    return fallback


def _palette(theme: ThemeTokens) -> dict[str, str]:
    fallback = {
        "bg": "#FFFFFF",
        "surface": "#F4F7FA",
        "ink": "#102A43",
        "muted": "#627D98",
        "accent": "#2563EB",
        "accent2": "#F59E0B",
        "line": "#CBD5E1",
    }
    return {name: _safe_hex(theme.palette.get(name), value) for name, value in fallback.items()}


def _box(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, width, height = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x = min(max(0.0, x), float(CANVAS_WIDTH - 1))
    y = min(max(0.0, y), float(CANVAS_HEIGHT - 1))
    width = min(width, float(CANVAS_WIDTH) - x)
    height = min(height, float(CANVAS_HEIGHT) - y)
    return x, y, width, height


def _safe_hex(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if _HEX_RE.fullmatch(text) else fallback


def _font(value: Any, fallback: str) -> str:
    text = str(value or "").strip().split(",", 1)[0].strip(" '\"")
    return text or fallback


def _trim(value: str, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized if len(normalized) <= limit else normalized[: max(1, limit - 1)].rstrip() + "…"


def _xml(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    return normalized or "zone"


__all__ = ["render_template_reference_svg", "resolve_template_layout"]
