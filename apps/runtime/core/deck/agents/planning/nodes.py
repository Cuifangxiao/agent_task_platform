"""Planning nodes for the versioned HTML-first graph."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.agents.planning.graph import build_brief
from core.deck.design.html_catalog import recipe_prompt_manifest
from core.deck.factuality import qualitative_claims, unsupported_qualitative_claims
from core.deck.helpers import clamp_slide_count, extract_number_tokens, truncate
from core.deck.llm import (
    ModelInvocationError,
    StructuredOutputError,
    invoke_structured_with_retries,
    llm_for_node,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.paths import DeckProjectPaths
from core.deck.runtime_metrics import record_fallback
from core.deck.schemas import (
    DeckBrief,
    DeckPlan,
    LayoutFamily,
    PageRhythm,
    SlidePlan,
    SlideRole,
    SourceFact,
)
from core.deck.state import DeckGraphState


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
    writer(events.stage_started("normalize_request", "Normalizing the presentation request"))
    title = truncate(str(state.get("deck_title") or "Untitled presentation").strip(), 180)
    description = truncate(str(state.get("user_description") or "").strip(), 5_000)
    source_text = truncate(str(state.get("uploaded_file_text") or "").strip(), SOURCE_TEXT_LIMIT)
    source_context = truncate("\n\n".join(part for part in (description, source_text) if part), SOURCE_TEXT_LIMIT)
    requested_count = int(state.get("expected_slide_count") or 0)
    source_chunks = [item for item in state.get("source_chunks") or [] if isinstance(item, dict)]
    deterministic = _deterministic_brief(
        title,
        description,
        source_text,
        source_chunks=source_chunks,
        requested_count=requested_count,
        requested_language=str(state.get("language") or ""),
    )
    deterministic = deterministic.model_copy(update={"source_text": source_context})

    brief_llm = llm_registry.get("brief")
    brief = deterministic
    if brief_llm is not None:
        try:
            inferred = invoke_structured_with_retries(
                brief_llm,
                DeckBrief,
                system=load_node_prompt("deck_brief"),
                user=json.dumps(
                    {
                        "deterministic_brief": deterministic.model_dump(mode="json"),
                        "user_description": description,
                        "source_excerpt": source_text[:20_000],
                        "rules": {
                            "facts_are_immutable": True,
                            "slide_count": deterministic.slide_count,
                            "language": deterministic.language,
                            "aspect_ratio": "16:9",
                        },
                    },
                    ensure_ascii=False,
                ),
                attempts=2,
            )
            brief = inferred.model_copy(
                update={
                    "topic": deterministic.topic,
                    "facts": deterministic.facts,
                    "source_text": source_context,
                    "slide_count": deterministic.slide_count,
                    "language": deterministic.language,
                    "aspect_ratio": "16:9",
                }
            )
            inferred_values = [
                brief.audience,
                brief.goal,
                brief.audience_outcome,
                brief.tone,
                brief.communication_job,
            ]
            _assert_source_safe_numbers(inferred_values, source_context)
            _assert_source_safe_qualitative_claims(inferred_values, deterministic)
        except StructuredOutputError as exc:
            record_fallback()
            brief = deterministic
            writer(
                events.stage_progress(
                    "normalize_request",
                    f"Brief schema validation failed; used deterministic request normalization: {exc}",
                    {"status": "warning"},
                )
            )
        except ModelInvocationError:
            raise
    else:
        record_fallback()

    writer(
        events.stage_progress(
            "normalize_request",
            "Deck brief ready",
            {"status": "done", "facts_count": len(brief.facts), "slide_count": brief.slide_count},
        )
    )
    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.brief_json, brief.model_dump(mode="json"))
    return {
        "deck_title": brief.topic,
        "user_description": description,
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
                        "no_invented_facts": True,
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
    legacy_plan = {
        **plan_payload,
        "title": plan.deck_title,
        "narrative": plan.core_message,
    }
    outline_markdown = deck_plan_markdown(plan)
    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.deck_plan_json, plan_payload)
        _atomic_write_text(project.outline_markdown, outline_markdown)
    writer(events.outline_ready(outline_markdown))
    return {
        "deck_plan": plan_payload,
        "plan": legacy_plan,
        "outline_markdown": outline_markdown,
    }


def _deterministic_brief(
    title: str,
    description: str,
    source_text: str,
    *,
    source_chunks: list[dict[str, Any]],
    requested_count: int,
    requested_language: str,
) -> DeckBrief:
    legacy = build_brief(title, description, source_text, source_chunks=source_chunks)
    facts = [
        SourceFact(
            fact_id=f"fact#{index + 1}",
            statement=truncate(str(item.get("statement") or "").strip(), 500),
            value=truncate(str(item.get("value") or "").strip(), 80),
            unit=truncate(str(item.get("unit") or "").strip(), 40),
            source_file=truncate(str(item.get("source_file") or "").strip(), 255),
            page=max(0, int(item.get("page") or 0)),
            section=truncate(str(item.get("section") or "").strip(), 255),
            source_excerpt=truncate(str(item.get("source_excerpt") or "").strip(), 800),
        )
        for index, item in enumerate(legacy.get("facts") or [])
        if str(item.get("statement") or "").strip()
    ][:200]
    language = _normalize_language(
        requested_language,
        inferred=str(legacy.get("language") or "zh-CN"),
    )
    slide_count = clamp_slide_count(requested_count or legacy.get("suggested_pages") or 8)
    audience = truncate(str(legacy.get("audience") or "general audience").strip(), 180)
    goal = truncate(
        str(legacy.get("goal") or description or "communicate the main idea clearly").strip(),
        300,
    )
    audience_outcome = _audience_outcome(goal, language)
    core = truncate(facts[0].statement if facts else goal, 320)
    communication_job = (
        f"演示结束时，{audience} 应该{audience_outcome}，因为{core}。"
        if language.casefold().startswith("zh")
        else f"By the end, {audience} should {audience_outcome}, because {core}."
    )
    return DeckBrief(
        topic=truncate(title, 180),
        audience=audience,
        goal=goal,
        audience_outcome=audience_outcome,
        tone="clear, concise, and evidence-led",
        language=language,
        slide_count=slide_count,
        aspect_ratio="16:9",
        source_text=source_text or description,
        communication_job=truncate(communication_job, 500),
        brand_preferences=[],
        color_preferences=_extract_preferences(description, ("蓝", "红", "绿", "橙", "黑", "白", "blue", "red", "green", "orange")),
        style_preferences=_extract_preferences(description, ("商务", "科技", "编辑", "学术", "极简", "consulting", "tech", "editorial", "minimal")),
        facts=facts,
    )


def _deterministic_plan(brief: DeckBrief) -> DeckPlan:
    count = brief.slide_count
    facts = brief.facts
    core = facts[0].statement if facts else brief.goal
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
            fact = facts[offset % len(facts)] if facts else None
            if fact and fact.value and index == 3:
                family = LayoutFamily.KEY_METRIC
            title = truncate(fact.statement if fact else f"{brief.topic}：{brief.goal}", 110)
            takeaway = truncate(fact.statement if fact else brief.audience_outcome, 300)
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
    """Restore identifiers and evidence-sensitive recipe choices after LLM output."""

    has_numeric_fact = any(fact.value.strip() for fact in brief.facts)
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
        elif slide.layout_family is LayoutFamily.KEY_METRIC and not has_numeric_fact:
            # A metric-led composition without a sourced value visually implies
            # evidence that does not exist.  Use an assertion-led page instead.
            updates["layout_family"] = LayoutFamily.STATEMENT_FOCUS
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


def _audience_outcome(goal: str, language: str) -> str:
    if language.casefold().startswith("zh"):
        return f"理解核心判断并决定如何推进“{truncate(goal, 80)}”"
    return f"understand the core judgment and decide how to act on {truncate(goal, 100)}"


def _extract_preferences(text: str, candidates: tuple[str, ...]) -> list[str]:
    lowered = str(text or "").casefold()
    return [candidate for candidate in candidates if candidate.casefold() in lowered][:8]


def _normalize_language(requested: str, *, inferred: str) -> str:
    value = str(requested or "").strip() or str(inferred or "zh-CN").strip()
    folded = value.casefold()
    if folded in {"zh", "cn", "zh-cn", "chinese", "中文"}:
        return "zh-CN"
    if folded in {"en", "en-us", "english", "英文"}:
        return "en-US"
    if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", value):
        return value
    fallback = str(inferred or "zh-CN").strip()
    if fallback.casefold().startswith("en"):
        return "en-US"
    return "zh-CN"


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
    source_values = [brief.topic, brief.source_text]
    for fact in brief.facts:
        source_values.extend(
            [
                fact.statement,
                fact.source_excerpt,
                f"{fact.statement} {fact.value}{fact.unit}",
            ]
        )
    unsupported = unsupported_qualitative_claims(claims, source_values)
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


__all__ = ["PlanningNodes", "deck_plan_markdown", "normalize_request", "plan_deck"]
