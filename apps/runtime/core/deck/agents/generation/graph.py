"""Generation graph: write, render, and validate all slides."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
import re
import time
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

import core.deck.events as events
from core.deck.agents.design.graph import page_contract
from core.deck.helpers import clamp_slide_count, component_from_layout, fallback_slide_title
from core.deck.instrumentation import instrument_node, run_phase_graph
from core.deck.agents.planning.graph import parse_outline_slides
from core.deck.llm import (
    compose_context_prompt,
    invoke_json_with_retries,
    llm_for_node,
    llm_log_context,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.quality import issues_to_dicts, validate_slide_spec
from core.deck.rendering.artist import render_svg_artwork
from core.deck.rendering.browser import BrowserRenderer, BrowserSession
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.runtime_metrics import begin_node_metrics, end_node_metrics, record_fallback
from core.deck.spec import COMPONENTS, DeckSpec, SlideSpec, ThemeTokens, default_theme, normalize_slide_spec
from core.deck.state import DeckGraphState

logger = logging.getLogger(__name__)


class Generation:
    """Slide authoring and rendering phase."""

    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        browser_session: BrowserSession | None = None,
        *,
        html_renderer: HTMLSlideRenderer | None = None,
        browser_renderer: BrowserRenderer | None = None,
    ) -> None:
        self.llm_registry = normalize_llm_registry(llms)
        self.output_dir = Path(output_dir)
        self.html_renderer = html_renderer or HTMLSlideRenderer()
        self.browser_renderer = browser_renderer or BrowserRenderer(
            self.output_dir,
            browser_session,
        )
        self.graph = self.compile_graph()

    def write_slides(self, state: DeckGraphState) -> dict[str, Any]:
        return write_and_design_slides(
            state,
            self.llm_registry,
            self.html_renderer,
            self.browser_renderer,
        )

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        builder.add_node(
            "write_slides",
            instrument_node("writer", self.write_slides, self.llm_registry),
        )
        builder.add_edge(START, "write_slides")
        builder.add_edge("write_slides", END)
        return builder.compile()

    def run(self, state: DeckGraphState) -> dict[str, Any]:
        return run_phase_graph(self.graph, state)


# ─────────────────────────────── write-slides node ───────────────────────────


def write_and_design_slides(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
) -> dict[str, Any]:
    writer = get_stream_writer()
    request_id = str(state["request_id"])
    plan_slides = plan_slide_outlines(state)
    total = len(plan_slides)
    theme = ThemeTokens.from_dict(state.get("theme") or default_theme().to_dict())
    brief = state.get("brief") or {}
    deck = DeckSpec(
        title=str((state.get("plan") or {}).get("title") or state.get("deck_title") or "Untitled presentation").strip(),
        narrative=str((state.get("plan") or {}).get("narrative") or "").strip(),
        audience=str(brief.get("audience") or ""),
        goal=str(brief.get("goal") or ""),
        language=str(brief.get("language") or "zh"),
        theme=theme,
        slides=[],
    )
    slide_llm = llm_for_node(llm_registry, "writer", aliases=("slides",))
    # Full-page SVG authoring must be explicitly configured; the writer model
    # is responsible for content rather than page rendering.
    designer_llm = llm_registry.get("designer")
    writer(events.stage_started("slides", f"Generating {total} SVG slide previews", {"total": total}))
    logger.info("deck slides start: request_id=%s total=%s", request_id, total)

    slides: list[SlideSpec] = []
    raw_slides: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    # Execute each page end-to-end before starting the next one.  This keeps the
    # semantic and visual context aligned and mirrors the immutable page roster.
    previous_slides: list[SlideSpec] = []
    previous_visual_signatures: list[dict[str, Any]] = []
    for index, outline in enumerate(plan_slides, start=1):
        title = str(outline.get("title") or fallback_slide_title(index, state)).strip()
        writer(
            events.slide_started(
                f"Generating page {index}/{total}: {title}",
                {"index": index, "total": total, "title": title},
            )
        )
        raw_slide, slide, recovery_warning = _generate_slide_spec(
            state, index, outline, plan_slides, total, deck, slide_llm, request_id, previous_slides,
        )
        raw_slides.append(raw_slide)
        slide = apply_design_contract(slide, state)
        if recovery_warning:
            logger.warning(
                "event=ppt_slide_writer_recovered request_id=%s slide_index=%s warning=%s",
                request_id,
                slide.index,
                recovery_warning,
            )
        spec_issues = validate_slide_spec(slide)
        all_issues.extend(issues_to_dicts(spec_issues))
        slides.append(slide)
        previous_slides.append(slide)
        deck.slides = slides

        designer_started_at = time.perf_counter()
        designer_metrics, designer_token = begin_node_metrics()
        try:
            with llm_log_context(
                request_id=request_id,
                stage="designer",
                slide_index=slide.index,
                operation="svg_artist",
            ):
                artwork = render_svg_artwork(
                    llm=designer_llm,
                    state=state,
                    deck=deck,
                    slide=slide,
                    outline=outline,
                    previous_visual_signatures=previous_visual_signatures,
                )
        finally:
            end_node_metrics(designer_token)
        previous_visual_signatures.append(artwork.visual_signature)
        if artwork.issues:
            all_issues.extend(issues_to_dicts(artwork.issues))
        metadata = llm_registry.get("__metadata__") if isinstance(llm_registry.get("__metadata__"), dict) else {}
        designer_model = metadata.get("designer") or {}
        measured = designer_metrics.snapshot()
        logger.info(
            "event=ppt_slide_designer_end request_id=%s slide_index=%s duration_ms=%s provider=%s model=%s "
            "render_mode=%s template_id=%s layout_id=%s page_rhythm=%s rejected_issue_count=%s "
            "model_call_count=%s token_usage=%s retry_count=%s fallback_count=%s export_degraded=%s",
            request_id,
            slide.index,
            round((time.perf_counter() - designer_started_at) * 1000),
            designer_model.get("provider", ""),
            designer_model.get("model", ""),
            artwork.render_mode,
            artwork.template_id,
            artwork.layout_id,
            artwork.page_rhythm,
            len(artwork.rejected_issues),
            measured["model_call_count"],
            measured["token_usage"],
            measured["retry_count"],
            measured["fallback_count"],
            artwork.render_mode != "llm_svg",
        )
        html = html_renderer.render(deck, slide)
        render_started_at = time.perf_counter()
        logger.info("deck slide render start: request_id=%s index=%s", request_id, slide.index)
        artifact = browser.render_slide(request_id, slide, html, theme=theme, svg=artwork.svg)
        logger.info(
            "deck slide render done: request_id=%s index=%s renderer=%s issues=%s elapsed=%.2fs",
            request_id,
            slide.index,
            artifact.renderer,
            len(artifact.issues),
            time.perf_counter() - render_started_at,
        )
        artifact_payload = {
            "index": slide.index,
            "total": total,
            "title": slide.title,
            "png": artifact.png_rel,
            "html": artifact.html_rel,
            "svg": artifact.svg_rel,
            "status": "ready" if not (artifact.issues or spec_issues or artwork.issues) else "warning",
            "renderer": artifact.renderer,
            "svg_renderer": artwork.render_mode,
            "template_id": artwork.template_id,
            "layout_id": artwork.layout_id,
            "page_rhythm": artwork.page_rhythm,
            "visual_signature": artwork.visual_signature,
            "artist_rejected_issues": artwork.rejected_issues,
        }
        artifacts.append(artifact_payload)
        writer(events.slide_preview(f"Page {slide.index}/{total} preview ready: {slide.title}", artifact_payload))
        if artifact.issues:
            all_issues.extend(issues_to_dicts(artifact.issues))

    deck.slides = slides
    logger.info("deck slides done: request_id=%s total=%s validation_issues=%s", request_id, len(slides), len(all_issues))
    return {
        "slides": [slide.to_dict() for slide in slides],
        "raw_slides": raw_slides,
        "final_slides": [slide.to_dict() for slide in slides],
        "deck_spec": deck.to_dict(),
        "artifacts": artifacts,
        "validation_issues": all_issues,
    }


def _generate_slide_spec(
    state: DeckGraphState,
    index: int,
    outline: dict[str, Any],
    plan_slides: list[dict[str, Any]],
    total: int,
    deck: DeckSpec,
    slide_llm: Any,
    request_id: str,
    previous_slides: list[SlideSpec],
) -> tuple[dict[str, Any], SlideSpec, str]:
    started_at = time.perf_counter()
    raw_slide: dict[str, Any] = {}
    warning = ""
    logger.info(
        "event=ppt_slide_writer_start request_id=%s slide_index=%s total=%s previous_slides=%s",
        request_id,
        index,
        total,
        len(previous_slides),
    )
    try:
        with llm_log_context(
            request_id=request_id,
            stage="writer",
            slide_index=index,
            operation="slide_spec",
        ):
            raw_slide = invoke_json_with_retries(
                slide_llm,
                system=load_node_prompt("writer"),
                user=slide_prompt(state, outline, previous_slides, total, neighbor_outlines=_neighbor_outlines(plan_slides, index)),
                attempts=3,
            )
    except ValueError as exc:
        warning = str(exc)
        record_fallback()
    slide = normalize_slide_spec(raw_slide, index=index, outline=outline, deck_title=deck.title, narrative=deck.narrative)
    slide = normalize_placeholder_slide(slide, outline, deck)
    logger.info(
        "event=ppt_slide_writer_end request_id=%s slide_index=%s component=%s variant=%s duration_ms=%s recovered=%s",
        request_id,
        index,
        slide.component,
        slide.layout_variant,
        round((time.perf_counter() - started_at) * 1000),
        bool(warning),
    )
    return raw_slide, slide, warning


def _neighbor_outlines(plan_slides: list[dict[str, Any]], index: int) -> dict[str, Any]:
    offset = index - 1
    return {
        "previous": plan_slides[offset - 1] if offset > 0 else None,
        "next": plan_slides[offset + 1] if offset + 1 < len(plan_slides) else None,
    }


def apply_design_contract(slide: SlideSpec, state: DeckGraphState) -> SlideSpec:
    """Re-apply the frozen page contract after model-directed styling."""

    locked = page_contract(state, slide.index)
    component = str(locked.get("component") or "").strip()
    if component in COMPONENTS:
        if not _component_accepts_content(component, slide.content, slide.chart):
            slide.content = _adapt_content_for_component(slide, component)
        slide.component = component
    variant = str(locked.get("layout_variant") or "").strip()
    if variant in {"balanced", "editorial", "split"}:
        slide.layout_variant = variant
    contract = state.get("design_contract") if isinstance(state.get("design_contract"), dict) else {}
    slide.spec_hash = str(contract.get("spec_hash") or "").strip()
    return slide


def _adapt_content_for_component(slide: SlideSpec, component: str) -> dict[str, Any]:
    """Preserve generated facts when the writer misses the locked component schema."""

    semantic_content = {
        key: value
        for key, value in slide.content.items()
        if key not in {"eyebrow", "meta", "speaker", "left_title", "right_title"}
    }
    values = _content_strings(semantic_content)
    fallback = next(
        (
            str(slide.content.get(key) or "").strip()
            for key in ("body", "takeaway", "subtitle", "quote")
            if str(slide.content.get(key) or "").strip()
        ),
        values[0] if values else slide.title,
    )
    items = values or [fallback]
    if component == "cover":
        return {"eyebrow": str(slide.content.get("eyebrow") or "PRESENTATION"), "subtitle": "\n".join(items)}
    if component in {"section", "end"}:
        return {"subtitle": "\n".join(items)}
    if component in {"agenda", "timeline", "quadrant", "summary"}:
        return {"items": items}
    if component == "steps":
        return {"steps": items}
    if component == "kpi-cards":
        return {"cards": [{"value": "", "label": item, "caption": ""} for item in items]}
    if component == "comparison":
        midpoint = max(1, (len(items) + 1) // 2)
        return {
            "left_title": str(slide.content.get("left_title") or "Current"),
            "right_title": str(slide.content.get("right_title") or "Target"),
            "left": items[:midpoint],
            "right": items[midpoint:],
        }
    if component == "data-table":
        return {"columns": ["Item"], "rows": [[item] for item in items]}
    if component == "quote":
        return {"quote": "\n".join(items), "speaker": str(slide.content.get("speaker") or "")}
    if component == "chart-takeaway":
        return {"takeaway": "\n".join(items)}
    remaining = [item for item in items if item != fallback]
    return {"body": fallback, "bullets": remaining}


def _content_strings(value: Any) -> list[str]:
    collected: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, str):
            text = item.strip()
            if text and text not in collected:
                collected.append(text)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            text = str(item)
            if text not in collected:
                collected.append(text)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return collected


def _component_accepts_content(component: str, content: dict[str, Any], chart: dict[str, Any] | None) -> bool:
    requirements = {
        "cover": ("subtitle",),
        "section": ("subtitle",),
        "agenda": ("items",),
        "content-basic": ("body", "bullets"),
        "kpi-cards": ("cards",),
        "timeline": ("items",),
        "steps": ("steps", "items"),
        "comparison": ("left", "right"),
        "quadrant": ("items",),
        "data-table": ("rows",),
        "quote": ("quote",),
        "summary": ("items",),
        "end": ("subtitle",),
    }
    if component == "chart-takeaway":
        return bool(chart and chart.get("series"))
    required = requirements.get(component, ())
    if component in {"comparison"}:
        return all(content.get(key) for key in required)
    return any(bool(content.get(key)) for key in required) if required else True


def split_slide_preserving_content(slide: SlideSpec) -> list[SlideSpec] | None:
    """Split dense list/body content across two slides without slicing semantic text."""

    if slide.chart and isinstance(slide.chart.get("series"), list) and len(slide.chart["series"]) > 1:
        values = slide.chart["series"]
        midpoint = (len(values) + 1) // 2
        first, second = copy.deepcopy(slide), copy.deepcopy(slide)
        first.chart["series"] = values[:midpoint]
        second.chart["series"] = values[midpoint:]
        second.title = f"{slide.title}（续）"
        return [first, second]
    for key in ("bullets", "items", "steps", "rows", "cards"):
        values = slide.content.get(key)
        if isinstance(values, list) and len(values) > 1:
            midpoint = (len(values) + 1) // 2
            first, second = copy.deepcopy(slide), copy.deepcopy(slide)
            first.content[key] = values[:midpoint]
            second.content[key] = values[midpoint:]
            second.title = f"{slide.title}（续）"
            return [first, second]
    body_key = next((key for key in ("body", "subtitle", "takeaway", "quote") if len(str(slide.content.get(key) or "")) > 1), "")
    if body_key:
        body = str(slide.content[body_key])
        midpoint = len(body) // 2
        candidates = [body.rfind(mark, 0, midpoint + 1) for mark in ("。", ".", "；", ";", "，", ",", " ")]
        split_at = max(candidates)
        if split_at < max(1, midpoint // 2):
            split_at = midpoint
        else:
            split_at += 1
        first, second = copy.deepcopy(slide), copy.deepcopy(slide)
        first.content[body_key] = body[:split_at].rstrip()
        second.content[body_key] = body[split_at:].lstrip()
        second.title = f"{slide.title}（续）"
        return [first, second]
    return None


def slide_prompt(
    state: DeckGraphState,
    current_slide_outline: dict[str, Any],
    previous_slides: list[SlideSpec],
    total_slides: int,
    *,
    neighbor_outlines: dict[str, Any] | None = None,
) -> str:
    return compose_context_prompt(
        {
            "deck_title": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "brief": relevant_brief_context(state, current_slide_outline),
            "outline_markdown": state.get("outline_markdown", ""),
            "total_slides": total_slides,
            "current_slide_outline": current_slide_outline,
            "design_contract": {
                "schema": (state.get("design_contract") or {}).get("schema"),
                "spec_hash": (state.get("design_contract") or {}).get("spec_hash"),
                "page": page_contract(state, int(current_slide_outline.get("index") or 0)),
            },
            "previous_slides": [slide.to_dict() for slide in previous_slides[-2:]],
            "neighbor_outlines": neighbor_outlines or {},
        },
        uploaded_file_text=relevant_source_context(state, current_slide_outline),
    )


def relevant_source_context(state: DeckGraphState, outline: dict[str, Any], *, limit: int = 5) -> str:
    chunks = [item for item in state.get("source_chunks") or [] if isinstance(item, dict)]
    if not chunks:
        return ""
    query = " ".join(str(outline.get(key) or "") for key in ("title", "key_message", "role")).casefold()
    terms = _search_terms(query)
    scored = [
        (sum(1 for term in terms if term in str(chunk.get("excerpt") or "").casefold()), chunk)
        for chunk in chunks
    ]
    matched = [item for score, item in sorted(scored, key=lambda pair: pair[0], reverse=True) if score > 0]
    ranked = (matched or chunks)[:limit]
    return "\n\n".join(
        f"[{chunk.get('source_file')} p.{chunk.get('page')} {chunk.get('section')}]\n{chunk.get('excerpt')}"
        for chunk in ranked
    )


def relevant_brief_context(state: DeckGraphState, outline: dict[str, Any], *, fact_limit: int = 12) -> dict[str, Any]:
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else {}
    query = " ".join(str(outline.get(key) or "") for key in ("title", "key_message", "role")).casefold()
    terms = _search_terms(query)
    facts = [item for item in brief.get("facts") or [] if isinstance(item, dict)]
    scored = [
        (
            sum(
            1
            for term in terms
            if term in f"{fact.get('statement', '')} {fact.get('source_excerpt', '')}".casefold()
            ),
            fact,
        )
        for fact in facts
    ]
    matched = [item for score, item in sorted(scored, key=lambda pair: pair[0], reverse=True) if score > 0]
    ranked = (matched or facts)[:fact_limit]
    return {
        key: value
        for key, value in brief.items()
        if key != "facts"
    } | {"facts": ranked}


def _search_terms(value: str, *, limit: int = 64) -> set[str]:
    """Tokenize both whitespace languages and unsegmented CJK for source ranking."""

    terms: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9._/-]+|[\u3400-\u9fff]+", (value or "").casefold()):
        if len(token) < 2:
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", token) and len(token) > 4:
            terms.extend(token[index : index + size] for size in (2, 3, 4) for index in range(len(token) - size + 1))
        else:
            terms.append(token)
    return set(terms[:limit])


def plan_slide_outlines(state: DeckGraphState) -> list[dict[str, Any]]:
    raw_plan_slides = (state.get("plan") or {}).get("slides") or []
    parsed_slides = raw_plan_slides if isinstance(raw_plan_slides, list) else []
    if not parsed_slides:
        parsed_slides = parse_outline_slides(str(state.get("outline_markdown") or ""))
    page_count = target_slide_count(state, parsed_slides)
    outlines: list[dict[str, Any]] = []
    for index in range(page_count):
        source = parsed_slides[index] if index < len(parsed_slides) and isinstance(parsed_slides[index], dict) else {}
        outlines.append(
            {
                "index": index + 1,
                "title": str(source.get("title") or fallback_slide_title(index + 1, state)).strip(),
                "key_message": str(source.get("key_message") or "").strip(),
                "role": str(source.get("role") or ("hook" if index == 0 else "evidence")).strip(),
                "component": str(source.get("component") or component_from_layout(source.get("layout"), index)).strip(),
                "layout": str(source.get("layout") or ("title" if index == 0 else "content")).strip(),
                "page_rhythm": str(source.get("page_rhythm") or "").strip(),
                "layout_intent": str(source.get("layout_intent") or "").strip(),
                "visualization": source.get("visualization") or "none",
                "evidence": str(source.get("evidence") or "").strip(),
            }
        )
    return outlines


def target_slide_count(state: DeckGraphState, raw_slides: list[Any] | None = None) -> int:
    plan_slides = (state.get("plan") or {}).get("slides") or []
    if isinstance(plan_slides, list) and plan_slides:
        return clamp_slide_count(len(plan_slides))
    if raw_slides:
        return clamp_slide_count(len(raw_slides))
    parsed = parse_outline_slides(str(state.get("outline_markdown") or ""))
    if parsed:
        return clamp_slide_count(len(parsed))
    return clamp_slide_count((state.get("brief") or {}).get("suggested_pages") or 8)


def normalize_placeholder_slide(slide: SlideSpec, outline: dict[str, Any], deck: DeckSpec) -> SlideSpec:
    issues = validate_slide_spec(slide)
    if not any(issue.kind == "placeholder_text" for issue in issues):
        return slide
    key_message = str(outline.get("key_message") or deck.narrative or slide.title).strip()
    slide.title = str(outline.get("title") or slide.title).strip()
    if slide.component == "content-basic":
        slide.content = {"body": key_message, "bullets": []}
    elif slide.component in {"cover", "section", "end"}:
        slide.content["subtitle"] = key_message
    else:
        slide.content["items"] = [key_message or slide.title]
    return slide


__all__ = [
    "Generation",
    "apply_design_contract",
    "plan_slide_outlines",
    "relevant_source_context",
    "split_slide_preserving_content",
    "write_and_design_slides",
]
