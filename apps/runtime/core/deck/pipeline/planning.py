"""Request normalization and narrative planning for the HTML deck pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.pipeline.events as events
from core.deck.design_system.catalog import get_recipe, recipe_prompt_manifest
from core.deck.domain.factuality import qualitative_claims, unsupported_qualitative_claims
from core.deck.domain.text import extract_number_tokens, truncate
from core.deck.runtime.llm import (
    ModelInvocationError,
    StructuredOutputError,
    invoke_structured_with_retries,
    llm_for_node,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.workspace.paths import DeckProjectPaths
from core.deck.runtime.metrics import record_fallback
from core.deck.domain.schemas import (
    DeckBrief,
    DeckBriefInterpretation,
    DeckPlan,
    LayoutFamily,
    PageRhythm,
    SlidePlan,
    SlideRole,
)
from core.deck.pipeline.state import DeckGraphState


SOURCE_TEXT_LIMIT = 60_000


class PlanningNodes:
    def __init__(self, llms: Any, output_dir: Path | None = None) -> None:
        self.llm_registry = normalize_llm_registry(llms)
        self.output_dir = Path(output_dir) if output_dir is not None else None

    def normalize_request(self, state: DeckGraphState) -> dict[str, Any]:
        return normalize_request(state, self.llm_registry, output_dir=self.output_dir)

    def plan_deck(self, state: DeckGraphState) -> dict[str, Any]:
        return plan_deck(state, self.llm_registry, output_dir=self.output_dir)


def normalize_request(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("normalize_request", "Interpreting the raw presentation request"))
    raw_user_input = str(state.get("user_description") or "")
    if not raw_user_input.strip():
        raise ValueError("user_description is required")
    source_text = truncate(str(state.get("uploaded_file_text") or "").strip(), SOURCE_TEXT_LIMIT)
    source_context = truncate(
        "\n\n".join(part for part in (raw_user_input, source_text) if part),
        SOURCE_TEXT_LIMIT,
    )
    requested_count = int(state.get("expected_slide_count") or 0)
    if requested_count < 0 or requested_count > 30:
        raise ValueError("expected_slide_count must be between 1 and 30 when provided")
    requested_language = str(state.get("language") or "").strip()
    aspect_ratio = str(state.get("aspect_ratio") or "16:9").strip()
    if aspect_ratio != "16:9":
        raise ValueError("the HTML deck renderer currently supports only 16:9")
    requested_template_id = str(state.get("requested_template_id") or "").strip()

    interpreted = invoke_structured_with_retries(
        llm_for_node(llm_registry, "brief"),
        DeckBriefInterpretation,
        system=load_node_prompt("deck_brief"),
        user=json.dumps(
            {
                "raw_user_input": raw_user_input,
                "explicit_options": {
                    "slide_count": requested_count or None,
                    "language": requested_language or None,
                    "aspect_ratio": aspect_ratio,
                    "template_id": (
                        requested_template_id
                        if requested_template_id and requested_template_id != "auto"
                        else None
                    ),
                },
                "attachment_context": {
                    "present": bool(source_text),
                    "normalized_text": source_text,
                },
                "runtime_capabilities": {
                    "minimum_slide_count": 1,
                    "maximum_slide_count": 30,
                    "supported_aspect_ratios": ["16:9"],
                    "output_mode": "image_based_pptx",
                    "editable": False,
                },
            },
            ensure_ascii=False,
        ),
        attempts=3,
    )
    interpreted_payload = interpreted.model_dump(mode="json")
    if requested_count:
        interpreted_payload["slide_count"] = requested_count
    if requested_language:
        interpreted_payload["language"] = requested_language
    interpreted_payload["aspect_ratio"] = aspect_ratio
    brief = DeckBrief.model_validate(
        {
            **interpreted_payload,
            "source_text": source_context,
        }
    )

    writer(
        events.stage_progress(
            "normalize_request",
            "Deck brief ready",
            {
                "status": "done",
                "slide_count": brief.slide_count,
                "source_mode": brief.source_mode,
                "content_requirement_count": len(brief.content_requirements),
            },
        )
    )
    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.brief_json, brief.model_dump(mode="json"))
    return {
        "deck_title": brief.topic,
        "user_description": raw_user_input,
        "uploaded_file_text": source_text,
        "brief": brief.model_dump(mode="json"),
        "status": "planning",
    }


def plan_deck(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("plan_deck", "Planning the complete narrative and page roster"))
    brief = DeckBrief.model_validate(state.get("brief") or {})
    fallback = _deterministic_plan(brief)
    plan = fallback
    try:
        plan = invoke_structured_with_retries(
            llm_for_node(llm_registry, "outline"),
            DeckPlan,
            system=load_node_prompt("deck_planner"),
            user=json.dumps(
                {
                    "brief": brief.model_dump(mode="json"),
                    "layout_recipe_manifest": recipe_prompt_manifest(),
                    "hard_constraints": {
                        "slide_count": brief.slide_count,
                        "first_role": "cover",
                        "last_role_when_multiple": "closing",
                        "stable_slide_ids": "s01, s02, ...",
                        "no_invented_claims": True,
                    },
                },
                ensure_ascii=False,
            ),
            attempts=3,
        )
        if len(plan.slides) != brief.slide_count:
            raise StructuredOutputError(
                f"DeckPlan contains {len(plan.slides)} slides; expected {brief.slide_count}"
        )
        plan = _lock_plan_contract(plan, brief)
        plan_values = [
            plan.core_message,
            *plan.narrative_arc,
            *(
                value
                for slide in plan.slides
                for value in (
                    slide.assertion_title,
                    slide.takeaway,
                    slide.content_intent,
                    slide.transition_from_previous,
                )
            ),
        ]
        _assert_source_safe_numbers(plan_values, brief.source_text)
        _assert_source_safe_qualitative_claims(plan_values, brief)
    except StructuredOutputError as exc:
        record_fallback()
        writer(
            events.stage_progress(
                "plan_deck",
                f"DeckPlan schema validation failed; used deterministic narrative plan: {exc}",
                {"status": "warning"},
            )
        )
        plan = fallback
    except ModelInvocationError:
        raise

    plan_payload = plan.model_dump(mode="json")
    outline_markdown = deck_plan_markdown(plan)
    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.deck_plan_json, plan_payload)
        _atomic_write_text(project.outline_markdown, outline_markdown)
    writer(events.outline_ready(outline_markdown))
    return {
        "deck_plan": plan_payload,
        "layout_preferences": allocate_layout_preferences(plan),
    }


def allocate_layout_preferences(plan: DeckPlan) -> dict[str, dict[str, Any]]:
    """Pre-assign recipe variants so slide generation can safely fan out.

    Variants rotate independently inside each layout family.  The preference
    is deliberately descriptive rather than model-generated: every branch can
    see the preceding visual rhythm without waiting for preceding HTML.
    """

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
                f"Preallocated {variant} composition for {family} with "
                f"{slide.rhythm.value} page rhythm."
            ),
        }
    return preferences


def _deterministic_plan(brief: DeckBrief) -> DeckPlan:
    count = brief.slide_count
    core = brief.goal
    slides: list[SlidePlan] = []
    for index in range(1, count + 1):
        if index == 1:
            role = SlideRole.COVER
            family = LayoutFamily.COVER_MINIMAL
            rhythm = PageRhythm.ANCHOR
            title = truncate(brief.topic, 120)
            takeaway = truncate(core, 320)
            intent = "Establish the promise and the question this presentation will resolve."
        elif index == count:
            role = SlideRole.CLOSING
            family = LayoutFamily.CLOSING_RESOLUTION
            rhythm = PageRhythm.BREATHING
            title = "核心结论已经指向明确的下一步" if brief.language.startswith("zh") else "The conclusion points to a clear next step"
            takeaway = brief.audience_outcome
            intent = "Resolve the opening promise and make the expected next action explicit."
        else:
            offset = index - 2
            role_cycle = [
                SlideRole.CONTEXT,
                SlideRole.PROBLEM,
                SlideRole.EVIDENCE,
                SlideRole.INSIGHT,
                SlideRole.SOLUTION,
                SlideRole.PROCESS,
                SlideRole.DECISION,
            ]
            family_cycle = [
                LayoutFamily.EDITORIAL_SPLIT,
                LayoutFamily.STATEMENT_FOCUS,
                LayoutFamily.THREE_POINTS,
                LayoutFamily.COMPARISON,
                LayoutFamily.PROCESS,
                LayoutFamily.TIMELINE,
            ]
            role = role_cycle[offset % len(role_cycle)]
            family = family_cycle[offset % len(family_cycle)]
            title = truncate(f"{brief.topic}：{brief.goal}", 110)
            takeaway = truncate(brief.audience_outcome, 300)
            intent = f"Advance the narrative through {role.value} without repeating earlier pages."
            rhythm = PageRhythm.DENSE if index % 3 else PageRhythm.BREATHING
        slides.append(
            SlidePlan(
                slide_id=f"s{index:02d}",
                index=index,
                role=role,
                assertion_title=title,
                takeaway=takeaway,
                content_intent=intent,
                layout_family=family,
                rhythm=rhythm,
                transition_from_previous="" if index == 1 else "Build on the prior conclusion and introduce one new question.",
            )
        )
    return DeckPlan(
        deck_title=brief.topic,
        core_message=core,
        narrative_arc=[brief.goal, brief.audience_outcome, core],
        slides=slides,
    )


def _lock_plan_contract(plan: DeckPlan, brief: DeckBrief) -> DeckPlan:
    """Restore stable identifiers and boundary-page recipes after LLM output."""

    slides: list[SlidePlan] = []
    final_index = len(plan.slides)
    for index, slide in enumerate(plan.slides, start=1):
        updates: dict[str, Any] = {"slide_id": f"s{index:02d}", "index": index}
        if index == 1:
            updates.update({"role": SlideRole.COVER, "layout_family": LayoutFamily.COVER_MINIMAL})
        elif final_index > 1 and index == final_index:
            updates.update(
                {"role": SlideRole.CLOSING, "layout_family": LayoutFamily.CLOSING_RESOLUTION}
            )
        slides.append(slide.model_copy(update=updates))
    return plan.model_copy(update={"deck_title": brief.topic, "slides": slides})


def deck_plan_markdown(plan: DeckPlan) -> str:
    lines = [f"# {plan.deck_title}", f"> {plan.core_message}", ""]
    for slide in plan.slides:
        lines.extend(
            [
                f"## {slide.index}. {slide.assertion_title}",
                f"- slide_id: {slide.slide_id}",
                f"- role: {slide.role.value}",
                f"- takeaway: {slide.takeaway}",
                f"- layout_family: {slide.layout_family.value}",
                f"- rhythm: {slide.rhythm.value}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _assert_source_safe_numbers(values: list[str], source_text: str) -> None:
    allowed = set(extract_number_tokens(source_text))
    used = {token for value in values for token in extract_number_tokens(value)}
    unknown = sorted(used - allowed)
    if unknown:
        raise StructuredOutputError(
            "structured output introduced source-free numeric tokens: " + ", ".join(unknown[:8])
        )


def _assert_source_safe_qualitative_claims(values: list[str], brief: DeckBrief) -> None:
    claims = [claim for value in values for claim in qualitative_claims(value)]
    if not claims:
        return
    unsupported = unsupported_qualitative_claims(claims, [brief.topic, brief.source_text])
    if unsupported:
        summary = "; ".join(
            f"{kind}={truncate(value, 100)!r}" for kind, value in unsupported[:4]
        )
        raise StructuredOutputError(
            "DeckPlan introduced unsupported qualitative factual claims: " + summary
        )


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
    "PlanningNodes",
    "allocate_layout_preferences",
    "deck_plan_markdown",
    "normalize_request",
    "plan_deck",
]
