"""Translate semantic pagination into the HTML processor's page contracts.

The canonical preprocessor artifacts are ``global_meta``, ``logical_outline``,
and ``page_plan``. This module begins the processor phase: it assigns the
layout-aware ``DeckBrief``/``DeckPlan`` contracts without mutating PagePlan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.pipeline.events as events
from core.deck.agents.preprocessor.schema import GlobalMeta, LogicalOutline, PageCategory, PagePlan
from core.deck.design_system.catalog import get_recipe
from core.deck.domain.schemas import (
    DeckBrief,
    DeckPlan,
    LayoutFamily,
    PageRhythm,
    SlidePlan,
    SlideRole,
)
from core.deck.domain.text import truncate
from core.deck.pipeline.state import DeckGraphState
from core.deck.workspace.paths import DeckProjectPaths


class LayoutPlanningNodes:
    """Build processor-owned page contracts after semantic pagination."""

    def __init__(self, _llms: Any = None, output_dir: Path | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else None

    def prepare_processor_input(self, state: DeckGraphState) -> dict[str, Any]:
        return prepare_processor_input(state, output_dir=self.output_dir)


def prepare_processor_input(
    state: DeckGraphState,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Build layout-aware processor input without mutating the canonical page plan."""

    writer = get_stream_writer()
    writer(
        events.stage_started(
            "prepare_processor_input",
            "Preparing layout-aware HTML processor input",
        )
    )
    meta = GlobalMeta.model_validate(state.get("global_meta") or {})
    outline = LogicalOutline.model_validate(state.get("logical_outline") or {})
    page_plan = PagePlan.model_validate(state.get("page_plan") or {})

    brief = _processor_brief(meta, len(page_plan.pages))
    plan = _processor_deck_plan(meta, outline, page_plan)
    plan_payload = plan.model_dump(mode="json")
    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.brief_json, brief.model_dump(mode="json"))
        _atomic_write_json(project.deck_plan_json, plan_payload)
        _atomic_write_text(project.outline_markdown, deck_plan_markdown(plan))

    writer(
        events.stage_progress(
            "prepare_processor_input",
            "HTML processor input ready",
            {"status": "done", "page_count": len(plan.slides)},
        )
    )
    return {
        "brief": brief.model_dump(mode="json"),
        "deck_plan": plan_payload,
        "layout_preferences": allocate_layout_preferences(plan),
    }


def allocate_layout_preferences(plan: DeckPlan) -> dict[str, dict[str, Any]]:
    """Allocate tested recipe variants after semantic pagination is complete."""

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
                f"Allocated {variant} for {family} with "
                f"{slide.rhythm.value} rhythm."
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


def _processor_brief(meta: GlobalMeta, page_count: int) -> DeckBrief:
    return DeckBrief(
        topic=meta.title,
        audience=meta.audience,
        goal=meta.goal,
        audience_outcome=meta.audience_outcome,
        tone=meta.tone,
        language=meta.language,
        slide_count=page_count,
        aspect_ratio=meta.aspect_ratio,
        communication_job=meta.communication_job,
        content_requirements=meta.content_constraints,
        brand_preferences=meta.brand_preferences,
        color_preferences=meta.color_preferences,
        style_preferences=meta.style_preferences,
        source_mode=meta.source_mode,
        source_text=meta.source_text,
    )


def _processor_deck_plan(
    meta: GlobalMeta,
    outline: LogicalOutline,
    page_plan: PagePlan,
) -> DeckPlan:
    topic_intents = {
        topic.title: topic.intent
        for section in outline.sections
        for topic in section.topics
    }
    slides: list[SlidePlan] = []
    final_index = len(page_plan.pages)
    for page in page_plan.pages:
        role, family, rhythm = _processor_page_shape(page.page_category)
        if page.page_no == 1:
            role, family, rhythm = (
                SlideRole.COVER,
                LayoutFamily.COVER_MINIMAL,
                PageRhythm.ANCHOR,
            )
        elif final_index > 1 and page.page_no == final_index:
            role, family, rhythm = (
                SlideRole.CLOSING,
                LayoutFamily.CLOSING_RESOLUTION,
                PageRhythm.BREATHING,
            )
        intent = topic_intents.get(page.topic) or (
            f"Develop the semantic page topic '{page.topic}' while advancing the presentation goal."
        )
        takeaway = meta.audience_outcome if role in {SlideRole.COVER, SlideRole.CLOSING} else intent
        slides.append(
            SlidePlan(
                slide_id=f"s{page.page_no:02d}",
                index=page.page_no,
                role=role,
                assertion_title=truncate(page.topic, 120),
                takeaway=truncate(takeaway, 320),
                content_intent=truncate(intent, 500),
                layout_family=family,
                rhythm=rhythm,
                transition_from_previous=(
                    "" if page.page_no == 1 else "Continue the logical sequence defined by the semantic page plan."
                ),
            )
        )
    return DeckPlan(
        deck_title=meta.title,
        core_message=outline.central_thesis,
        narrative_arc=[section.purpose for section in outline.sections][:12],
        slides=slides,
    )


def _processor_page_shape(
    category: PageCategory,
) -> tuple[SlideRole, LayoutFamily, PageRhythm]:
    return {
        PageCategory.COVER: (
            SlideRole.COVER,
            LayoutFamily.COVER_MINIMAL,
            PageRhythm.ANCHOR,
        ),
        PageCategory.CATALOG: (
            SlideRole.CONTEXT,
            LayoutFamily.THREE_POINTS,
            PageRhythm.BREATHING,
        ),
        PageCategory.SECTION: (
            SlideRole.SECTION,
            LayoutFamily.SECTION_BREAK,
            PageRhythm.BREATHING,
        ),
        PageCategory.CONTENT: (
            SlideRole.INSIGHT,
            LayoutFamily.EDITORIAL_SPLIT,
            PageRhythm.DENSE,
        ),
        PageCategory.CHART: (
            SlideRole.EVIDENCE,
            LayoutFamily.KEY_METRIC,
            PageRhythm.DENSE,
        ),
        PageCategory.SUMMARY: (
            SlideRole.DECISION,
            LayoutFamily.STATEMENT_FOCUS,
            PageRhythm.ANCHOR,
        ),
        PageCategory.THANKS: (
            SlideRole.CLOSING,
            LayoutFamily.CLOSING_RESOLUTION,
            PageRhythm.BREATHING,
        ),
    }[category]


def _project_paths(output_dir: Path | None, state: DeckGraphState) -> DeckProjectPaths | None:
    request_id = str(state.get("request_id") or "").strip()
    if output_dir is None or not request_id:
        return None
    return DeckProjectPaths.for_request(output_dir, request_id).ensure()


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "LayoutPlanningNodes",
    "allocate_layout_preferences",
    "deck_plan_markdown",
    "prepare_processor_input",
]
