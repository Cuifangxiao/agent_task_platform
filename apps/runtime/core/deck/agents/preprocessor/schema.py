"""Strict contracts owned exclusively by the PPT preprocessor.

The preprocessor stops at a semantic page roster.  Layout identifiers,
geometry, HTML, SVG, and renderer constraints intentionally do not exist in
this module, so the page-mapping step cannot accidentally select a layout.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ConstraintText = Annotated[str, Field(min_length=1, max_length=240)]
PreferenceText = Annotated[str, Field(min_length=1, max_length=120)]
KeyPointText = Annotated[str, Field(min_length=1, max_length=240)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GlobalMetaInterpretation(StrictModel):
    """Model-owned interpretation of the user's natural-language request."""

    title: str = Field(min_length=1, max_length=180)
    subtitle: str = Field(default="", max_length=180)
    presenter: str = Field(default="", max_length=120)
    audience: str = Field(min_length=1, max_length=180)
    scene: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    goal: str = Field(min_length=1, max_length=300)
    audience_outcome: str = Field(min_length=1, max_length=300)
    communication_job: str = Field(min_length=1, max_length=500)
    language: str = Field(min_length=2, max_length=20)
    target_page_count: int = Field(ge=1, le=30)
    aspect_ratio: Literal["16:9"] = "16:9"
    tone: str = Field(min_length=1, max_length=120)
    content_constraints: list[ConstraintText] = Field(default_factory=list, max_length=16)
    brand_preferences: list[PreferenceText] = Field(default_factory=list, max_length=8)
    color_preferences: list[PreferenceText] = Field(default_factory=list, max_length=8)
    style_preferences: list[PreferenceText] = Field(default_factory=list, max_length=8)
    source_mode: Literal["general_knowledge", "source_grounded"]


class GlobalMeta(GlobalMetaInterpretation):
    """Validated global metadata plus runtime-owned source material."""

    source_text: str = Field(default="", max_length=60_000)


class OutlineTopic(StrictModel):
    """One semantic topic; it is not a slide or layout instruction."""

    topic_id: str = Field(pattern=r"^t\d{2,3}$")
    title: str = Field(min_length=1, max_length=180)
    intent: str = Field(min_length=1, max_length=360)
    key_points: list[KeyPointText] = Field(default_factory=list, max_length=8)


class OutlineSection(StrictModel):
    """One chapter in the document-like hierarchy."""

    section_id: str = Field(pattern=r"^sec\d{2,3}$")
    title: str = Field(min_length=1, max_length=180)
    purpose: str = Field(min_length=1, max_length=360)
    topics: list[OutlineTopic] = Field(min_length=1, max_length=12)


class LogicalOutline(StrictModel):
    """High-level content hierarchy with deliberately no page concept."""

    title: str = Field(min_length=1, max_length=180)
    central_thesis: str = Field(min_length=1, max_length=500)
    narrative_strategy: str = Field(min_length=1, max_length=500)
    sections: list[OutlineSection] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_outline_ids(self) -> "LogicalOutline":
        section_ids = [section.section_id for section in self.sections]
        topic_ids = [topic.topic_id for section in self.sections for topic in section.topics]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section_id values must be unique")
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("topic_id values must be unique")
        return self


class PageCategory(str, Enum):
    COVER = "cover"
    CATALOG = "catalog"
    SECTION = "section"
    CONTENT = "content"
    CHART = "chart"
    SUMMARY = "summary"
    THANKS = "thanks"


class PagePlanItem(StrictModel):
    """The complete step-four output for one page.

    Do not add content payloads or layout fields here.  Those belong to the
    processor phase after a layout has been selected.
    """

    page_no: int = Field(ge=1, le=30)
    page_category: PageCategory
    topic: str = Field(min_length=1, max_length=180)


class PagePlan(StrictModel):
    """A page sequence produced only from an outline and asset A."""

    scene: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    pages: list[PagePlanItem] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_page_numbers(self) -> "PagePlan":
        page_numbers = [page.page_no for page in self.pages]
        if page_numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("page_no values must be consecutive and one-based")
        return self


class SceneNarrativeTemplate(StrictModel):
    """Asset A: whole-deck narrative constraints, never a page layout."""

    scene: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    mandatory_page_types: list[PageCategory] = Field(min_length=1, max_length=8)
    suggest_page_order: list[PageCategory] = Field(min_length=1, max_length=30)
    page_min: int = Field(ge=1, le=30)
    page_max: int = Field(ge=1, le=30)

    @field_validator("mandatory_page_types")
    @classmethod
    def unique_mandatory_types(cls, value: list[PageCategory]) -> list[PageCategory]:
        if len(value) != len(set(value)):
            raise ValueError("mandatory_page_types must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> "SceneNarrativeTemplate":
        if self.page_min > self.page_max:
            raise ValueError("page_min must not exceed page_max")
        if self.page_min < len(self.mandatory_page_types):
            raise ValueError("page_min cannot be smaller than the mandatory page count")
        missing = set(self.mandatory_page_types) - set(self.suggest_page_order)
        if missing:
            raise ValueError("suggest_page_order must include every mandatory page type")
        return self


__all__ = [
    "GlobalMeta",
    "GlobalMetaInterpretation",
    "LogicalOutline",
    "OutlineSection",
    "OutlineTopic",
    "PageCategory",
    "PagePlan",
    "PagePlanItem",
    "SceneNarrativeTemplate",
]
