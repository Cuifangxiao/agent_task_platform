"""Per-slide content authoring, HTML generation, DOM QA, repair, and capture."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

import html5lib
from langgraph.config import get_stream_writer

import core.deck.pipeline.events as events
from core.deck.design_system.catalog import get_recipe
from core.deck.html import HTMLAssembler, icon_registry
from core.deck.domain.text import extract_number_tokens, truncate
from core.deck.runtime.llm import (
    invoke_structured_with_retries,
    llm_for_node,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.workspace.paths import DeckProjectPaths
from core.deck.html.browser import (
    BrowserContentError,
    BrowserEnvironmentError,
    HTMLBrowserRenderer,
)
from core.deck.domain.schemas import (
    ContentBlock,
    ContentBlockType,
    DeckOutlineSpec,
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
    SlideDesignPlan,
    SlideOutlineSpec,
    SlidePlan,
)
from core.deck.pipeline.state import DeckGraphState


MAX_SEMANTIC_REPAIRS = 2
CONTENT_DENSITY_CONTRACT = {
    "cover": "title, subtitle or value proposition, and only source-backed metadata when available",
    "section_break": "one transition statement plus 2 concise preview points",
    "standard_page": "2-4 substantive evidence points plus one clear takeaway",
    "data_page": "2-4 source-backed metrics or comparisons plus an explicit implication",
    "comparison": "2-3 matched points per side plus a conclusion",
    "process_or_timeline": "3-5 meaningful stages or events",
    "sparse_source_policy": (
        "use stable explanatory knowledge already approved in the outline; never invent dates, "
        "numbers, quotations, organizations, outcomes, or sources"
    ),
}


class SlideProgressTracker:
    """Thread-safe per-request completion counts for out-of-order previews."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._completed_by_request: dict[str, set[str]] = {}

    def mark_completed(self, request_id: str, slide_id: str) -> int:
        with self._lock:
            completed = self._completed_by_request.setdefault(str(request_id), set())
            completed.add(str(slide_id))
            return len(completed)


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
        self.progress_tracker = SlideProgressTracker()
        self._preview_emit_lock = threading.Lock()

    def prepare_slide(self, state: DeckGraphState) -> dict[str, Any]:
        return prepare_slide(state)

    def generate_slide_design(self, state: DeckGraphState) -> dict[str, Any]:
        return generate_slide_design(state, self.output_dir, self.llm_registry)

    def generate_slide_html(self, state: DeckGraphState) -> dict[str, Any]:
        return generate_slide_html(state, self.output_dir, self.llm_registry)

    def sanitize_and_assemble_html(self, state: DeckGraphState) -> dict[str, Any]:
        return sanitize_and_assemble_html(state, self.assembler)

    def render_html_and_measure_dom(self, state: DeckGraphState) -> dict[str, Any]:
        return render_html_and_measure_dom(state, self.output_dir, self.browser_renderer)

    def validate_dom(self, state: DeckGraphState) -> dict[str, Any]:
        return validate_dom(state, self.output_dir, max_repairs=self.max_repairs)

    def repair_slide_html(self, state: DeckGraphState) -> dict[str, Any]:
        return repair_slide_html(
            state,
            self.output_dir,
            self.llm_registry,
            max_repairs=self.max_repairs,
        )

    def capture_png(self, state: DeckGraphState) -> dict[str, Any]:
        return capture_png(state, self.output_dir, self.browser_renderer)

    def persist_slide_artifacts(self, state: DeckGraphState) -> dict[str, Any]:
        return persist_slide_artifacts(state, self.output_dir, self.assembler, self.llm_registry)

    def emit_slide_preview(self, state: DeckGraphState) -> dict[str, Any]:
        slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
        lock = getattr(self, "_preview_emit_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._preview_emit_lock = lock
        with lock:
            return emit_slide_preview(
                state,
                completed_count=self.progress_tracker.mark_completed(
                    str(state["request_id"]),
                    slide.slide_id,
                ),
            )

    def aggregate_slides(self, state: DeckGraphState) -> dict[str, Any]:
        return aggregate_slides(state, self.output_dir)

def prepare_slide(state: DeckGraphState) -> dict[str, Any]:
    outline = DeckOutlineSpec.model_validate(state.get("deck_outline") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    offset = max(0, int(state.get("current_slide_index") or 0))
    if offset >= len(plan.slides):
        raise ValueError("prepare_slide called after the final planned page")
    slide = plan.slides[offset]
    outline_slide = outline.slides[offset]
    if outline_slide.id != slide.slide_id:
        raise ValueError(
            f"outline/renderer slide mismatch at page {offset + 1}: "
            f"{outline_slide.id} != {slide.slide_id}"
        )
    emit_event = get_stream_writer()
    emit_event(
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
        "current_slide_outline": outline_slide.model_dump(mode="json"),
        "current_slide_plan": slide.model_dump(mode="json"),
        "current_slide_design_plan": {},
        "current_slide_content_spec": _deterministic_content_spec(
            outline_slide,
            slide,
        ).model_dump(mode="json"),
        "current_html_slide_spec": {},
        "current_assembled_html": "",
        "current_diagnostics": [],
        "current_repair_attempts": 0,
        "current_render_decision": "repair",
        "current_speculative_preview_path": "",
        "current_speculative_preview_hash": "",
        "current_preview_path": "",
        "current_preview_hash": "",
        "current_renderer_metadata": {},
        "status": "generating",
    }


def generate_slide_design(
    state: DeckGraphState,
    output_dir: Path,
    llm_registry: dict[str, Any],
) -> dict[str, Any]:
    outline = DeckOutlineSpec.model_validate(state.get("deck_outline") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    outline_slide = SlideOutlineSpec.model_validate(state.get("current_slide_outline") or {})
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    recipe = get_recipe(slide.layout_family)
    preference = dict(state.get("current_layout_preference") or {})
    expected_layout = str(preference.get("layout_family") or slide.layout_family.value)
    design_plan = invoke_structured_with_retries(
        llm_for_node(llm_registry, "style"),
        SlideDesignPlan,
        system=load_node_prompt("slide_visual_planner"),
        user=json.dumps(
            {
                "deck": {
                    "title": outline.title,
                    "subtitle": outline.subtitle,
                    "metadata": outline.metadata.model_dump(
                        mode="json",
                        exclude={"global_style_spec"},
                    ),
                },
                "global_style_spec": outline.metadata.global_style_spec.model_dump(mode="json"),
                "source_markdown": str(state.get("source_text") or ""),
                "current_slide": outline_slide.model_dump(mode="json"),
                "slide_summaries": [
                    {
                        "slide_id": item.id,
                        "title": item.title,
                        "type": item.type.value,
                        "layout_suggestion": item.layout_suggestion.value,
                        "summary": truncate(item.content_note, 160),
                    }
                    for item in outline.slides
                ],
                "neighbor_slides": _neighbor_plans(plan, slide.index),
                "locked_layout": preference,
                "supported_layout_types": [item.value for item in LayoutFamily],
                "recipe": recipe.prompt_manifest(),
                "approved_fonts": [
                    design.typography.chinese_font,
                    design.typography.latin_font,
                ],
                "relevant_sources": _relevant_sources(state, slide),
                "relevant_assets": _relevant_assets(state, slide),
                "content_density_contract": CONTENT_DENSITY_CONTRACT,
                "hard_constraints": {
                    "slide_id": slide.slide_id,
                    "layout_type": expected_layout,
                    "inherit_global_style_exactly": True,
                    "do_not_return_hidden_chain_of_thought": True,
                },
            },
            ensure_ascii=False,
        ),
        attempts=3,
        output_validator=lambda value: _validate_design_plan(
            SlideDesignPlan.model_validate(value),
            outline_slide=outline_slide,
            expected_layout=slide.layout_family,
            global_style=outline.metadata.global_style_spec.model_dump(mode="json"),
            approved_fonts=[
                design.typography.chinese_font,
                design.typography.latin_font,
            ],
        ),
    )
    design_plan = SlideDesignPlan.model_validate(design_plan)
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"])).ensure()
    _atomic_write_json(project.slide_design_spec(slide.index), design_plan.model_dump(mode="json"))
    return {"current_slide_design_plan": design_plan.model_dump(mode="json")}


def generate_slide_html(
    state: DeckGraphState,
    output_dir: Path,
    llm_registry: dict[str, Any],
) -> dict[str, Any]:
    outline = DeckOutlineSpec.model_validate(state.get("deck_outline") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    outline_slide = SlideOutlineSpec.model_validate(state.get("current_slide_outline") or {})
    design_plan = SlideDesignPlan.model_validate(state.get("current_slide_design_plan") or {})
    content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
    recipe = get_recipe(slide.layout_family)
    allowed_icons = sorted(set(content.suggested_icon_keys + icon_registry().search(slide.takeaway, limit=8)))[:12]
    spec = invoke_structured_with_retries(
        llm_for_node(llm_registry, "designer"),
        HtmlSlideSpec,
        system=load_node_prompt("slide_html_builder"),
        user=json.dumps(
            {
                "deck": {
                    "title": outline.title,
                    "subtitle": outline.subtitle,
                    "language": outline.metadata.language,
                    "source_summary": outline.metadata.source_summary,
                },
                "design_spec": design.model_dump(mode="json"),
                "global_style_spec": outline.metadata.global_style_spec.model_dump(mode="json"),
                "source_markdown": str(state.get("source_text") or ""),
                "current_slide": outline_slide.model_dump(mode="json"),
                "slide_design_plan": design_plan.model_dump(mode="json"),
                "slide_content": content.model_dump(mode="json"),
                "neighbor_slides": _neighbor_plans(plan, slide.index),
                "recipe": recipe.prompt_manifest(),
                "relevant_sources": _relevant_sources(state, slide),
                "relevant_assets": _relevant_assets(state, slide),
                "content_density_contract": CONTENT_DENSITY_CONTRACT,
                "available_icon_keys": allowed_icons,
                "layout_preference": dict(state.get("current_layout_preference") or {}),
                "previous_compositions": list(state.get("previous_compositions") or [])[-4:],
                "html_contract": {
                    "canvas": "1600x900",
                    "root": "exactly one section.slide",
                    "semantic_boxes": "data-box",
                    "decorations": "data-decorative=true",
                    "external_resources": "forbidden",
                    "javascript": "forbidden",
                    "animations_and_transitions": "forbidden",
                    "gradients": "forbidden",
                    "output": "HtmlSlideSpec JSON with body fragment and .slide-scoped CSS",
                },
            },
            ensure_ascii=False,
        ),
        attempts=3,
        output_validator=lambda value: _validate_and_lock_html_spec(
            HtmlSlideSpec.model_validate(value),
            slide=slide,
            allowed_icons=allowed_icons,
        ),
    )
    spec = HtmlSlideSpec.model_validate(spec)

    _write_raw_debug(output_dir, state, spec, attempt=0)
    return {"current_html_slide_spec": spec.model_dump(mode="json")}


def sanitize_and_assemble_html(state: DeckGraphState, assembler: HTMLAssembler) -> dict[str, Any]:
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    spec = HtmlSlideSpec.model_validate(state.get("current_html_slide_spec") or {})
    outline = DeckOutlineSpec.model_validate(state.get("deck_outline") or {})
    outline_slide = SlideOutlineSpec.model_validate(state.get("current_slide_outline") or {})
    try:
        result = assembler.assemble(
            body_html=spec.body_html,
            page_css=spec.page_css,
            design_spec=design,
            language=outline.metadata.language,
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
                total_slides=len(outline.slides),
                approved_context=[
                    outline_slide.title,
                    *outline_slide.key_points,
                    outline_slide.content_note,
                    str(state.get("source_text") or ""),
                    *[
                        str(item.get("excerpt") or "")
                        for item in _relevant_sources(state, slide)
                    ],
                ],
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
            "current_assembled_html": result.document,
            "current_diagnostics": diagnostics,
        }
    except ValueError as exc:
        safe = assembler.assemble(
            body_html='<section class="slide" data-layout="statement_focus"></section>',
            page_css="",
            design_spec=design,
            language=outline.metadata.language,
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
            "current_assembled_html": safe.document,
            "current_diagnostics": [diagnostic.model_dump(mode="json")],
        }


def render_html_and_measure_dom(
    state: DeckGraphState,
    output_dir: Path,
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
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"])).ensure()
    speculative_path = _speculative_preview_png(project, slide.index)
    existing_diagnostics = [
        item for item in state.get("current_diagnostics") or [] if isinstance(item, dict)
    ]
    document = str(state.get("current_assembled_html") or "")
    font_families = (design.typography.chinese_font, design.typography.latin_font)
    captured = None
    if _has_error_diagnostic(existing_diagnostics):
        measurement = browser_renderer.measure(document, font_families=font_families)
    else:
        rendered = browser_renderer.measure_and_capture(
            document,
            speculative_path,
            font_families=font_families,
        )
        measurement = rendered.measurement
        captured = rendered.captured_png
    diagnostics = list(existing_diagnostics)
    diagnostics.extend(_normalize_browser_diagnostic(item) for item in measurement.diagnostics)
    return {
        "current_diagnostics": _deduplicate_diagnostics(diagnostics),
        "current_speculative_preview_path": project.relative(captured.path) if captured else "",
        "current_speculative_preview_hash": captured.sha256 if captured else "",
    }


def validate_dom(
    state: DeckGraphState,
    output_dir: Path,
    *,
    max_repairs: int,
) -> dict[str, Any]:
    diagnostics = [item for item in state.get("current_diagnostics") or [] if isinstance(item, dict)]
    environment_errors = [
        item
        for item in diagnostics
        if item.get("severity") == "error" and item.get("category") == "environment"
    ]
    if environment_errors:
        _discard_speculative_preview(state, output_dir)
        raise BrowserEnvironmentError(str(environment_errors[0].get("message") or "renderer environment failed"))
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    if not errors:
        return {"current_render_decision": "valid"}
    _discard_speculative_preview(state, output_dir)
    attempts = max(0, int(state.get("current_repair_attempts") or 0))
    if attempts >= max_repairs:
        first = errors[0]
        code = str(first.get("code") or "DOM_INVALID")
        message = str(first.get("message") or "slide validation failed")
        slide_data = state.get("current_slide_plan") or {}
        page_index = int(slide_data.get("index") or 0) if isinstance(slide_data, dict) else 0
        page_label = f"Page {page_index}" if page_index > 0 else "Page"
        raise BrowserContentError(
            f"{page_label} validation failed after {attempts} repair attempt(s): "
            f"{code}: {message}"
        )
    return {
        "current_render_decision": "repair",
        "current_speculative_preview_path": "",
        "current_speculative_preview_hash": "",
    }


def repair_slide_html(
    state: DeckGraphState,
    output_dir: Path,
    llm_registry: dict[str, Any],
    *,
    max_repairs: int,
) -> dict[str, Any]:
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    outline = DeckOutlineSpec.model_validate(state.get("deck_outline") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    outline_slide = SlideOutlineSpec.model_validate(state.get("current_slide_outline") or {})
    design_plan = SlideDesignPlan.model_validate(state.get("current_slide_design_plan") or {})
    content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
    current = HtmlSlideSpec.model_validate(state.get("current_html_slide_spec") or {})
    attempts = max(0, int(state.get("current_repair_attempts") or 0)) + 1
    allowed_icons = sorted(
        set(content.suggested_icon_keys + icon_registry().search(slide.takeaway, limit=8))
    )[:12]
    repaired = invoke_structured_with_retries(
        llm_for_node(llm_registry, "designer"),
        HtmlSlideSpec,
        system=load_node_prompt("html_repair"),
        user=json.dumps(
            {
                "design_spec": design.model_dump(mode="json"),
                "global_style_spec": outline.metadata.global_style_spec.model_dump(mode="json"),
                "slide_outline": outline_slide.model_dump(mode="json"),
                "slide_design_plan": design_plan.model_dump(mode="json"),
                "slide_plan": slide.model_dump(mode="json"),
                "slide_content": content.model_dump(mode="json"),
                "current_html_spec": current.model_dump(mode="json"),
                "diagnostics": state.get("current_diagnostics") or [],
                "recipe": get_recipe(slide.layout_family).prompt_manifest(),
                "available_icon_keys": allowed_icons,
                "repair_attempt": attempts,
                "repairs_remaining_after_this": max(0, max_repairs - attempts),
            },
            ensure_ascii=False,
        ),
        attempts=2,
        output_validator=lambda value: _validate_and_lock_html_spec(
            HtmlSlideSpec.model_validate(value),
            slide=slide,
            allowed_icons=allowed_icons,
        ),
    )
    repaired = HtmlSlideSpec.model_validate(repaired)
    _write_raw_debug(output_dir, state, repaired, attempt=attempts)
    return {
        "current_html_slide_spec": repaired.model_dump(mode="json"),
        "current_repair_attempts": attempts,
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
    relative_speculative = str(state.get("current_speculative_preview_path") or "").strip()
    speculative = _resolve_project_artifact(project, relative_speculative)
    if not speculative.is_file():
        raise BrowserEnvironmentError("speculative PNG is missing after DOM validation")
    actual_hash = hashlib.sha256(speculative.read_bytes()).hexdigest()
    expected_hash = str(state.get("current_speculative_preview_hash") or "")
    if not expected_hash or actual_hash != expected_hash:
        raise BrowserEnvironmentError("speculative PNG changed after DOM validation")
    preview_path = project.preview_png(slide.index)
    os.replace(speculative, preview_path)
    return {
        "current_speculative_preview_path": "",
        "current_speculative_preview_hash": "",
        "current_preview_path": project.relative(preview_path),
        "current_preview_hash": actual_hash,
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
    design = DesignSpec.model_validate(state.get("design_spec") or {})
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    design_plan = SlideDesignPlan.model_validate(state.get("current_slide_design_plan") or {})
    content = SlideContentSpec.model_validate(state.get("current_slide_content_spec") or {})
    html_spec = HtmlSlideSpec.model_validate(state.get("current_html_slide_spec") or {})
    document = str(state.get("current_assembled_html") or "")
    diagnostics = list(state.get("current_diagnostics") or [])

    html_path = project.preview_html(slide.index)
    content_path = project.slide_content_spec(slide.index)
    design_plan_path = project.slide_design_spec(slide.index)
    html_spec_path = project.html_slide_spec(slide.index)
    diagnostics_path = project.slide_diagnostics(slide.index)
    _atomic_write_text(html_path, document)
    _atomic_write_json(content_path, content.model_dump(mode="json"))
    _atomic_write_json(design_plan_path, design_plan.model_dump(mode="json"))
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
        "slide_design_plan": design_plan.model_dump(mode="json"),
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
        design_plan_path=project.relative(design_plan_path),
        html_spec_path=project.relative(html_spec_path),
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
        diagnostics_path=project.relative(diagnostics_path),
    )
    completed = dict(state.get("completed_slides") or {})
    completed[slide.slide_id] = metadata.model_dump(mode="json")
    history = list(state.get("layout_history") or [])
    history = [item for item in history if item.get("slide_id") != slide.slide_id]
    history.append(
        {
            "slide_id": slide.slide_id,
            "index": slide.index,
            "layout_family": html_spec.layout_family.value,
            "layout_variant": html_spec.layout_variant,
            "composition_summary": html_spec.composition_summary,
            "estimated_density": html_spec.estimated_density,
        }
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
    return {
        "completed_slides": completed,
        "layout_history": history,
        "validation_issues": existing_issues,
    }


def aggregate_slides(state: DeckGraphState, output_dir: Path) -> dict[str, Any]:
    """Validate and persist the reducer-merged results after Send fan-in."""

    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    completed = dict(state.get("completed_slides") or {})
    planned_ids = [slide.slide_id for slide in plan.slides]
    missing = [slide_id for slide_id in planned_ids if slide_id not in completed]
    unexpected = sorted(set(completed) - set(planned_ids))
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        raise ValueError("parallel slide fan-in is incomplete: " + " ".join(details))

    ordered = sorted(completed.values(), key=lambda item: int(item.get("index") or 0))
    request_id = str(state.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("request_id is required before slide aggregation")
    project = DeckProjectPaths.for_request(output_dir, request_id).ensure()
    _atomic_write_json(
        project.slide_artifact_manifest,
        {
            "schema": "slide-artifacts.v1",
            "request_id": request_id,
            "slides": ordered,
        },
    )
    return {
        "current_slide_index": len(plan.slides),
        "status": "exporting",
    }


def emit_slide_preview(
    state: DeckGraphState,
    *,
    completed_count: int | None = None,
) -> dict[str, Any]:
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
                "completed": completed_count,
                "title": slide.assertion_title,
                "png": metadata.get("preview_path", ""),
                "artifact_hash": metadata.get("artifact_hash", ""),
                "event_id": metadata.get("event_id", ""),
            },
        )
    )
    return {}


def _validate_design_plan(
    plan: SlideDesignPlan,
    *,
    outline_slide: SlideOutlineSpec,
    expected_layout: LayoutFamily,
    global_style: dict[str, Any],
    approved_fonts: list[str] | None = None,
) -> SlideDesignPlan:
    if plan.slide_id != outline_slide.id:
        raise ValueError(
            f"design slide_id {plan.slide_id!r} does not match outline {outline_slide.id!r}"
        )
    if plan.design_specifications.layout_type is not expected_layout:
        raise ValueError(
            "design layout_type does not match the deterministic page layout lock"
        )
    if plan.design_specifications.color_scheme.model_dump(mode="json") != global_style:
        raise ValueError("design color_scheme must exactly equal GlobalStyleSpec")
    if approved_fonts is not None:
        declared_fonts = [
            item.strip().strip("\"'")
            for item in plan.design_specifications.typography.font_family.split(",")
            if item.strip()
        ]
        allowed_fonts = {item.strip() for item in approved_fonts if item.strip()}
        if not declared_fonts or any(item not in allowed_fonts for item in declared_fonts):
            raise ValueError("design typography must use only approved local fonts")
    return plan


def _validate_and_lock_html_spec(
    spec: HtmlSlideSpec,
    *,
    slide: SlidePlan,
    allowed_icons: list[str],
) -> HtmlSlideSpec:
    if spec.slide_id != slide.slide_id:
        raise ValueError(
            f"HTML slide_id {spec.slide_id!r} does not match design page {slide.slide_id!r}"
        )
    if spec.layout_family is not slide.layout_family:
        raise ValueError("HTML layout_family does not match the approved SlideDesignPlan")
    allowed = set(allowed_icons)
    return spec.model_copy(
        update={
            "icon_keys": [key for key in spec.icon_keys if key in allowed][:10],
        }
    )


def _deterministic_content_spec(
    outline_slide: SlideOutlineSpec,
    slide: SlidePlan,
) -> SlideContentSpec:
    """Derive the renderer's approved-copy guard from the slide outline."""

    blocks: list[ContentBlock] = []
    points = [truncate(item, 180) for item in outline_slide.key_points]
    if slide.layout_family is LayoutFamily.KEY_METRIC:
        metric_tokens = extract_number_tokens(" ".join([*points, slide.takeaway]))
        if not metric_tokens:
            metric_tokens = extract_number_tokens(slide.assertion_title)
        if not metric_tokens:
            note_without_locators = re.sub(
                r"(?i)\b(?:page|slide|section)\s*#?\s*\d+\b|"
                r"第?\s*\d+\s*(?:页|頁|章|节|節)",
                " ",
                outline_slide.content_note,
            )
            metric_tokens = extract_number_tokens(note_without_locators)
        if metric_tokens:
            blocks.append(
                ContentBlock(
                    type=ContentBlockType.METRIC,
                    value=truncate(metric_tokens[0], 80),
                    label=truncate(slide.assertion_title, 120),
                )
            )
        elif points:
            # ``key_metric`` is also used for formulas and single mechanisms,
            # not only numeric KPIs.  Keep their approved evidence in the
            # renderer contract instead of collapsing the page to takeaway
            # text and then falsely rejecting a faithful formula rendering.
            blocks.append(ContentBlock(type=ContentBlockType.BULLET_LIST, items=points))
    elif slide.layout_family is LayoutFamily.COMPARISON:
        values = points or [truncate(slide.takeaway, 180)]
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
        values = points or [truncate(slide.takeaway, 180)]
        blocks.extend(
            ContentBlock(type=block_type, label=f"{index:02d}", text=value, ordinal=index)
            for index, value in enumerate(values[:5], start=1)
        )
    elif slide.layout_family is LayoutFamily.THREE_POINTS:
        values = points or [truncate(slide.takeaway, 180)]
        blocks.extend(
            ContentBlock(type=ContentBlockType.ICON_LABEL, label=f"{index:02d}", text=value)
            for index, value in enumerate(values, start=1)
        )
    else:
        if len(points) > 1:
            blocks.append(ContentBlock(type=ContentBlockType.BULLET_LIST, items=points))
        else:
            blocks.append(
                ContentBlock(
                    type=ContentBlockType.PARAGRAPH,
                    text=points[0] if points else slide.takeaway,
                )
            )
    if not blocks:
        blocks.append(ContentBlock(type=ContentBlockType.CALLOUT, text=slide.takeaway))
    icons = icon_registry().search(" ".join([slide.assertion_title, slide.takeaway]), limit=3)
    return SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=blocks,
        emphasis=(points or [truncate(slide.takeaway, 180)])[:6],
        suggested_icon_keys=icons,
    )


def _neighbor_plans(plan: DeckPlan, index: int) -> dict[str, Any]:
    offset = index - 1
    return {
        "previous": plan.slides[offset - 1].model_dump(mode="json") if offset > 0 else None,
        "next": plan.slides[offset + 1].model_dump(mode="json") if offset + 1 < len(plan.slides) else None,
    }


def _relevant_sources(state: DeckGraphState, slide: SlidePlan, *, limit: int = 6) -> list[dict[str, Any]]:
    chunks = [item for item in state.get("source_chunks") or [] if isinstance(item, dict)]
    terms = _slide_relevance_terms(state, slide)
    ranked = sorted(
        (
            (_source_relevance_score(item, terms), position, item)
            for position, item in enumerate(chunks)
        ),
        key=lambda value: (-value[0], value[1]),
    )
    result = [
        {
            "source_file": item.get("source_file", ""),
            "page": item.get("page", 0),
            "section": item.get("section", ""),
            "excerpt": str(item.get("excerpt") or "")[:1_200],
        }
        for score, _position, item in ranked[:limit]
        if score > 0
    ]
    if result:
        return result
    source_text = str(state.get("source_text") or "").strip()
    if not source_text:
        return []
    return [
        {
            "source_file": "normalized-source",
            "page": 0,
            "section": "",
            "excerpt": source_text[:1_200],
        }
    ]


def _relevant_assets(state: DeckGraphState, slide: SlidePlan, *, limit: int = 12) -> list[dict[str, Any]]:
    """Expose only task-local source descriptors; never create remote asset URLs."""

    result: list[dict[str, Any]] = []
    for item in _relevant_sources(state, slide, limit=limit):
        source_file = str(item.get("source_file") or "").strip()
        section = str(item.get("section") or "").strip()
        if not source_file:
            continue
        result.append(
            {
                "source_file": source_file,
                "page": item.get("page", 0),
                "section": section,
                "trusted_local_only": True,
            }
        )
    terms = _slide_relevance_terms(state, slide)
    local_assets = [item for item in state.get("local_assets") or [] if isinstance(item, dict)]
    ranked_assets = sorted(
        (
            (
                sum(
                    1
                    for term in terms
                    if term in " ".join(
                        [
                            str(item.get("original_name") or ""),
                            str(item.get("asset_id") or ""),
                            str(item.get("kind") or ""),
                            str(item.get("relative_path") or ""),
                        ]
                    ).casefold()
                ),
                position,
                item,
            )
            for position, item in enumerate(local_assets)
        ),
        key=lambda value: (-value[0], value[1]),
    )
    for score, _position, item in ranked_assets:
        if score <= 0 or len(result) >= limit:
            continue
        result.append(
            {
                key: item.get(key)
                for key in (
                    "asset_id",
                    "kind",
                    "original_name",
                    "mime_type",
                    "relative_path",
                    "size_bytes",
                    "sha256",
                    "trusted_local_only",
                    "visual_analysis_available",
                    "html_embedding_allowed",
                )
            }
        )
    return result


_RELEVANCE_STOPWORDS = {
    "about",
    "and",
    "are",
    "based",
    "for",
    "from",
    "ground",
    "in",
    "into",
    "of",
    "on",
    "page",
    "section",
    "show",
    "slide",
    "source",
    "that",
    "the",
    "this",
    "to",
    "use",
    "with",
}


def _slide_relevance_terms(state: DeckGraphState, slide: SlidePlan) -> set[str]:
    outline = state.get("current_slide_outline") or {}
    if not isinstance(outline, dict):
        outline = {}
    text = " ".join(
        [
            slide.assertion_title,
            slide.takeaway,
            slide.content_intent,
            str(outline.get("content_note") or ""),
            " ".join(str(item) for item in outline.get("key_points") or []),
        ]
    )
    return {
        normalized
        for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,}|[\u3400-\u9fff]{2,4}", text)
        if (normalized := token.casefold()) not in _RELEVANCE_STOPWORDS
    }


def _source_relevance_score(item: dict[str, Any], terms: set[str]) -> int:
    excerpt = str(item.get("excerpt") or "").casefold()
    locator = " ".join(
        [
            str(item.get("source_file") or ""),
            str(item.get("section") or ""),
            str(item.get("page") or ""),
        ]
    ).casefold()
    return sum((3 if term in locator else 0) + (1 if term in excerpt else 0) for term in terms)


def _write_raw_debug(
    output_dir: Path,
    state: DeckGraphState,
    spec: HtmlSlideSpec,
    *,
    attempt: int,
) -> None:
    slide = SlidePlan.model_validate(state.get("current_slide_plan") or {})
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"])).ensure()
    path = project.raw_model_output(slide.index, attempt)
    _atomic_write_json(path, spec.model_dump(mode="json"))
    # The private debug artifact stays outside the public task root and is
    # never emitted as a ResourceRef or copied into graph state.


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
    total_slides: int | None = None,
    approved_context: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Reject visually plausible HTML that changes or drops page semantics.

    Browser geometry cannot determine whether the model preserved the approved
    title, content, or evidence, so model-authored HTML must pass this semantic
    check before it can be captured.
    """

    parser = html5lib.HTMLParser(namespaceHTMLElements=False)
    root = parser.parseFragment(str(body_html or ""))
    rendered_nodes = [str(item).strip() for item in root.itertext() if str(item).strip()]
    rendered = " ".join(rendered_nodes)
    rendered_canonical = _canonical_visible_text(rendered)
    diagnostics: list[dict[str, Any]] = []

    body_candidates: list[str] = [content.takeaway, *content.emphasis]
    for block in content.content_blocks:
        body_candidates.extend([block.text, *block.items, block.label, block.value])

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
    if meaningful_candidates and not any(
        _approved_visible_text_present(value, rendered_canonical)
        for value in meaningful_candidates
    ):
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
                    "An approved metric value is absent from the rendered HTML.",
                    "Restore the exact approved metric value.",
                    {"value": block.value},
                )
            )

    approved_number_text = " ".join(
        [
            content.assertion_title,
            content.takeaway,
            *content.emphasis,
            *(approved_context or []),
            *[
                value
                for block in content.content_blocks
                for value in (block.text, *block.items, block.label, block.value)
                if value
            ],
        ]
    )
    approved_numbers = {
        _canonical_number_token(value)
        for value in extract_number_tokens(approved_number_text)
    }
    approved_numbers.update(
        {
            _canonical_number_token(str(slide.index)),
            _canonical_number_token(f"{slide.index:02d}"),
        }
    )
    if total_slides is not None and total_slides > 0:
        approved_numbers.update(
            {
                _canonical_number_token(str(total_slides)),
                _canonical_number_token(f"{total_slides:02d}"),
            }
        )
    unapproved_numbers = sorted(
        {
            value
            for token in extract_number_tokens(rendered)
            if (value := _canonical_number_token(token)) not in approved_numbers
        }
    )
    if unapproved_numbers:
        diagnostics.append(
            _content_diagnostic(
                "UNAPPROVED_NUMBER",
                "Rendered HTML introduces numeric evidence outside the approved page context.",
                "Remove or replace numbers that are not present in the outline, content contract, or relevant sources.",
                {"values": unapproved_numbers},
            )
        )
    return diagnostics


def _canonical_visible_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", normalized)


def _approved_visible_text_present(candidate: str, rendered_canonical: str) -> bool:
    """Match faithful copy edits while numeric guards remain exact.

    The HTML model often makes harmless grammatical edits such as adding
    ``用于`` or changing ``得到`` to ``得``.  Requiring the whole approved
    sentence as one substring falsely rejected those pages.  Long body copy is
    therefore accepted when most of its distinctive word/CJK-bigram tokens are
    present.  Metric values and every rendered numeric claim are still checked
    separately and exactly below.
    """

    approved = _canonical_visible_text(candidate)
    if not approved:
        return False
    if approved in rendered_canonical:
        return True
    tokens = _visible_text_match_tokens(approved)
    if len(tokens) < 3:
        return False
    rendered_tokens = _visible_text_match_tokens(rendered_canonical)
    matched = sum(token in rendered_tokens for token in tokens)
    return matched >= 3 and matched / len(tokens) >= 0.6


def _visible_text_match_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"[\u3400-\u9fff]+|[a-z0-9]+(?:[_./%+-][a-z0-9]+)*", value):
        token = match.group(0)
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            if len(token) == 1:
                tokens.add(token)
            else:
                tokens.update(token[index : index + 2] for index in range(len(token) - 1))
        elif len(token) >= 2 or token.isdigit():
            tokens.add(token)
    return tokens


def _canonical_number_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).replace(",", "")
    suffix = "%" if normalized.endswith("%") else ""
    number = normalized[:-1] if suffix else normalized
    if re.fullmatch(r"\d+", number):
        number = str(int(number))
    return number + suffix


def _content_diagnostic(
    code: str,
    message: str,
    suggestion: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return RendererDiagnostic(
        code=code,
        category=DiagnosticCategory.CONTENT,
        # Copy fidelity and numeric-evidence matching are quality signals, not
        # renderer safety boundaries.  Keep them in diagnostics and reports,
        # while prompts remain responsible for correcting them without making
        # an otherwise renderable deck fail as a whole.
        severity=DiagnosticSeverity.WARNING,
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


def _has_error_diagnostic(diagnostics: list[dict[str, Any]]) -> bool:
    return any(str(item.get("severity") or "").casefold() == "error" for item in diagnostics)


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


def _speculative_preview_png(project: DeckProjectPaths, page: int) -> Path:
    return project.previews / f".page-{int(page):03d}.speculative.png"


def _resolve_project_artifact(project: DeckProjectPaths, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise BrowserEnvironmentError("speculative PNG path is invalid")
    resolved = (project.root / relative_path).resolve()
    try:
        resolved.relative_to(project.root)
    except ValueError as exc:
        raise BrowserEnvironmentError("speculative PNG path escaped the project directory") from exc
    return resolved


def _discard_speculative_preview(state: DeckGraphState, output_dir: Path) -> None:
    relative_path = str(state.get("current_speculative_preview_path") or "").strip()
    if not relative_path:
        return
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"]))
    _resolve_project_artifact(project, relative_path).unlink(missing_ok=True)


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
    "SlideProgressTracker",
    "aggregate_slides",
    "capture_png",
    "emit_slide_preview",
    "generate_slide_design",
    "generate_slide_html",
    "persist_slide_artifacts",
    "prepare_slide",
    "render_html_and_measure_dom",
    "repair_slide_html",
    "sanitize_and_assemble_html",
    "validate_dom",
]
