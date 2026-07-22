"""Three-node preprocessor graph for requirements, ideas, and pagination."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

import core.deck.pipeline.events as events
from core.deck.agents.preprocessor.schema import (
    GlobalMeta,
    GlobalMetaInterpretation,
    LogicalOutline,
    OutlineSection,
    OutlineTopic,
    PageCategory,
    PagePlan,
    PagePlanItem,
    SceneNarrativeTemplate,
)
from core.deck.agents.preprocessor.templates import SceneTemplateRepository
from core.deck.domain.factuality import qualitative_claims, unsupported_qualitative_claims
from core.deck.domain.text import extract_number_tokens, truncate
from core.deck.pipeline.instrumentation import instrument_node
from core.deck.pipeline.state import DeckGraphState
from core.deck.runtime.llm import (
    ModelInvocationError,
    StructuredOutputError,
    invoke_structured_with_retries,
    llm_for_node,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.runtime.metrics import record_fallback
from core.deck.workspace.paths import DeckProjectPaths


SOURCE_TEXT_LIMIT = 60_000
PREPROCESSOR_NODE_SEQUENCE = (
    "parse_requirements",
    "create_logical_outline",
    "map_outline_to_pages",
)


class Preprocessor:
    """Own steps 2–4 and stop before any single-page layout decision."""

    def __init__(
        self,
        llms: Any,
        output_dir: Path | None = None,
        *,
        templates: SceneTemplateRepository | None = None,
    ) -> None:
        self.llm_registry = normalize_llm_registry(llms)
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.templates = templates or SceneTemplateRepository()
        self.graph = self.compile_graph()

    def parse_requirements(self, state: DeckGraphState) -> dict[str, Any]:
        return parse_requirements(state, self.llm_registry, output_dir=self.output_dir)

    def create_logical_outline(self, state: DeckGraphState) -> dict[str, Any]:
        return create_logical_outline(state, self.llm_registry, output_dir=self.output_dir)

    def map_outline_to_pages(self, state: DeckGraphState) -> dict[str, Any]:
        return map_outline_to_pages(
            state,
            self.llm_registry,
            self.templates,
            output_dir=self.output_dir,
        )

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        handlers = {
            "parse_requirements": (self.parse_requirements, "brief"),
            "create_logical_outline": (self.create_logical_outline, "outline"),
            "map_outline_to_pages": (self.map_outline_to_pages, "outline"),
        }
        for name in PREPROCESSOR_NODE_SEQUENCE:
            handler, model_key = handlers[name]
            builder.add_node(
                name,
                instrument_node(name, handler, self.llm_registry, model_key=model_key),
            )
        builder.add_edge(START, "parse_requirements")
        builder.add_edge("parse_requirements", "create_logical_outline")
        builder.add_edge("create_logical_outline", "map_outline_to_pages")
        builder.add_edge("map_outline_to_pages", END)
        return builder.compile()

    def run(self, state: DeckGraphState) -> dict[str, Any]:
        return self.graph.invoke(state)


def parse_requirements(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("parse_requirements", "Parsing the presentation request"))
    raw_user_input = str(state.get("user_description") or "").strip()
    if not raw_user_input:
        raise ValueError("user_description is required")

    attachment_text = truncate(
        str(state.get("uploaded_file_text") or "").strip(),
        SOURCE_TEXT_LIMIT,
    )
    source_text = truncate(
        "\n\n".join(part for part in (raw_user_input, attachment_text) if part),
        SOURCE_TEXT_LIMIT,
    )
    requested_count = int(state.get("expected_slide_count") or 0)
    if requested_count < 0 or requested_count > 30:
        raise ValueError("expected_slide_count must be between 1 and 30 when provided")
    requested_language = str(state.get("language") or "").strip()
    aspect_ratio = str(state.get("aspect_ratio") or "16:9").strip()

    interpreted = invoke_structured_with_retries(
        llm_for_node(llm_registry, "brief"),
        GlobalMetaInterpretation,
        system=load_node_prompt("requirement_parser"),
        user=json.dumps(
            {
                "raw_user_input": raw_user_input,
                "explicit_options": {
                    "target_page_count": requested_count or None,
                    "language": requested_language or None,
                    "aspect_ratio": aspect_ratio,
                },
                "attachment_context": {
                    "present": bool(attachment_text),
                    "normalized_text": attachment_text,
                },
                "hard_constraints": {
                    "minimum_page_count": 1,
                    "maximum_page_count": 30,
                    "supported_aspect_ratios": ["16:9"],
                    "scene_is_semantic_not_visual": True,
                },
            },
            ensure_ascii=False,
        ),
        attempts=3,
    )
    payload = interpreted.model_dump(mode="json")
    if requested_count:
        payload["target_page_count"] = requested_count
    if requested_language:
        payload["language"] = requested_language
    payload["aspect_ratio"] = aspect_ratio
    meta = GlobalMeta.model_validate({**payload, "source_text": source_text})

    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.global_meta_json, meta.model_dump(mode="json"))
    writer(
        events.stage_progress(
            "parse_requirements",
            "Global presentation metadata ready",
            {
                "status": "done",
                "scene": meta.scene,
                "target_page_count": meta.target_page_count,
            },
        )
    )
    return {
        "deck_title": meta.title,
        "global_meta": meta.model_dump(mode="json"),
        "status": "planning",
    }


def create_logical_outline(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("create_logical_outline", "Creating the page-agnostic content outline"))
    meta = GlobalMeta.model_validate(state.get("global_meta") or {})
    fallback = _deterministic_outline(meta)
    outline = fallback
    try:
        outline = invoke_structured_with_retries(
            llm_for_node(llm_registry, "outline"),
            LogicalOutline,
            system=load_node_prompt("content_ideator"),
            user=json.dumps(
                {
                    "global_meta": meta.model_dump(mode="json", exclude={"source_text"}),
                    "source_text": meta.source_text,
                    "hard_constraints": {
                        "no_page_concept": True,
                        "no_page_count_allocation": True,
                        "no_layout_or_visual_template": True,
                        "no_invented_numeric_claims": True,
                    },
                },
                ensure_ascii=False,
            ),
            attempts=3,
        )
        outline = outline.model_copy(update={"title": meta.title})
        outline_values = _outline_values(outline)
        _assert_source_safe_numbers(outline_values, meta.source_text)
        _assert_source_safe_qualitative_claims(outline_values, meta)
    except StructuredOutputError as exc:
        record_fallback()
        writer(
            events.stage_progress(
                "create_logical_outline",
                f"Logical outline validation failed; used a deterministic outline: {exc}",
                {"status": "warning"},
            )
        )
        outline = fallback
    except ModelInvocationError:
        raise

    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.logical_outline_json, outline.model_dump(mode="json"))
        _atomic_write_text(project.logical_outline_text, logical_outline_text(outline))
    writer(events.outline_ready(logical_outline_text(outline)))
    return {"logical_outline": outline.model_dump(mode="json")}


def map_outline_to_pages(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    templates: SceneTemplateRepository,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("map_outline_to_pages", "Mapping the outline with the scene narrative template"))
    meta = GlobalMeta.model_validate(state.get("global_meta") or {})
    outline = LogicalOutline.model_validate(state.get("logical_outline") or {})

    # Asset A is first loaded here.  Earlier nodes receive neither this object
    # nor its page-order constraints.
    template = templates.resolve(meta.scene)
    target_page_count = min(
        template.page_max,
        max(template.page_min, meta.target_page_count),
    )
    fallback = _deterministic_page_plan(meta, outline, template, target_page_count)
    page_plan = fallback
    try:
        page_plan = invoke_structured_with_retries(
            llm_for_node(llm_registry, "outline"),
            PagePlan,
            system=load_node_prompt("page_mapper"),
            user=json.dumps(
                {
                    "global_meta": meta.model_dump(mode="json", exclude={"source_text"}),
                    "logical_outline": outline.model_dump(mode="json"),
                    "scene_narrative_template": template.model_dump(mode="json"),
                    "hard_constraints": {
                        "target_page_count": target_page_count,
                        "output_fields_per_page": ["page_no", "page_category", "topic"],
                        "forbidden_fields": [
                            "layout_id",
                            "layout_template",
                            "layout_family",
                            "content",
                            "media",
                            "html",
                            "svg",
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            attempts=3,
        )
        page_plan = page_plan.model_copy(update={"scene": template.scene})
        _validate_page_plan(page_plan, template, target_page_count)
        page_topics = [page.topic for page in page_plan.pages]
        _assert_source_safe_numbers(page_topics, meta.source_text)
        _assert_source_safe_qualitative_claims(page_topics, meta)
    except StructuredOutputError as exc:
        record_fallback()
        writer(
            events.stage_progress(
                "map_outline_to_pages",
                f"Page mapping validation failed; used deterministic pagination: {exc}",
                {"status": "warning"},
            )
        )
        page_plan = fallback
    except ModelInvocationError:
        raise

    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.scene_template_json, template.model_dump(mode="json"))
        _atomic_write_json(project.page_plan_json, page_plan.model_dump(mode="json"))
    writer(
        events.stage_progress(
            "map_outline_to_pages",
            "Semantic page roster ready",
            {
                "status": "done",
                "scene": template.scene,
                "page_count": len(page_plan.pages),
            },
        )
    )
    return {
        "narrative_template": template.model_dump(mode="json"),
        "page_plan": page_plan.model_dump(mode="json"),
    }


def logical_outline_text(outline: LogicalOutline) -> str:
    lines = [outline.title, outline.central_thesis, ""]
    for section_no, section in enumerate(outline.sections, start=1):
        lines.append(f"{section_no}. {section.title}")
        for topic_no, topic in enumerate(section.topics, start=1):
            lines.append(f"  {section_no}.{topic_no} {topic.title}")
            for point in topic.key_points:
                lines.append(f"    - {point}")
    return "\n".join(lines).rstrip() + "\n"


def _deterministic_outline(meta: GlobalMeta) -> LogicalOutline:
    constraints = list(meta.content_constraints)
    if not constraints:
        constraints = (
            ["建立主题背景与沟通目标", "展开核心观点与逻辑关系", "形成结论与下一步"]
            if meta.language.startswith("zh")
            else [
                "Establish the context and communication goal",
                "Develop the core ideas and their relationships",
                "Synthesize the conclusion and next step",
            ]
        )
    sections: list[OutlineSection] = []
    topic_number = 1
    for section_number, start in enumerate(range(0, len(constraints), 3), start=1):
        group = constraints[start : start + 3]
        topics = []
        for requirement in group:
            topics.append(
                OutlineTopic(
                    topic_id=f"t{topic_number:02d}",
                    title=truncate(requirement, 180),
                    intent=truncate(f"围绕“{requirement}”建立清晰解释。", 360),
                    key_points=[truncate(requirement, 240)],
                )
            )
            topic_number += 1
        sections.append(
            OutlineSection(
                section_id=f"sec{section_number:02d}",
                title=truncate(group[0], 180),
                purpose=truncate(f"推进演示目标：{meta.goal}", 360),
                topics=topics,
            )
        )
    return LogicalOutline(
        title=meta.title,
        central_thesis=meta.goal,
        narrative_strategy=meta.communication_job,
        sections=sections,
    )


def _deterministic_page_plan(
    meta: GlobalMeta,
    outline: LogicalOutline,
    template: SceneNarrativeTemplate,
    target_page_count: int,
) -> PagePlan:
    categories = _fit_category_sequence(template, target_page_count)
    topics = [topic.title for section in outline.sections for topic in section.topics]
    sections = [section.title for section in outline.sections]
    content_cursor = 0
    section_cursor = 0
    pages: list[PagePlanItem] = []
    for page_no, category in enumerate(categories, start=1):
        if category is PageCategory.COVER:
            topic = meta.title
        elif category is PageCategory.CATALOG:
            topic = "目录" if meta.language.startswith("zh") else "Agenda"
        elif category is PageCategory.SECTION:
            topic = sections[section_cursor % len(sections)]
            section_cursor += 1
        elif category is PageCategory.SUMMARY:
            topic = "结论与下一步" if meta.language.startswith("zh") else "Conclusion and next step"
        elif category is PageCategory.THANKS:
            topic = "总结与交流" if meta.language.startswith("zh") else "Closing discussion"
        else:
            topic = topics[content_cursor % len(topics)]
            content_cursor += 1
        pages.append(PagePlanItem(page_no=page_no, page_category=category, topic=topic))
    plan = PagePlan(scene=template.scene, pages=pages)
    _validate_page_plan(plan, template, target_page_count)
    return plan


def _fit_category_sequence(
    template: SceneNarrativeTemplate,
    target_page_count: int,
) -> list[PageCategory]:
    categories = list(template.suggest_page_order)
    mandatory = set(template.mandatory_page_types)
    while len(categories) > target_page_count:
        removable = next(
            (
                index
                for index in range(len(categories) - 1, -1, -1)
                if categories[index] not in mandatory
                or categories.count(categories[index]) > 1
            ),
            None,
        )
        if removable is None:
            break
        categories.pop(removable)
    while len(categories) < target_page_count:
        trailing = {PageCategory.SUMMARY, PageCategory.THANKS}
        insert_at = next(
            (index for index, category in enumerate(categories) if category in trailing),
            len(categories),
        )
        categories.insert(insert_at, PageCategory.CONTENT)
    return categories


def _validate_page_plan(
    page_plan: PagePlan,
    template: SceneNarrativeTemplate,
    target_page_count: int,
) -> None:
    if page_plan.scene != template.scene:
        raise StructuredOutputError("PagePlan scene does not match the selected narrative template")
    if len(page_plan.pages) != target_page_count:
        raise StructuredOutputError(
            f"PagePlan contains {len(page_plan.pages)} pages; expected {target_page_count}"
        )
    present = {page.page_category for page in page_plan.pages}
    missing = set(template.mandatory_page_types) - present
    if missing:
        values = ", ".join(sorted(category.value for category in missing))
        raise StructuredOutputError(f"PagePlan is missing mandatory page categories: {values}")


def _outline_values(outline: LogicalOutline) -> list[str]:
    return [
        outline.title,
        outline.central_thesis,
        outline.narrative_strategy,
        *(
            value
            for section in outline.sections
            for value in (
                section.title,
                section.purpose,
                *(item for topic in section.topics for item in (topic.title, topic.intent, *topic.key_points)),
            )
        ),
    ]


def _assert_source_safe_numbers(values: list[str], source_text: str) -> None:
    allowed = set(extract_number_tokens(source_text))
    used = {token for value in values for token in extract_number_tokens(value)}
    unknown = sorted(used - allowed)
    if unknown:
        raise StructuredOutputError(
            "preprocessor introduced source-free numeric tokens: " + ", ".join(unknown[:8])
        )


def _assert_source_safe_qualitative_claims(values: list[str], meta: GlobalMeta) -> None:
    claims = [claim for value in values for claim in qualitative_claims(value)]
    if not claims:
        return
    unsupported = unsupported_qualitative_claims(claims, [meta.title, meta.source_text])
    if unsupported:
        summary = "; ".join(
            f"{kind}={truncate(value, 100)!r}" for kind, value in unsupported[:4]
        )
        raise StructuredOutputError(
            "preprocessor introduced unsupported qualitative factual claims: " + summary
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
    "PREPROCESSOR_NODE_SEQUENCE",
    "Preprocessor",
    "create_logical_outline",
    "logical_outline_text",
    "map_outline_to_pages",
    "parse_requirements",
]
