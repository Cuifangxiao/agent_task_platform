"""Explicit per-slide nodes for HTML authoring, QA, repair, and persistence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import html5lib
from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.design.html_catalog import get_recipe
from core.deck.factuality import (
    canonical_claim_text as _canonical_claim_text,
    deduplicate_qualitative_claims as _deduplicate_qualitative_claims,
    qualitative_claims as _qualitative_claims,
    unsupported_qualitative_claims,
)
from core.deck.html import HTMLAssembler, build_fallback_html_spec, icon_registry
from core.deck.helpers import extract_number_tokens, truncate
from core.deck.llm import (
    ModelInvocationError,
    StructuredOutputError,
    invoke_structured_with_retries,
    llm_for_node,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.paths import DeckProjectPaths
from core.deck.rendering.html_browser import (
    BrowserContentError,
    BrowserEnvironmentError,
    HTMLBrowserRenderer,
)
from core.deck.runtime_metrics import record_fallback
from core.deck.schemas import (
    ContentBlock,
    ContentBlockType,
    DeckBrief,
    DeckPlan,
    DesignSpec,
    DiagnosticCategory,
    DiagnosticSeverity,
    HtmlSlideSpec,
    LayoutFamily,
    PROMPT_VERSION,
    RENDERER_VERSION,
    RendererDiagnostic,
    SlideArtifactMetadata,
    SlideContentSpec,
    SlidePlan,
)
from core.deck.state import DeckGraphState


MAX_SEMANTIC_REPAIRS = 2


class GenerationNodes:
    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        *,
        assembler: HTMLAssembler | None = None,
        browser_renderer: HTMLBrowserRenderer | None = None,
        max_repairs: int = MAX_SEMANTIC_REPAIRS,
    ) -> None:
        self.llm_registry = normalize_llm_registry(llms)
        self.output_dir = Path(output_dir)
        self.assembler = assembler or HTMLAssembler()
        self.browser_renderer = browser_renderer or HTMLBrowserRenderer()
        self.max_repairs = max(0, int(max_repairs))

    def prepare_slide(self, state: DeckGraphState) -> dict[str, Any]:
        return prepare_slide(state)

    def generate_slide_content(self, state: DeckGraphState) -> dict[str, Any]:
        return generate_slide_content(state, self.llm_registry)

    def generate_slide_html(self, state: DeckGraphState) -> dict[str, Any]:
        return generate_slide_html(state, self.output_dir, self.llm_registry)

    def sanitize_and_assemble_html(self, state: DeckGraphState) -> dict[str, Any]:
        return sanitize_and_assemble_html(state, self.assembler)

    def render_html_and_measure_dom(self, state: DeckGraphState) -> dict[str, Any]:
        return render_html_and_measure_dom(state, self.browser_renderer)

    def validate_dom(self, state: DeckGraphState) -> dict[str, Any]:
        return validate_dom(state, max_repairs=self.max_repairs)

    def repair_slide_html(self, state: DeckGraphState) -> dict[str, Any]:
        return repair_slide_html(
            state,
            self.output_dir,
            self.llm_registry,
            max_repairs=self.max_repairs,
        )

    def build_fallback_html(self, state: DeckGraphState) -> dict[str, Any]:
        return build_fallback_html(state)

    def capture_png(self, state: DeckGraphState) -> dict[str, Any]:
        return capture_png(state, self.output_dir, self.browser_renderer)

    def persist_slide_artifacts(self, state: DeckGraphState) -> dict[str, Any]:
        return persist_slide_artifacts(state, self.output_dir, self.assembler, self.llm_registry)

    def emit_slide_preview(self, state: DeckGraphState) -> dict[str, Any]:
        return emit_slide_preview(state)

    def advance_slide(self, state: DeckGraphState) -> dict[str, Any]:
        return advance_slide(state)


def prepare_slide(state: DeckGraphState) -> dict[str, Any]:
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    offset = max(0, int(state.get("current_slide_index") or 0))
    if offset >= len(plan.slides):
        raise ValueError("prepare_slide called after the final planned page")
    slide = plan.slides[offset]
    writer = get_stream_writer()
    writer(
        events.slide_started(
            f"Generating page {slide.index}/{len(plan.slides)}: {slide.assertion_title}",
            {
                "slide_id": slide.slide_id,
                "index": slide.index,
                "total": len(plan.slides),
                "title": slide.assertion_title,
            },
        )
    )
    return {
        "current_slide_id": slide.slide_id,
        "current_slide_plan": slide.model_dump(mode="json"),
        "current_slide_content_spec": {},
        "current_html_slide_spec": {},
        "current_sanitized_body_html": "",
        "current_sanitized_page_css": "",
        "current_assembled_html": "",
        "current_diagnostics": [],
        "current_repair_attempts": 0,
        "current_fallback_used": False,
        "current_render_decision": "repair",
        "current_preview_path": "",
        "current_preview_hash": "",
        "current_renderer_metadata": {},
        "current_raw_debug_path": "",
        "status": "generating",
    }


def generate_slide_content(state: DeckGraphState, llm_registry: dict[str, Any]) -> dict[str, Any]:
    brief = DeckBrief.model_validate(state.get("brief") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    recipe = get_recipe(slide.layout_family)
    valid_fact_ids = {fact.fact_id for fact in brief.facts}
    icons = icon_registry().search(
        " ".join([slide.assertion_title, slide.takeaway, slide.content_intent]), limit=12
    )
    fallback = _deterministic_content_spec(brief, slide)
    content = fallback
    try:
        content = invoke_structured_with_retries(
            llm_for_node(llm_registry, "writer", aliases=("slides",)),
            SlideContentSpec,
            system=load_node_prompt("slide_content"),
            user=json.dumps(
                {
                    "brief": brief.model_dump(mode="json", exclude={"source_text"}),
                    "deck_plan": plan.model_dump(mode="json"),
                    "current_slide": slide.model_dump(mode="json"),
                    "neighbor_slides": _neighbor_plans(plan, slide.index),
                    "recipe": recipe.prompt_manifest(),
                    "relevant_sources": _relevant_sources(state, slide),
                    "available_icon_keys": icons,
                    "hard_constraints": {
                        "slide_id": slide.slide_id,
                        "assertion_title": slide.assertion_title,
                        "takeaway": slide.takeaway,
                        "no_invented_numbers": True,
                    },
                },
                ensure_ascii=False,
            ),
            attempts=3,
        )
        content = content.model_copy(
            update={
                "slide_id": slide.slide_id,
                "assertion_title": slide.assertion_title,
                "takeaway": slide.takeaway,
                "suggested_icon_keys": [key for key in content.suggested_icon_keys if key in icons][:8],
                "source_refs": [ref for ref in content.source_refs if ref in valid_fact_ids][:20],
            }
        )
        content = _normalize_content_for_recipe(content, brief, slide)
        _assert_content_source_safe(content, brief, slide)
    except StructuredOutputError as exc:
        record_fallback()
        get_stream_writer()(
            events.stage_progress(
                "generate_slide_content",
                f"Page {slide.index} content schema failed; used source-safe fallback: {exc}",
                {"status": "warning", "index": slide.index, "total": len(plan.slides)},
            )
        )
        content = fallback
    except ModelInvocationError:
        raise
    return {"current_slide_content_spec": content.model_dump(mode="json")}


def generate_slide_html(
    state: DeckGraphState,
    output_dir: Path,
    llm_registry: dict[str, Any],
) -> dict[str, Any]:
    brief = DeckBrief.model_validate(state.get("brief") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
    recipe = get_recipe(slide.layout_family)
    allowed_icons = sorted(set(content.suggested_icon_keys + icon_registry().search(slide.takeaway, limit=8)))[:12]
    fallback = build_fallback_html_spec(content, slide)
    fallback_used = False
    spec = fallback
    try:
        spec = invoke_structured_with_retries(
            llm_for_node(llm_registry, "designer"),
            HtmlSlideSpec,
            system=load_node_prompt("html_slide_designer"),
            user=json.dumps(
                {
                    "brief": brief.model_dump(mode="json", exclude={"source_text", "facts"}),
                    "deck_plan": plan.model_dump(mode="json"),
                    "design_spec": design.model_dump(mode="json"),
                    "current_slide": slide.model_dump(mode="json"),
                    "slide_content": content.model_dump(mode="json"),
                    "neighbor_slides": _neighbor_plans(plan, slide.index),
                    "recipe": recipe.prompt_manifest(),
                    "available_icon_keys": allowed_icons,
                    "previous_compositions": list(state.get("layout_history") or [])[-4:],
                    "html_contract": {
                        "canvas": "1600x900",
                        "root": "exactly one section.slide",
                        "semantic_boxes": "data-box",
                        "decorations": "data-decorative=true",
                        "external_resources": "forbidden",
                    },
                },
                ensure_ascii=False,
            ),
            attempts=2,
        )
        spec = _lock_html_spec(spec, slide, allowed_icons)
    except StructuredOutputError as exc:
        record_fallback()
        fallback_used = True
        get_stream_writer()(
            events.stage_progress(
                "generate_slide_html",
                f"Page {slide.index} HTML schema failed; used the executable recipe fallback: {exc}",
                {"status": "warning", "index": slide.index, "total": len(plan.slides)},
            )
        )
        spec = fallback
    except ModelInvocationError:
        raise

    raw_path = _write_raw_debug(output_dir, state, spec, attempt=0)
    return {
        "current_html_slide_spec": spec.model_dump(mode="json"),
        "current_fallback_used": fallback_used,
        "current_raw_debug_path": raw_path,
    }


def sanitize_and_assemble_html(state: DeckGraphState, assembler: HTMLAssembler) -> dict[str, Any]:
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    spec = HtmlSlideSpec.model_validate(state.get("current_html_slide_spec") or {})
    brief = DeckBrief.model_validate(state.get("brief") or {})
    try:
        result = assembler.assemble(
            body_html=spec.body_html,
            page_css=spec.page_css,
            design_spec=design,
            language=brief.language,
            declared_icon_keys=spec.icon_keys,
            expected_layout=spec.layout_family.value,
        )
        diagnostics = [_sanitizer_diagnostic(item.to_dict()) for item in result.diagnostics]
        slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
        content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
        diagnostics.extend(
            _content_contract_diagnostics(
                result.body_html,
                slide,
                content,
                fallback_used=bool(state.get("current_fallback_used")),
            )
        )
        sanitized_spec = spec.model_copy(
            update={
                "body_html": result.body_html,
                "page_css": result.page_css,
                "icon_keys": list(result.resolved_icon_keys),
            }
        )
        return {
            "current_html_slide_spec": sanitized_spec.model_dump(mode="json"),
            "current_sanitized_body_html": result.body_html,
            "current_sanitized_page_css": result.page_css,
            "current_assembled_html": result.document,
            "current_diagnostics": diagnostics,
        }
    except ValueError as exc:
        safe = assembler.assemble(
            body_html='<section class="slide" data-layout="statement_focus"></section>',
            page_css="",
            design_spec=design,
            language=brief.language,
            declared_icon_keys=[],
            expected_layout=spec.layout_family.value,
        )
        diagnostic = RendererDiagnostic(
            code="SANITIZER_REJECTED_OUTPUT",
            category=DiagnosticCategory.SECURITY,
            severity=DiagnosticSeverity.ERROR,
            message=str(exc),
            suggestion="Return a smaller fragment that follows the HTML/CSS contract.",
        )
        return {
            "current_sanitized_body_html": safe.body_html,
            "current_sanitized_page_css": safe.page_css,
            "current_assembled_html": safe.document,
            "current_diagnostics": [diagnostic.model_dump(mode="json")],
        }


def render_html_and_measure_dom(
    state: DeckGraphState,
    browser_renderer: HTMLBrowserRenderer,
) -> dict[str, Any]:
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    get_stream_writer()(
        events.stage_started(
            "render_html_and_measure_dom",
            f"Checking page {slide.index} in isolated Chromium",
        )
    )
    measurement = browser_renderer.measure(
        str(state.get("current_assembled_html") or ""),
        font_families=(design.typography.chinese_font, design.typography.latin_font),
    )
    diagnostics = list(state.get("current_diagnostics") or [])
    diagnostics.extend(_normalize_browser_diagnostic(item) for item in measurement.diagnostics)
    return {"current_diagnostics": _deduplicate_diagnostics(diagnostics)}


def validate_dom(state: DeckGraphState, *, max_repairs: int) -> dict[str, Any]:
    diagnostics = [item for item in state.get("current_diagnostics") or [] if isinstance(item, dict)]
    environment_errors = [
        item
        for item in diagnostics
        if item.get("severity") == "error" and item.get("category") == "environment"
    ]
    if environment_errors:
        raise BrowserEnvironmentError(str(environment_errors[0].get("message") or "renderer environment failed"))
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    if not errors:
        return {"current_render_decision": "valid"}
    if bool(state.get("current_fallback_used")):
        raise BrowserContentError(
            f"Program fallback failed deterministic DOM QA: {errors[0].get('code', 'DOM_INVALID')}"
        )
    attempts = max(0, int(state.get("current_repair_attempts") or 0))
    return {"current_render_decision": "repair" if attempts < max_repairs else "fallback"}


def repair_slide_html(
    state: DeckGraphState,
    output_dir: Path,
    llm_registry: dict[str, Any],
    *,
    max_repairs: int,
) -> dict[str, Any]:
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
    current = HtmlSlideSpec.model_validate(state.get("current_html_slide_spec") or {})
    attempts = max(0, int(state.get("current_repair_attempts") or 0)) + 1
    allowed_icons = list(icon_registry().keys())
    try:
        repaired = invoke_structured_with_retries(
            llm_for_node(llm_registry, "designer"),
            HtmlSlideSpec,
            system=load_node_prompt("html_repair"),
            user=json.dumps(
                {
                    "design_spec": design.model_dump(mode="json"),
                    "slide_plan": slide.model_dump(mode="json"),
                    "slide_content": content.model_dump(mode="json"),
                    "current_html_spec": current.model_dump(mode="json"),
                    "diagnostics": state.get("current_diagnostics") or [],
                    "recipe": get_recipe(slide.layout_family).prompt_manifest(),
                    "repair_attempt": attempts,
                    "repairs_remaining_after_this": max(0, max_repairs - attempts),
                },
                ensure_ascii=False,
            ),
            attempts=2,
        )
        repaired = _lock_html_spec(repaired, slide, allowed_icons)
        fallback_used = False
    except StructuredOutputError:
        record_fallback()
        repaired = build_fallback_html_spec(content, slide)
        fallback_used = True
    except ModelInvocationError:
        raise
    raw_path = _write_raw_debug(output_dir, state, repaired, attempt=attempts)
    return {
        "current_html_slide_spec": repaired.model_dump(mode="json"),
        "current_repair_attempts": attempts,
        "current_fallback_used": fallback_used,
        "current_raw_debug_path": raw_path,
        "current_diagnostics": [],
    }


def build_fallback_html(state: DeckGraphState) -> dict[str, Any]:
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
    fallback = build_fallback_html_spec(content, slide)
    record_fallback()
    return {
        "current_html_slide_spec": fallback.model_dump(mode="json"),
        "current_fallback_used": True,
        "current_diagnostics": [],
    }


def capture_png(
    state: DeckGraphState,
    output_dir: Path,
    browser_renderer: HTMLBrowserRenderer,
) -> dict[str, Any]:
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"])).ensure()
    captured = browser_renderer.capture(
        str(state.get("current_assembled_html") or ""),
        project.preview_png(slide.index),
        font_families=(design.typography.chinese_font, design.typography.latin_font),
    )
    return {
        "current_preview_path": project.relative(captured.path),
        "current_preview_hash": captured.sha256,
        "current_renderer_metadata": browser_renderer.metadata(
            font_families=(design.typography.chinese_font, design.typography.latin_font)
        ),
    }


def persist_slide_artifacts(
    state: DeckGraphState,
    output_dir: Path,
    assembler: HTMLAssembler,
    llm_registry: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(state["request_id"])
    project = DeckProjectPaths.for_request(output_dir, request_id).ensure()
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
    html_spec = HtmlSlideSpec.model_validate(state.get("current_html_slide_spec") or {})
    document = str(state.get("current_assembled_html") or "")
    diagnostics = list(state.get("current_diagnostics") or [])

    html_path = project.preview_html(slide.index)
    content_path = project.slide_content_spec(slide.index)
    html_spec_path = project.html_slide_spec(slide.index)
    diagnostics_path = project.slide_diagnostics(slide.index)
    _atomic_write_text(html_path, document)
    _atomic_write_json(content_path, content.model_dump(mode="json"))
    _atomic_write_json(html_spec_path, html_spec.model_dump(mode="json"))
    _atomic_write_json(diagnostics_path, {"slide_id": slide.slide_id, "diagnostics": diagnostics})

    content_hash = _json_hash(content.model_dump(mode="json"))
    html_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
    model_metadata = llm_registry.get("__metadata__") if isinstance(llm_registry.get("__metadata__"), dict) else {}
    artifact_inputs = {
        "content_hash": content_hash,
        "html_hash": html_hash,
        "preview_hash": str(state.get("current_preview_hash") or ""),
        "design_spec": design.model_dump(mode="json"),
        "prompt_version": PROMPT_VERSION,
        "renderer_version": RENDERER_VERSION,
        "base_css_hash": assembler.base_css_hash,
        "icon_registry_version": assembler.icon_registry_version,
        "render_environment": dict(state.get("current_renderer_metadata") or {}),
        "models": model_metadata,
    }
    artifact_hash = _json_hash(artifact_inputs)
    metadata = SlideArtifactMetadata(
        slide_id=slide.slide_id,
        index=slide.index,
        title=slide.assertion_title,
        layout_family=html_spec.layout_family,
        layout_variant=html_spec.layout_variant,
        composition_summary=html_spec.composition_summary,
        html_path=project.relative(html_path),
        preview_path=str(state.get("current_preview_path") or ""),
        content_hash=content_hash,
        html_hash=html_hash,
        preview_hash=str(state.get("current_preview_hash") or ""),
        artifact_hash=artifact_hash,
        event_id=_preview_event_id(
            request_id,
            slide.slide_id,
            content_hash=content_hash,
            preview_hash=str(state.get("current_preview_hash") or ""),
        ),
        renderer_version=RENDERER_VERSION,
        prompt_version=PROMPT_VERSION,
        base_css_hash=assembler.base_css_hash,
        icon_registry_version=assembler.icon_registry_version,
        render_environment=dict(state.get("current_renderer_metadata") or {}),
        model_metadata=model_metadata,
        repair_attempts=max(0, int(state.get("current_repair_attempts") or 0)),
        fallback_used=bool(state.get("current_fallback_used")),
        diagnostics_path=project.relative(diagnostics_path),
    )
    completed = dict(state.get("completed_slides") or {})
    completed[slide.slide_id] = metadata.model_dump(mode="json")
    ordered = sorted(completed.values(), key=lambda item: int(item.get("index") or 0))
    history = list(state.get("layout_history") or [])
    history = [item for item in history if item.get("slide_id") != slide.slide_id]
    history.append(
        {
            "slide_id": slide.slide_id,
            "layout_family": html_spec.layout_family.value,
            "composition_summary": html_spec.composition_summary,
        }
    )
    artifacts = [
        {
            "slide_id": item["slide_id"],
            "index": item["index"],
            "total": len(plan.slides),
            "title": item["title"],
            "png": item["preview_path"],
            "html": item["html_path"],
            "status": "ready",
            "renderer": "playwright-html",
            "layout_family": item["layout_family"],
            "layout_variant": item["layout_variant"],
            "artifact_hash": item["artifact_hash"],
        }
        for item in ordered
    ]
    _atomic_write_json(
        project.slide_artifact_manifest,
        {
            "schema": "slide-artifacts.v1",
            "request_id": request_id,
            "slides": ordered,
        },
    )
    existing_issues = [
        item
        for item in state.get("validation_issues") or []
        if isinstance(item, dict) and item.get("slide_id") != slide.slide_id
    ]
    existing_issues.extend(
        {**item, "slide_id": slide.slide_id, "index": slide.index}
        for item in diagnostics
        if isinstance(item, dict)
    )
    content_specs = _ordered_content_specs(project, ordered)
    return {
        "completed_slides": completed,
        "layout_history": history,
        "artifacts": artifacts,
        "slides": [item.model_dump(mode="json") for item in content_specs],
        "final_slides": [item.model_dump(mode="json") for item in content_specs],
        "validation_issues": existing_issues,
    }


def emit_slide_preview(state: DeckGraphState) -> dict[str, Any]:
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    metadata = dict((state.get("completed_slides") or {}).get(slide.slide_id) or {})
    get_stream_writer()(
        events.slide_preview(
            f"Page {slide.index}/{len(plan.slides)} preview ready: {slide.assertion_title}",
            {
                "slide_id": slide.slide_id,
                "index": slide.index,
                "total": len(plan.slides),
                "title": slide.assertion_title,
                "png": metadata.get("preview_path", ""),
                "artifact_hash": metadata.get("artifact_hash", ""),
                "event_id": metadata.get("event_id", ""),
            },
        )
    )
    return {}


def advance_slide(state: DeckGraphState) -> dict[str, Any]:
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    next_offset = max(0, int(state.get("current_slide_index") or 0)) + 1
    return {
        "current_slide_index": next_offset,
        "status": "exporting" if next_offset >= len(plan.slides) else "generating",
    }


def _lock_html_spec(spec: HtmlSlideSpec, slide: SlidePlan, allowed_icons: list[str]) -> HtmlSlideSpec:
    allowed = set(allowed_icons)
    return spec.model_copy(
        update={
            "slide_id": slide.slide_id,
            "layout_family": slide.layout_family,
            "icon_keys": [key for key in spec.icon_keys if key in allowed][:10],
        }
    )


def _deterministic_content_spec(brief: DeckBrief, slide: SlidePlan) -> SlideContentSpec:
    relevant = _rank_facts(brief, slide)[:5]
    refs = [fact.fact_id for fact in relevant]
    blocks: list[ContentBlock] = []
    if slide.layout_family is LayoutFamily.KEY_METRIC:
        fact = next((item for item in relevant if item.value), None)
        if fact:
            blocks.append(
                ContentBlock(
                    type=ContentBlockType.METRIC,
                    value=truncate(f"{fact.value}{fact.unit}", 80),
                    label=truncate(fact.statement, 120),
                )
            )
    elif slide.layout_family is LayoutFamily.COMPARISON:
        values = _bounded_fact_statements(relevant, slide, limit=6) or [
            truncate(brief.goal, 180),
            truncate(brief.audience_outcome, 180),
        ]
        midpoint = max(1, (len(values) + 1) // 2)
        blocks.extend(
            [
                ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="left", items=values[:midpoint]),
                ContentBlock(
                    type=ContentBlockType.COMPARISON_SIDE,
                    side="right",
                    items=values[midpoint:] or [truncate(slide.takeaway, 180)],
                ),
            ]
        )
    elif slide.layout_family in {LayoutFamily.PROCESS, LayoutFamily.TIMELINE}:
        block_type = (
            ContentBlockType.PROCESS_STEP
            if slide.layout_family is LayoutFamily.PROCESS
            else ContentBlockType.TIMELINE_ITEM
        )
        values = _bounded_fact_statements(relevant, slide, limit=5) or [truncate(slide.takeaway, 180)]
        blocks.extend(
            ContentBlock(type=block_type, label=f"{index:02d}", text=value, ordinal=index)
            for index, value in enumerate(values[:5], start=1)
        )
    elif slide.layout_family is LayoutFamily.THREE_POINTS:
        values = _bounded_fact_statements(relevant[:3], slide, limit=3) or [truncate(slide.takeaway, 180)]
        blocks.extend(
            ContentBlock(type=ContentBlockType.ICON_LABEL, label=f"{index:02d}", text=value)
            for index, value in enumerate(values, start=1)
        )
    else:
        blocks.append(ContentBlock(type=ContentBlockType.PARAGRAPH, text=slide.takeaway))
        fact_values = [
            value
            for value in _bounded_fact_statements(relevant, slide, limit=4)
            if value != slide.takeaway
        ]
        if fact_values:
            blocks.append(ContentBlock(type=ContentBlockType.BULLET_LIST, items=fact_values))
    if not blocks:
        blocks.append(ContentBlock(type=ContentBlockType.CALLOUT, text=slide.takeaway))
    icons = icon_registry().search(" ".join([slide.assertion_title, slide.takeaway]), limit=3)
    return SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=blocks,
        emphasis=[truncate(slide.takeaway, 180)],
        suggested_icon_keys=icons,
        source_refs=refs,
    )


def _normalize_content_for_recipe(
    content: SlideContentSpec,
    brief: DeckBrief,
    slide: SlidePlan,
) -> SlideContentSpec:
    """Apply recipe capacity and replace every metric with source ledger data."""

    capacity = get_recipe(slide.layout_family).capacity
    remaining_chars = capacity.body_chars
    remaining_items = capacity.items
    references = [ref for ref in content.source_refs if any(f.fact_id == ref for f in brief.facts)]
    valued_facts = [fact for fact in _rank_facts(brief, slide) if fact.value]
    metric_fact = next((fact for fact in valued_facts if fact.fact_id in references), None)
    if metric_fact is None and valued_facts:
        metric_fact = valued_facts[0]
    normalized: list[ContentBlock] = []
    item_blocks = {
        ContentBlockType.ICON_LABEL,
        ContentBlockType.PROCESS_STEP,
        ContentBlockType.TIMELINE_ITEM,
    }

    def consume(value: str, maximum: int) -> str:
        nonlocal remaining_chars
        clean = str(value or "").strip()
        if not clean or remaining_chars <= 0:
            return ""
        result = truncate(clean, min(maximum, remaining_chars))
        remaining_chars = max(0, remaining_chars - len(result))
        return result

    for block in content.content_blocks:
        if block.type is ContentBlockType.METRIC:
            if metric_fact is None:
                continue
            references.append(metric_fact.fact_id)
            metric_value = consume(f"{metric_fact.value}{metric_fact.unit}", 80)
            metric_label = consume(metric_fact.statement, 120)
            if metric_value or metric_label:
                normalized.append(
                    ContentBlock(
                        type=ContentBlockType.METRIC,
                        value=metric_value,
                        label=metric_label,
                    )
                )
            continue

        if block.type in item_blocks:
            if remaining_items <= 0:
                continue
            remaining_items -= 1
        items: list[str] = []
        for item in block.items:
            if remaining_items <= 0:
                break
            rendered = consume(item, 180)
            if rendered:
                items.append(rendered)
                remaining_items -= 1
        payload = {
            "type": block.type,
            "text": consume(block.text, 700),
            "items": items,
            "label": consume(block.label, 120),
            "value": consume(block.value, 80),
            "side": block.side,
            "ordinal": block.ordinal,
        }
        if payload["text"] or payload["items"] or payload["label"] or payload["value"]:
            normalized.append(ContentBlock.model_validate(payload))
        if remaining_chars <= 0:
            break

    if not normalized:
        return _deterministic_content_spec(brief, slide)
    return content.model_copy(
        update={
            "content_blocks": normalized[:10],
            "emphasis": [truncate(item, 180) for item in content.emphasis[:6]],
            "source_refs": list(dict.fromkeys(references))[:20],
        }
    )


def _assert_content_source_safe(
    content: SlideContentSpec,
    brief: DeckBrief,
    slide: SlidePlan,
) -> None:
    source = "\n".join(
        [
            brief.source_text,
            slide.assertion_title,
            slide.takeaway,
            *(f"{fact.statement} {fact.value}{fact.unit}" for fact in brief.facts),
        ]
    )
    allowed = set(extract_number_tokens(source))
    values: list[str] = [*content.emphasis]
    for block in content.content_blocks:
        values.extend([block.text, *block.items, block.value])
        if block.type not in {
            ContentBlockType.PROCESS_STEP,
            ContentBlockType.TIMELINE_ITEM,
            ContentBlockType.ICON_LABEL,
        }:
            values.append(block.label)
    used = {token for value in values for token in extract_number_tokens(value)}
    unknown = sorted(used - allowed)
    if unknown:
        raise StructuredOutputError(
            "SlideContentSpec introduced source-free numeric tokens: " + ", ".join(unknown[:8])
        )

    risky_claims = _content_qualitative_claims(content)
    if not risky_claims:
        return

    source_values = [brief.source_text]
    referenced_fact_ids = set(content.source_refs)
    for fact in brief.facts:
        if fact.fact_id not in referenced_fact_ids:
            continue
        source_values.extend(
            [
                fact.statement,
                fact.source_excerpt,
                fact.value,
                f"{fact.value}{fact.unit}",
                f"{fact.statement} {fact.value}{fact.unit}",
            ]
        )
    unsupported = unsupported_qualitative_claims(risky_claims, source_values)
    if unsupported:
        summary = "; ".join(
            f"{kind}={truncate(value, 100)!r}" for kind, value in unsupported[:4]
        )
        raise StructuredOutputError(
            "SlideContentSpec introduced unsupported qualitative factual claims: " + summary
        )


def _content_qualitative_claims(content: SlideContentSpec) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    for value in [
        content.assertion_title,
        content.takeaway,
        *content.emphasis,
    ]:
        claims.extend(_qualitative_claims(value))
    for block in content.content_blocks:
        is_quote = block.type is ContentBlockType.QUOTE
        for value in [block.text, *block.items, block.label, block.value]:
            claims.extend(_qualitative_claims(value, force_quote=is_quote))
    return _deduplicate_qualitative_claims(claims)


def _bounded_fact_statements(facts: list[Any], slide: SlidePlan, *, limit: int) -> list[str]:
    capacity = get_recipe(slide.layout_family).capacity
    item_count = max(1, min(limit, capacity.items or limit))
    per_item = max(60, min(180, capacity.body_chars // item_count if capacity.body_chars else 120))
    return [truncate(str(fact.statement), per_item) for fact in facts[:limit] if str(fact.statement).strip()]


def _rank_facts(brief: DeckBrief, slide: SlidePlan) -> list[Any]:
    terms = {
        token.casefold()
        for token in re.findall(r"[a-zA-Z0-9]{2,}|[\u3400-\u9fff]{2,4}", f"{slide.assertion_title} {slide.takeaway}")
    }
    return sorted(
        brief.facts,
        key=lambda fact: (
            -sum(1 for term in terms if term in fact.statement.casefold()),
            fact.fact_id,
        ),
    )


def _neighbor_plans(plan: DeckPlan, index: int) -> dict[str, Any]:
    offset = index - 1
    return {
        "previous": plan.slides[offset - 1].model_dump(mode="json") if offset > 0 else None,
        "next": plan.slides[offset + 1].model_dump(mode="json") if offset + 1 < len(plan.slides) else None,
    }


def _relevant_sources(state: DeckGraphState, slide: SlidePlan, *, limit: int = 6) -> list[dict[str, Any]]:
    chunks = [item for item in state.get("source_chunks") or [] if isinstance(item, dict)]
    terms = {
        token.casefold()
        for token in re.findall(r"[a-zA-Z0-9]{2,}|[\u3400-\u9fff]{2,4}", f"{slide.assertion_title} {slide.takeaway}")
    }
    ranked = sorted(
        chunks,
        key=lambda item: -sum(
            1 for term in terms if term in str(item.get("excerpt") or "").casefold()
        ),
    )
    return [
        {
            "source_file": item.get("source_file", ""),
            "page": item.get("page", 0),
            "section": item.get("section", ""),
            "excerpt": str(item.get("excerpt") or "")[:1_200],
        }
        for item in ranked[:limit]
    ]


def _write_raw_debug(
    output_dir: Path,
    state: DeckGraphState,
    spec: HtmlSlideSpec,
    *,
    attempt: int,
) -> str:
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"])).ensure()
    path = project.raw_model_output(slide.index, attempt)
    _atomic_write_json(path, spec.model_dump(mode="json"))
    # Internal state only.  This absolute path is deliberately outside the
    # public task artifact root and is never emitted as a ResourceRef.
    return str(path.resolve())


def _sanitizer_diagnostic(item: dict[str, Any]) -> dict[str, Any]:
    severity = str(item.get("severity") or "warning")
    return RendererDiagnostic(
        code=str(item.get("code") or "SANITIZER_DIAGNOSTIC"),
        category=DiagnosticCategory.SECURITY,
        severity=DiagnosticSeverity.ERROR if severity == "error" else DiagnosticSeverity.WARNING,
        message=str(item.get("message") or "Sanitizer changed model output."),
        suggestion="Use only the documented HTML, CSS, SVG, and icon contract.",
        details=dict(item.get("details") or {}),
    ).model_dump(mode="json")


def _normalize_browser_diagnostic(item: dict[str, Any]) -> dict[str, Any]:
    return RendererDiagnostic.model_validate(item).model_dump(mode="json")


def _content_contract_diagnostics(
    body_html: str,
    slide: SlidePlan,
    content: SlideContentSpec,
    *,
    fallback_used: bool,
) -> list[dict[str, Any]]:
    """Reject visually plausible HTML that changes or drops page semantics.

    Browser geometry cannot determine whether the model preserved the approved
    title, content, or evidence.  The deterministic recipe may deliberately
    truncate prose to fit, so strict text coverage applies to model-authored
    HTML while the numeric evidence check applies to both paths.
    """

    parser = html5lib.HTMLParser(namespaceHTMLElements=False)
    root = parser.parseFragment(str(body_html or ""))
    rendered_nodes = [str(item).strip() for item in root.itertext() if str(item).strip()]
    rendered = " ".join(rendered_nodes)
    rendered_canonical = _canonical_visible_text(rendered)
    diagnostics: list[dict[str, Any]] = []

    authoritative_values = [
        slide.assertion_title,
        slide.takeaway,
        content.assertion_title,
        content.takeaway,
        *content.emphasis,
    ]
    body_candidates: list[str] = [content.takeaway]
    for block in content.content_blocks:
        authoritative_values.extend(
            [block.text, *block.items, block.label, block.value, str(block.ordinal or "")]
        )
        body_candidates.extend([block.text, *block.items, block.label, block.value])

    allowed_numbers = {
        *extract_number_tokens("\n".join(authoritative_values)),
        str(slide.index),
        f"{slide.index:02d}",
    }
    if slide.layout_family in {
        LayoutFamily.THREE_POINTS,
        LayoutFamily.PROCESS,
        LayoutFamily.TIMELINE,
    }:
        for ordinal in range(1, get_recipe(slide.layout_family).capacity.items + 1):
            allowed_numbers.update({str(ordinal), f"{ordinal:02d}"})
    used_numbers = set(extract_number_tokens(rendered))
    unexpected_numbers = sorted(used_numbers - allowed_numbers)
    if unexpected_numbers:
        diagnostics.append(
            _content_diagnostic(
                "HTML_INTRODUCED_NUMBERS",
                "Rendered HTML introduced numeric claims absent from SlideContentSpec.",
                "Use only source-backed numbers already present in the approved content.",
                {"unexpected_numbers": unexpected_numbers[:12]},
            )
        )

    diagnostics.extend(_html_qualitative_claim_diagnostics(root, content))

    if fallback_used:
        return diagnostics

    title = _canonical_visible_text(content.assertion_title)
    if title and title not in rendered_canonical:
        diagnostics.append(
            _content_diagnostic(
                "ASSERTION_TITLE_MISSING",
                "Rendered HTML does not preserve the approved assertion title.",
                "Render the exact SlideContentSpec assertion_title in the title box.",
            )
        )

    meaningful_candidates = [
        _canonical_visible_text(value)
        for value in body_candidates
        if len(_canonical_visible_text(value)) >= 4
    ]
    if meaningful_candidates and not any(value in rendered_canonical for value in meaningful_candidates):
        diagnostics.append(
            _content_diagnostic(
                "APPROVED_CONTENT_MISSING",
                "Rendered HTML contains none of the approved body content.",
                "Use the supplied takeaway or content blocks instead of replacing the page message.",
            )
        )

    for block in content.content_blocks:
        if block.type is not ContentBlockType.METRIC or not block.value:
            continue
        if _canonical_visible_text(block.value) not in rendered_canonical:
            diagnostics.append(
                _content_diagnostic(
                    "METRIC_VALUE_MISSING",
                    "A source-backed metric value is absent from the rendered HTML.",
                    "Restore the exact approved metric value.",
                    {"value": block.value},
                )
            )
    return diagnostics


def _canonical_visible_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", normalized)


def _html_qualitative_claim_diagnostics(
    root: Any,
    content: SlideContentSpec,
) -> list[dict[str, Any]]:
    approved = _content_qualitative_claims(content)
    rendered_claims: list[tuple[str, str]] = []
    semantic_tags = {
        "p",
        "li",
        "q",
        "cite",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    for element in root.iter():
        tag = str(getattr(element, "tag", "")).rsplit("}", 1)[-1].casefold()
        if tag == "blockquote":
            quote = " ".join(str(item).strip() for item in element.itertext() if str(item).strip())
            rendered_claims.extend(_qualitative_claims(quote, force_quote=True))

    rendered_claims.extend(
        _container_local_text_qualitative_claims(root, approved, semantic_tags)
    )

    # Inline markup can split a single assertion across several text nodes.
    # Reconstruct only semantic text containers, and only when their individual
    # nodes did not already expose a claim, to avoid treating a whole layout box
    # as one synthetic sentence.
    for element in root.iter():
        tag = str(getattr(element, "tag", "")).rsplit("}", 1)[-1].casefold()
        if tag not in semantic_tags and not getattr(element, "attrib", {}).get("data-box"):
            continue
        parts = [str(item).strip() for item in element.itertext() if str(item).strip()]
        if len(parts) < 2 or any(_qualitative_claims(part) for part in parts):
            continue
        rendered_claims.extend(_qualitative_claims(" ".join(parts)))

    # A designer can distribute one visual sentence across sibling elements
    # so that no individual text node contains a red-flag pattern. Rebuild
    # short adjacent windows under the same parent and evaluate the sentence
    # the audience actually reads.
    rendered_claims.extend(_adjacent_sibling_qualitative_claims(root))

    introduced = [
        (kind, value)
        for kind, value in _deduplicate_qualitative_claims(rendered_claims)
        if not _rendered_claim_is_approved(kind, value, approved)
    ]
    if not introduced:
        return []
    return [
        _content_diagnostic(
            "HTML_INTRODUCED_QUALITATIVE_CLAIM",
            "Rendered HTML introduced a quote or factual claim absent from SlideContentSpec.",
            "Use only the approved quote, attribution, customer case, and realized-outcome text.",
            {
                "claims": [
                    {"kind": kind, "text": truncate(value, 160)}
                    for kind, value in introduced[:8]
                ]
            },
        )
    ]


def _container_local_text_qualitative_claims(
    root: Any,
    approved: list[tuple[str, str]],
    semantic_tags: set[str],
) -> list[tuple[str, str]]:
    approved_values = {
        _canonical_claim_text(value)
        for _kind, value in approved
        if _canonical_claim_text(value)
    }
    container_values: dict[int, str] = {}
    claims: list[tuple[str, str]] = []

    def container_canonical(element: Any) -> str:
        key = id(element)
        if key not in container_values:
            visible = " ".join(
                str(item).strip() for item in element.itertext() if str(item).strip()
            )
            container_values[key] = _canonical_claim_text(visible)
        return container_values[key]

    def add_text(value: str, container: Any | None) -> None:
        text = str(value or "").strip()
        if not text:
            return
        container_value = container_canonical(container) if container is not None else ""
        container_is_exact = bool(container_value and container_value in approved_values)
        for kind, claim in _qualitative_claims(text):
            canonical = _canonical_claim_text(claim)
            if (
                container_is_exact
                and canonical
                and canonical != container_value
                and canonical in container_value
            ):
                continue
            claims.append((kind, claim))

    def visit(element: Any, container: Any | None) -> None:
        tag = str(getattr(element, "tag", "")).rsplit("}", 1)[-1].casefold()
        current = (
            element
            if tag in semantic_tags or getattr(element, "attrib", {}).get("data-box")
            else container
        )
        add_text(str(getattr(element, "text", "") or ""), current)
        for child in list(element):
            visit(child, current)
            add_text(str(getattr(child, "tail", "") or ""), current)

    visit(root, None)
    return claims


def _adjacent_sibling_qualitative_claims(root: Any) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    for parent in root.iter():
        parts: list[str] = []
        parent_text = str(getattr(parent, "text", "") or "").strip()
        if parent_text:
            parts.append(parent_text)
        for child in list(parent):
            child_text = " ".join(
                str(item).strip() for item in child.itertext() if str(item).strip()
            )
            if child_text:
                parts.append(child_text)
            tail = str(getattr(child, "tail", "") or "").strip()
            if tail:
                parts.append(tail)
        if len(parts) < 2:
            continue
        for width in (2, 3):
            for offset in range(0, len(parts) - width + 1):
                window = parts[offset : offset + width]
                if any(_qualitative_claims(part) for part in window):
                    continue
                claims.extend(_qualitative_claims(" ".join(window)))
    return claims


def _rendered_claim_is_approved(
    kind: str,
    value: str,
    approved: list[tuple[str, str]],
) -> bool:
    canonical = _canonical_claim_text(value)
    if not canonical:
        return True

    if kind == "quote":
        approved_quotes = {
            _canonical_claim_text(item)
            for approved_kind, item in approved
            if approved_kind == "quote" and _canonical_claim_text(item)
        }
        return canonical in approved_quotes

    approved_values = {
        _canonical_claim_text(item)
        for _approved_kind, item in approved
        if _canonical_claim_text(item)
    }
    return canonical in approved_values


def _content_diagnostic(
    code: str,
    message: str,
    suggestion: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return RendererDiagnostic(
        code=code,
        category=DiagnosticCategory.CONTENT,
        severity=DiagnosticSeverity.ERROR,
        message=message,
        suggestion=suggestion,
        details=details or {},
    ).model_dump(mode="json")


def _deduplicate_diagnostics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _ordered_content_specs(
    project: DeckProjectPaths,
    ordered_metadata: list[dict[str, Any]],
) -> list[SlideContentSpec]:
    result: list[SlideContentSpec] = []
    for item in ordered_metadata:
        path = project.slide_content_spec(int(item.get("index") or 0))
        result.append(SlideContentSpec.model_validate_json(path.read_text(encoding="utf-8")))
    return result


def _json_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preview_event_id(
    request_id: str,
    slide_id: str,
    *,
    content_hash: str,
    preview_hash: str,
) -> str:
    """Return the replay-stable identity for one exact rendered preview."""

    identity = hashlib.sha256(f"{content_hash}:{preview_hash}".encode("ascii")).hexdigest()
    return f"slide-preview:{request_id}:{slide_id}:{identity[:20]}"


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
    "GenerationNodes",
    "MAX_SEMANTIC_REPAIRS",
    "advance_slide",
    "build_fallback_html",
    "capture_png",
    "emit_slide_preview",
    "generate_slide_content",
    "generate_slide_html",
    "persist_slide_artifacts",
    "prepare_slide",
    "render_html_and_measure_dom",
    "repair_slide_html",
    "sanitize_and_assemble_html",
    "validate_dom",
]
