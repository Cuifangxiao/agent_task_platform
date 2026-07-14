"""Authoritative deck contracts and deterministic normalization helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720

COMPONENTS = {
    "cover",
    "agenda",
    "section",
    "content-basic",
    "kpi-cards",
    "timeline",
    "steps",
    "comparison",
    "quadrant",
    "chart-takeaway",
    "data-table",
    "quote",
    "summary",
    "end",
}

ROLE_VALUES = {"hook", "context", "evidence", "turn", "solution", "action", "summary"}

PLACEHOLDER_PATTERNS = (
    "key point",
    "todo",
    "tbd",
    "lorem",
    "placeholder",
    "sample text",
    "suggested visual",
    "示例",
    "占位",
    "待补充",
)

COMPONENT_LIMITS: dict[str, dict[str, int]] = {
    "cover": {"title_chars": 42, "subtitle_chars": 96},
    "agenda": {"items": 7, "item_chars": 24},
    "section": {"title_chars": 36, "subtitle_chars": 72},
    "content-basic": {"bullets": 4, "bullet_chars": 56, "body_chars": 120},
    "kpi-cards": {"items": 4, "value_chars": 10, "label_chars": 20},
    "timeline": {"items": 5, "item_chars": 42},
    "steps": {"items": 5, "item_chars": 42},
    "comparison": {"items": 4, "item_chars": 54},
    "quadrant": {"items": 4, "item_chars": 42},
    "chart-takeaway": {"takeaway_chars": 96},
    "data-table": {"rows": 5, "cols": 4, "cell_chars": 24},
    "quote": {"quote_chars": 120, "speaker_chars": 28},
    "summary": {"items": 4, "item_chars": 48},
    "end": {"title_chars": 36, "subtitle_chars": 72},
}


@dataclass
class ThemeTokens:
    name: str
    palette: dict[str, str]
    fonts: dict[str, Any]
    type_scale: dict[str, int]
    spacing_unit: int = 8
    radius: int = 12
    motif: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ThemeTokens":
        palette = dict(raw.get("palette") or {})
        fonts = dict(raw.get("fonts") or {})
        type_scale = dict(raw.get("type_scale") or {})
        return cls(
            name=str(raw.get("name") or "Editorial Signal").strip(),
            palette={
                "bg": str(palette.get("bg") or "#F7F3EA"),
                "surface": str(palette.get("surface") or "#FFFFFF"),
                "ink": str(palette.get("ink") or "#14213D"),
                "muted": str(palette.get("muted") or "#607080"),
                "accent": str(palette.get("accent") or "#2A9D8F"),
                "accent2": str(palette.get("accent2") or "#E76F51"),
                "line": str(palette.get("line") or "#D9DEE4"),
            },
            fonts={
                "display": str(fonts.get("display") or "Noto Sans SC"),
                "body": str(fonts.get("body") or "Noto Sans SC"),
                "display_weight": int(fonts.get("display_weight") or 700),
            },
            type_scale={
                "h1": int(type_scale.get("h1") or 44),
                "h2": int(type_scale.get("h2") or 30),
                "body": int(type_scale.get("body") or 18),
                "caption": int(type_scale.get("caption") or 13),
            },
            spacing_unit=int(raw.get("spacing_unit") or 8),
            radius=int(raw.get("radius") or 12),
            motif=str(raw.get("motif") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SlideSpec:
    index: int
    role: str
    component: str
    title: str
    content: dict[str, Any] = field(default_factory=dict)
    chart: dict[str, Any] | None = None
    notes: str = ""
    sources: list[str] = field(default_factory=list)
    layout_variant: str = "balanced"
    design: dict[str, Any] = field(default_factory=dict)
    spec_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeckSpec:
    title: str
    narrative: str
    audience: str
    goal: str
    language: str
    theme: ThemeTokens
    slides: list[SlideSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["theme"] = self.theme.to_dict()
        data["slides"] = [slide.to_dict() for slide in self.slides]
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeckSpec":
        return cls(
            title=str(raw.get("title") or "Untitled presentation").strip(),
            narrative=str(raw.get("narrative") or "").strip(),
            audience=str(raw.get("audience") or "").strip(),
            goal=str(raw.get("goal") or "").strip(),
            language=str(raw.get("language") or "zh").strip() or "zh",
            theme=ThemeTokens.from_dict(raw.get("theme") or {}),
            slides=[
                SlideSpec(
                    index=int(item.get("index") or index + 1),
                    role=_normalize_role(item.get("role")),
                    component=_normalize_component(item.get("component")),
                    title=str(item.get("title") or f"Slide {index + 1}").strip(),
                    content=dict(item.get("content") or {}),
                    chart=item.get("chart") if isinstance(item.get("chart"), dict) else None,
                    notes=str(item.get("notes") or "").strip(),
                    sources=[str(source).strip() for source in item.get("sources") or [] if str(source).strip()],
                    layout_variant=_normalize_layout_variant(item.get("layout_variant")),
                    design=_normalize_design(item.get("design")),
                    spec_hash=str(item.get("spec_hash") or "").strip(),
                )
                for index, item in enumerate(raw.get("slides") or [])
                if isinstance(item, dict)
            ],
        )


DECK_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "narrative", "audience", "goal", "language", "theme", "slides"],
    "properties": {
        "title": {"type": "string"},
        "narrative": {"type": "string"},
        "audience": {"type": "string"},
        "goal": {"type": "string"},
        "language": {"type": "string"},
        "theme": {"type": "object"},
        "slides": {"type": "array", "items": {"$ref": "#/$defs/SlideSpec"}},
    },
    "$defs": {
        "SlideSpec": {
            "type": "object",
            "required": ["index", "role", "component", "title", "content", "sources"],
            "properties": {
                "index": {"type": "integer", "minimum": 1},
                "role": {"type": "string", "enum": sorted(ROLE_VALUES)},
                "component": {"type": "string", "enum": sorted(COMPONENTS)},
                "title": {"type": "string"},
                "content": {"type": "object"},
                "chart": {"type": ["object", "null"]},
                "notes": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "layout_variant": {"type": "string", "enum": ["balanced", "editorial", "split"]},
                "design": {"type": "object"},
                "spec_hash": {"type": "string"},
            },
        }
    },
}


def default_theme() -> ThemeTokens:
    return ThemeTokens.from_dict(
        {
            "name": "Editorial Signal",
            "palette": {
                "bg": "#F7F3EA",
                "surface": "#FFFFFF",
                "ink": "#14213D",
                "muted": "#58677A",
                "accent": "#2A9D8F",
                "accent2": "#E76F51",
                "line": "#D8D3C8",
            },
            "fonts": {"display": "Noto Sans SC", "body": "Noto Sans SC", "display_weight": 700},
            "type_scale": {"h1": 44, "h2": 30, "body": 18, "caption": 13},
            "spacing_unit": 8,
            "radius": 12,
            "motif": "calm editorial blocks with precise dividers",
        }
    )


def normalize_slide_spec(
    raw: dict[str, Any],
    *,
    index: int,
    outline: dict[str, Any] | None = None,
    deck_title: str = "",
    narrative: str = "",
) -> SlideSpec:
    """Normalize one semantic slide payload into the authoritative SlideSpec."""

    outline = outline or {}
    source = _extract_single_slide_source(raw, index)
    component = _normalize_component(
        source.get("component")
        or outline.get("component")
        or _component_from_layout(source.get("layout") or outline.get("layout"), index)
    )
    title = str(source.get("title") or outline.get("title") or (deck_title if index == 1 else "")).strip()
    if not title:
        title = f"Slide {index}"

    key_message = str(
        source.get("key_message")
        or source.get("subtitle")
        or outline.get("key_message")
        or narrative
        or ""
    ).strip()
    bullets = _normalize_string_list(source.get("bullets") or source.get("items"))
    content = source.get("content") if isinstance(source.get("content"), dict) else {}
    if not content:
        content = _fallback_content(component, title, key_message, bullets, index)

    notes = str(source.get("notes") or source.get("speaker_notes") or key_message or "").strip()
    sources = [str(item).strip() for item in source.get("sources") or [] if str(item).strip()]

    return SlideSpec(
        index=index,
        role=_normalize_role(source.get("role") or outline.get("role"), index=index),
        component=component,
        title=title,
        content=content,
        chart=source.get("chart") if isinstance(source.get("chart"), dict) else None,
        notes=notes,
        sources=sources,
        layout_variant=_normalize_layout_variant(source.get("layout_variant")),
        design=_normalize_design(source.get("design")),
    )


def slide_text_values(slide: SlideSpec) -> list[str]:
    values = [slide.title, slide.notes]

    def walk(value: Any) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(slide.content)
    if slide.chart:
        walk(slide.chart)
    return [value for value in values if value]


def has_placeholder_text(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return any(pattern in lowered for pattern in PLACEHOLDER_PATTERNS)


def slide_has_placeholder(slide: SlideSpec) -> bool:
    return any(has_placeholder_text(value) for value in slide_text_values(slide))


def _fallback_content(component: str, title: str, key_message: str, bullets: list[str], index: int) -> dict[str, Any]:
    if component == "cover":
        return {"subtitle": key_message, "eyebrow": "Presentation", "meta": ""}
    if component == "section":
        return {"subtitle": key_message}
    if component == "agenda":
        return {"items": bullets or [title]}
    if component == "kpi-cards":
        return {"cards": [{"value": "", "label": title, "caption": key_message}]}
    if component == "steps":
        return {"steps": bullets or [key_message or title]}
    if component == "timeline":
        return {"items": bullets or [key_message or title]}
    if component == "comparison":
        left, right = _split_comparison_items(bullets)
        return {"left_title": "Current", "right_title": "Target", "left": left, "right": right}
    if component == "quote":
        return {"quote": key_message or title, "speaker": ""}
    if component == "summary":
        return {"items": bullets or [key_message or title]}
    if component == "end":
        return {"subtitle": key_message or "Thank you"}
    if component == "chart-takeaway":
        return {"takeaway": key_message or title}
    if component == "data-table":
        return {"columns": ["Item", "Signal"], "rows": [[title, key_message or ""]]}
    if component == "quadrant":
        return {"items": bullets or [key_message or title]}
    return {"body": key_message, "bullets": bullets}


def _extract_single_slide_source(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if isinstance(raw.get("slide"), dict):
        return raw["slide"]
    raw_slides = raw.get("slides")
    if isinstance(raw_slides, list):
        for item in raw_slides:
            if isinstance(item, dict) and int(item.get("index") or 0) == index:
                return item
        offset = index - 1
        if 0 <= offset < len(raw_slides) and isinstance(raw_slides[offset], dict):
            return raw_slides[offset]
    return raw if isinstance(raw, dict) else {}


def _normalize_component(value: Any) -> str:
    component = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "title": "cover",
        "content": "content-basic",
        "basic": "content-basic",
        "cards": "kpi-cards",
        "table": "data-table",
        "chart": "chart-takeaway",
    }
    component = aliases.get(component, component)
    return component if component in COMPONENTS else "content-basic"


def _component_from_layout(value: Any, index: int) -> str:
    layout = str(value or "").strip().lower()
    if index == 1 or layout == "title":
        return "cover"
    if layout == "section":
        return "section"
    if layout == "comparison":
        return "comparison"
    if layout == "summary":
        return "summary"
    return "content-basic"


def _normalize_role(value: Any, *, index: int = 0) -> str:
    role = str(value or "").strip().lower()
    aliases = {"intro": "hook", "background": "context", "next": "action", "ending": "summary"}
    role = aliases.get(role, role)
    if role in ROLE_VALUES:
        return role
    if index == 1:
        return "hook"
    return "summary" if role == "end" else "evidence"


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_layout_variant(value: Any) -> str:
    variant = str(value or "balanced").strip().lower()
    return variant if variant in {"balanced", "editorial", "split"} else "balanced"


def _normalize_design(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "emphasis": {"primary", "contrast", "left", "right", "center", "data"},
        "density": {"airy", "balanced", "compact"},
        "visual_hierarchy": {"headline", "evidence", "comparison", "sequence"},
        "accent_slot": {"title", "panel", "metric", "marker"},
    }
    normalized: dict[str, Any] = {}
    for key, values in allowed.items():
        item = str(value.get(key) or "").strip().lower()
        if item in values:
            normalized[key] = item
    return normalized


def _split_comparison_items(items: list[str]) -> tuple[list[str], list[str]]:
    left: list[str] = []
    right: list[str] = []
    for item in items:
        if "|" in item:
            a, b = item.split("|", 1)
            left.append(a.strip())
            right.append(b.strip())
        elif len(left) <= len(right):
            left.append(item)
        else:
            right.append(item)
    return left or items[:1], right or items[1:2]


def _soft_limit(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if not max_chars or len(text) <= max_chars:
        return text
    separators = ["。", ".", "；", ";", "，", ",", " "]
    candidate = text[:max_chars].rstrip()
    for separator in separators:
        pos = candidate.rfind(separator)
        if pos >= max(12, max_chars // 2):
            return candidate[: pos + 1].rstrip()
    return candidate.rstrip()
