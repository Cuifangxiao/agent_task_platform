"""Deterministic adapters from the deck outline to renderer page contracts.

The outline model owns page intent and the requested layout family.  This
module does not call a model: it translates that validated result into the
legacy renderer contracts that are still consumed by DOM QA, fallback HTML,
preview events, and the image-only PPTX exporter.
"""

from __future__ import annotations

from typing import Any

from core.deck.design_system.catalog import get_recipe
from core.deck.domain.schemas import (
    ContentMode,
    DeckOutlineSpec,
    DeckPlan,
    LayoutFamily,
    PageRhythm,
    SlideOutlineSpec,
    SlidePlan,
    SlideRole,
)
from core.deck.domain.text import truncate


def deck_plan_from_outline(outline: DeckOutlineSpec) -> DeckPlan:
    """Create the renderer-facing plan without another semantic model call."""

    slides = [
        SlidePlan(
            slide_id=slide.id,
            index=index,
            role=slide.type,
            assertion_title=truncate(slide.title, 120),
            takeaway=truncate(_takeaway(slide, deck_subtitle=outline.subtitle), 320),
            content_intent=truncate(slide.content_note, 500),
            layout_family=slide.layout_suggestion,
            rhythm=_page_rhythm(slide),
            transition_from_previous=(
                "" if index == 1 else "Continue the narrative sequence defined by the deck outline."
            ),
        )
        for index, slide in enumerate(outline.slides, start=1)
    ]
    narrative = [
        truncate(slide.content_note, 500)
        for slide in outline.slides
        if slide.type in {SlideRole.SECTION, SlideRole.INSIGHT, SlideRole.DECISION}
    ][:12]
    if not narrative:
        narrative = [truncate(outline.metadata.source_summary, 500)]
    return DeckPlan(
        deck_title=outline.title,
        core_message=truncate(outline.subtitle or outline.metadata.source_summary, 500),
        narrative_arc=narrative,
        slides=slides,
    )


def allocate_layout_preferences(plan: DeckPlan) -> dict[str, dict[str, Any]]:
    """Pre-allocate recipe variants so parallel pages share deterministic context."""

    family_usage: dict[str, int] = {}
    preferences: dict[str, dict[str, Any]] = {}
    for slide in plan.slides:
        family = slide.layout_family.value
        recipe = get_recipe(slide.layout_family)
        usage = family_usage.get(family, 0)
        variant = recipe.variants[usage % len(recipe.variants)]
        family_usage[family] = usage + 1
        preferences[slide.slide_id] = {
            "slide_id": slide.slide_id,
            "index": slide.index,
            "layout_family": family,
            "layout_variant": variant,
            "rhythm": slide.rhythm.value,
            "composition_summary": (
                f"Use {variant} for {family} with {slide.rhythm.value} rhythm."
            ),
        }
    return preferences


def deck_plan_markdown(plan: DeckPlan) -> str:
    lines = [f"# {plan.deck_title}", f"> {plan.core_message}", ""]
    for slide in plan.slides:
        lines.extend(
            [
                f"## {slide.index}. {slide.assertion_title}",
                f"- slide_id: {slide.slide_id}",
                f"- role: {slide.role.value}",
                f"- layout_family: {slide.layout_family.value}",
                f"- rhythm: {slide.rhythm.value}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _takeaway(slide: SlideOutlineSpec, *, deck_subtitle: str = "") -> str:
    # A cover has no body key points. Its audience-facing secondary copy comes
    # from the deck subtitle; content_note is an internal planning/audit field.
    if slide.type is SlideRole.COVER:
        return deck_subtitle.strip() or (slide.key_points[0] if slide.key_points else slide.title)
    if slide.key_points:
        return slide.key_points[0]
    return slide.content_note


def _page_rhythm(slide: SlideOutlineSpec) -> PageRhythm:
    if slide.type in {SlideRole.COVER, SlideRole.CLOSING}:
        return PageRhythm.ANCHOR
    if slide.type is SlideRole.SECTION or slide.content_mode is ContentMode.PARAGRAPH:
        return PageRhythm.BREATHING
    if slide.content_mode in {ContentMode.DATA, ContentMode.MIXED}:
        return PageRhythm.DENSE
    if slide.layout_suggestion in {
        LayoutFamily.STATEMENT_FOCUS,
        LayoutFamily.SECTION_BREAK,
    }:
        return PageRhythm.BREATHING
    return PageRhythm.DENSE


__all__ = [
    "allocate_layout_preferences",
    "deck_plan_from_outline",
    "deck_plan_markdown",
]
