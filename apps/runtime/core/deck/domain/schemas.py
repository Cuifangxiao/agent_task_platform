"""Versioned, serializable domain schemas for the HTML-first deck pipeline.

The LangGraph state stores ``model_dump(mode="json")`` dictionaries rather
than Pydantic instances.  This keeps checkpoints transport-safe while every
LLM boundary still has a strict, documented schema.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "deck-state.v6"
PROMPT_VERSION = "html-deck.v8"
RENDERER_VERSION = "chromium-html.v2"
CANVAS_WIDTH = 1600
CANVAS_HEIGHT = 900
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _color_luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _color_contrast(first: str, second: str) -> float:
    high, low = sorted((_color_luminance(first), _color_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


NarrativeText = Annotated[str, Field(min_length=1, max_length=500)]
ContentItemText = Annotated[str, Field(min_length=1, max_length=180)]
EmphasisText = Annotated[str, Field(min_length=1, max_length=180)]
IconKey = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
KeywordText = Annotated[str, Field(min_length=1, max_length=80)]
DesignElementText = Annotated[str, Field(min_length=1, max_length=240)]


class StrictModel(BaseModel):
    """Base model used at untrusted model-output boundaries."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GlobalStyleSpec(StrictModel):
    """The single, model-planned palette inherited by every page."""

    primary_color: str
    secondary_color: str
    accent_color: str
    background_color: str
    surface_color: str
    text_primary: str
    text_secondary: str
    text_muted: str
    border_color: str
    success_color: str

    @field_validator(
        "primary_color",
        "secondary_color",
        "accent_color",
        "background_color",
        "surface_color",
        "text_primary",
        "text_secondary",
        "text_muted",
        "border_color",
        "success_color",
    )
    @classmethod
    def valid_hex_color(cls, value: str) -> str:
        if not HEX_COLOR_RE.fullmatch(value):
            raise ValueError("colors must use six-digit #RRGGBB notation")
        return value.upper()

    @model_validator(mode="after")
    def readable_foreground(self) -> "GlobalStyleSpec":
        for surface_name, surface in (
            ("background_color", self.background_color),
            ("surface_color", self.surface_color),
        ):
            if _color_contrast(surface, self.text_primary) < 4.5:
                raise ValueError(
                    f"text_primary must have at least 4.5:1 contrast on {surface_name}"
                )
            if _color_contrast(surface, self.text_secondary) < 3.0:
                raise ValueError(
                    f"text_secondary must have at least 3:1 contrast on {surface_name}"
                )
            if _color_contrast(surface, self.text_muted) < 3.0:
                raise ValueError(
                    f"text_muted must have at least 3:1 contrast on {surface_name}"
                )
            for signal_name, signal in (
                ("primary_color", self.primary_color),
                ("secondary_color", self.secondary_color),
                ("accent_color", self.accent_color),
                ("success_color", self.success_color),
            ):
                if _color_contrast(surface, signal) < 3.0:
                    raise ValueError(
                        f"{signal_name} must have at least 3:1 contrast on {surface_name}"
                    )
        return self


class SlideRole(str, Enum):
    COVER = "cover"
    CONTEXT = "context"
    PROBLEM = "problem"
    EVIDENCE = "evidence"
    INSIGHT = "insight"
    SOLUTION = "solution"
    PROCESS = "process"
    DECISION = "decision"
    SECTION = "section"
    CLOSING = "closing"


class LayoutFamily(str, Enum):
    COVER_MINIMAL = "cover_minimal"
    SECTION_BREAK = "section_break"
    STATEMENT_FOCUS = "statement_focus"
    EDITORIAL_SPLIT = "editorial_split"
    THREE_POINTS = "three_points"
    COMPARISON = "comparison"
    PROCESS = "process"
    TIMELINE = "timeline"
    KEY_METRIC = "key_metric"
    CLOSING_RESOLUTION = "closing_resolution"


class PageRhythm(str, Enum):
    ANCHOR = "anchor"
    DENSE = "dense"
    BREATHING = "breathing"


class ContentMode(str, Enum):
    LIST = "list"
    PARAGRAPH = "paragraph"
    DATA = "data"
    MIXED = "mixed"


class DeckOutlineMetadata(StrictModel):
    author: str = Field(default="", max_length=120)
    date: str = Field(default="", max_length=120)
    language: str = Field(min_length=2, max_length=20)
    keywords: list[KeywordText] = Field(default_factory=list, max_length=12)
    design_style: str = Field(min_length=1, max_length=240)
    source_summary: str = Field(min_length=1, max_length=1_200)
    global_style_spec: GlobalStyleSpec


class SlideOutlineSpec(StrictModel):
    """One page planned by the deck-wide outline model."""

    id: str = Field(pattern=r"^s\d{2}$")
    title: str = Field(min_length=1, max_length=180)
    type: SlideRole
    key_points: list[ContentItemText] = Field(default_factory=list, max_length=6)
    layout_suggestion: LayoutFamily
    content_mode: ContentMode
    content_note: str = Field(min_length=1, max_length=1_200)


class DeckOutlineSpec(StrictModel):
    """The only model output produced before pages fan out."""

    title: str = Field(min_length=1, max_length=180)
    subtitle: str = Field(default="", max_length=240)
    metadata: DeckOutlineMetadata
    slides: list[SlideOutlineSpec] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_slide_roster(self) -> "DeckOutlineSpec":
        identifiers = [slide.id for slide in self.slides]
        expected = [f"s{index:02d}" for index in range(1, len(self.slides) + 1)]
        if identifiers != expected:
            raise ValueError("slide ids must be unique, consecutive, and ordered from s01")
        return self


class SlideDesignRationale(StrictModel):
    """Short, auditable design decisions rather than hidden chain-of-thought."""

    request_analysis: str = Field(min_length=1, max_length=800)
    content_structure: str = Field(min_length=1, max_length=800)
    layout_reasoning: str = Field(min_length=1, max_length=800)
    design_choices: str = Field(min_length=1, max_length=800)
    consistency_check: str = Field(min_length=1, max_length=800)


class SlideTypographyPlan(StrictModel):
    font_family: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=160)
    heading: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=160)
    caption: str = Field(min_length=1, max_length=160)


class SlideDesignSpecifications(StrictModel):
    layout_type: LayoutFamily
    color_scheme: GlobalStyleSpec
    typography: SlideTypographyPlan
    key_elements: list[DesignElementText] = Field(min_length=1, max_length=12)
    visual_hierarchy: list[DesignElementText] = Field(min_length=1, max_length=12)


class SlideDesignPlan(StrictModel):
    """Per-page output of the visual-planning model."""

    slide_id: str = Field(pattern=r"^s\d{2}$")
    design_rationale: SlideDesignRationale
    design_specifications: SlideDesignSpecifications


class SlidePlan(StrictModel):
    slide_id: str = Field(pattern=r"^s\d{2,3}$")
    index: int = Field(ge=1, le=30)
    role: SlideRole
    assertion_title: str = Field(min_length=1, max_length=120)
    takeaway: str = Field(min_length=1, max_length=320)
    content_intent: str = Field(min_length=1, max_length=500)
    layout_family: LayoutFamily
    rhythm: PageRhythm
    transition_from_previous: str = Field(default="", max_length=240)


class DeckPlan(StrictModel):
    deck_title: str = Field(min_length=1, max_length=180)
    core_message: str = Field(min_length=1, max_length=500)
    narrative_arc: list[NarrativeText] = Field(min_length=1, max_length=12)
    slides: list[SlidePlan] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_roster(self) -> "DeckPlan":
        indexes = [slide.index for slide in self.slides]
        if indexes != list(range(1, len(self.slides) + 1)):
            raise ValueError("slide indexes must be consecutive and one-based")
        ids = [slide.slide_id for slide in self.slides]
        if len(ids) != len(set(ids)):
            raise ValueError("slide_id values must be unique")
        if self.slides[0].role is not SlideRole.COVER:
            raise ValueError("the first slide must have role=cover")
        if len(self.slides) > 1 and self.slides[-1].role is not SlideRole.CLOSING:
            raise ValueError("the final slide must have role=closing")
        return self


class PaletteSpec(StrictModel):
    background: str
    surface: str
    text: str
    muted: str
    accent: str
    secondary: str
    divider: str

    @field_validator("background", "surface", "text", "muted", "accent", "secondary", "divider")
    @classmethod
    def valid_hex_color(cls, value: str) -> str:
        if not HEX_COLOR_RE.fullmatch(value):
            raise ValueError("colors must use six-digit #RRGGBB notation")
        return value.upper()


class TypeToken(StrictModel):
    size_px: int = Field(ge=14, le=120)
    weight: int = Field(ge=300, le=900, multiple_of=100)
    line_height: float = Field(ge=0.9, le=1.8)
    max_lines: int = Field(ge=1, le=12)


class TypographySpec(StrictModel):
    chinese_font: str = Field(min_length=1, max_length=120)
    latin_font: str = Field(min_length=1, max_length=120)
    cover_title: TypeToken
    page_title: TypeToken
    body: TypeToken
    caption: TypeToken

    @field_validator("chinese_font", "latin_font")
    @classmethod
    def safe_font_name(cls, value: str) -> str:
        if not re.fullmatch(r"[\w\u3400-\u9fff .+-]{1,120}", value):
            raise ValueError("font names may contain only letters, numbers, spaces, dots, plus, and hyphen")
        return value

    @model_validator(mode="after")
    def minimum_readable_sizes(self) -> "TypographySpec":
        if self.cover_title.size_px < 66:
            raise ValueError("cover_title must be at least 66px")
        if self.page_title.size_px < 46:
            raise ValueError("page_title must be at least 46px")
        if self.body.size_px < 21:
            raise ValueError("body must be at least 21px")
        if self.caption.size_px < 16:
            raise ValueError("caption must be at least 16px")
        return self


class SpacingSpec(StrictModel):
    canvas_margin: int = Field(ge=48, le=180)
    base_unit: int = Field(ge=4, le=16)
    column_gap: int = Field(ge=16, le=96)


class ShapeStyleSpec(StrictModel):
    radius: int = Field(ge=0, le=36)
    line_width: int = Field(ge=1, le=4)
    shadow: Literal["none", "soft", "offset"] = "none"


class DesignSpec(StrictModel):
    theme_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    palette: PaletteSpec
    global_style_spec: GlobalStyleSpec | None = None
    typography: TypographySpec
    spacing: SpacingSpec
    shape_style: ShapeStyleSpec
    icon_style: str = Field(min_length=1, max_length=160)
    background_style: str = Field(min_length=1, max_length=240)
    motif: str = Field(min_length=1, max_length=240)
    density_policy: str = Field(min_length=1, max_length=320)
    canvas_width: Literal[1600] = CANVAS_WIDTH
    canvas_height: Literal[900] = CANVAS_HEIGHT


class ContentBlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    BULLET_LIST = "bullet_list"
    METRIC = "metric"
    QUOTE = "quote"
    CALLOUT = "callout"
    COMPARISON_SIDE = "comparison_side"
    PROCESS_STEP = "process_step"
    TIMELINE_ITEM = "timeline_item"
    ICON_LABEL = "icon_label"


class ContentBlock(StrictModel):
    type: ContentBlockType
    text: str = Field(default="", max_length=700)
    items: list[ContentItemText] = Field(default_factory=list, max_length=8)
    label: str = Field(default="", max_length=120)
    value: str = Field(default="", max_length=80)
    side: Literal["", "left", "right"] = ""
    ordinal: int = Field(default=0, ge=0, le=12)

    @model_validator(mode="after")
    def has_payload(self) -> "ContentBlock":
        if not (self.text or self.items or self.label or self.value):
            raise ValueError("content block must contain text, items, label, or value")
        return self


class SlideContentSpec(StrictModel):
    slide_id: str = Field(pattern=r"^s\d{2,3}$")
    assertion_title: str = Field(min_length=1, max_length=120)
    takeaway: str = Field(min_length=1, max_length=320)
    content_blocks: list[ContentBlock] = Field(min_length=1, max_length=10)
    emphasis: list[EmphasisText] = Field(default_factory=list, max_length=6)
    suggested_icon_keys: list[IconKey] = Field(default_factory=list, max_length=8)


class HtmlSlideSpec(StrictModel):
    slide_id: str = Field(pattern=r"^s\d{2,3}$")
    layout_family: LayoutFamily
    layout_variant: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    composition_summary: str = Field(min_length=1, max_length=360)
    body_html: str = Field(min_length=1, max_length=40_000)
    page_css: str = Field(default="", max_length=40_000)
    icon_keys: list[IconKey] = Field(default_factory=list, max_length=10)
    estimated_density: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def fragment_only(self) -> "HtmlSlideSpec":
        # Match actual tag boundaries.  A plain ``"<head" in text`` check also
        # rejects the safe and commonly used ``<header>`` element.
        if re.search(
            r"<\s*(?:!doctype\b|html(?:\s|>)|head(?:\s|>)|script(?:\s|>)|"
            r"style(?:\s|>)|template(?:\s|>)|textarea(?:\s|>)|title(?:\s|>)|"
            r"iframe(?:\s|>)|object(?:\s|>)|embed(?:\s|>))",
            self.body_html,
            flags=re.IGNORECASE,
        ):
            raise ValueError("body_html must be a safe body fragment, not a full document")
        return self


class DiagnosticSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCategory(str, Enum):
    SECURITY = "security"
    CONTENT = "content"
    ENVIRONMENT = "environment"


class RendererDiagnostic(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    category: DiagnosticCategory
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    selector: str = Field(default="", max_length=500)
    message: str = Field(min_length=1, max_length=700)
    suggestion: str = Field(default="", max_length=700)
    box: dict[str, float] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class SlideArtifactMetadata(StrictModel):
    slide_id: str = Field(pattern=r"^s\d{2,3}$")
    index: int = Field(ge=1, le=30)
    title: str = Field(min_length=1, max_length=180)
    layout_family: LayoutFamily
    layout_variant: str = Field(min_length=1, max_length=80)
    composition_summary: str = Field(min_length=1, max_length=360)
    design_plan_path: str = Field(default="", max_length=500)
    html_spec_path: str = Field(default="", max_length=500)
    html_path: str = Field(min_length=1, max_length=500)
    preview_path: str = Field(min_length=1, max_length=500)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    html_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_id: str = Field(min_length=1, max_length=160)
    renderer_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(min_length=1, max_length=80)
    base_css_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    icon_registry_version: str = Field(min_length=1, max_length=160)
    render_environment: dict[str, Any] = Field(default_factory=dict)
    model_metadata: dict[str, Any] = Field(default_factory=dict)
    repair_attempts: int = Field(ge=0, le=10)
    fallback_used: bool = False
    diagnostics_path: str = Field(default="", max_length=500)


__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "ContentMode",
    "ContentBlock",
    "ContentBlockType",
    "DeckOutlineMetadata",
    "DeckOutlineSpec",
    "DeckPlan",
    "DesignSpec",
    "DiagnosticCategory",
    "DiagnosticSeverity",
    "HtmlSlideSpec",
    "GlobalStyleSpec",
    "LayoutFamily",
    "PROMPT_VERSION",
    "PageRhythm",
    "PaletteSpec",
    "RENDERER_VERSION",
    "RendererDiagnostic",
    "SCHEMA_VERSION",
    "ShapeStyleSpec",
    "SlideArtifactMetadata",
    "SlideContentSpec",
    "SlideDesignPlan",
    "SlideDesignRationale",
    "SlideDesignSpecifications",
    "SlideOutlineSpec",
    "SlidePlan",
    "SlideRole",
    "SlideTypographyPlan",
    "SpacingSpec",
    "TypeToken",
    "TypographySpec",
]
