"""Slide writer loop, HTML rendering, and preview artifact events."""

from __future__ import annotations

import logging
import time
import copy
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.helpers import clamp_slide_count, component_from_layout, fallback_slide_title
from core.deck.pipeline.llm import compose_context_prompt, invoke_json_with_retries, llm_for_node, load_node_prompt
from core.deck.runtime_metrics import begin_node_metrics, end_node_metrics, record_fallback
from core.deck.pipeline.outline import parse_outline_slides
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import issues_to_dicts, validate_slide_spec
from core.deck.rendering.browser import BrowserRenderer
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import COMPONENTS, DeckSpec, SlideSpec, ThemeTokens, _normalize_design, default_theme, normalize_slide_spec

logger = logging.getLogger(__name__)


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
    designer_llm = llm_for_node(llm_registry, "designer", aliases=("slides", "writer"))
    writer(events.stage_started("slides", f"Generating {total} HTML slide previews", {"total": total}))
    logger.info("deck slides start: request_id=%s total=%s", request_id, total)

    slides: list[SlideSpec] = []
    raw_slides: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    for index, outline in enumerate(plan_slides, start=1):
        title = str(outline.get("title") or fallback_slide_title(index, state)).strip()
        legacy_payload = legacy_slide_payload(
            {
                "index": index,
                "title": title,
                "key_message": str(outline.get("key_message") or "").strip(),
                "component": str(outline.get("component") or ""),
                "role": str(outline.get("role") or ""),
            },
            status="running",
        )
        writer(events.slide_started(f"Generating slide {index}/{total}: {title}", legacy_payload))

    # Explicit per-node registries are production clients; the single `default`
    # test/fallback client may not be thread-safe.
    max_workers = min(4, max(1, total)) if "writer" in llm_registry else 1
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="deck-writer") as executor:
        futures = [
            executor.submit(
                copy_context().run,
                _generate_slide_spec, state, index, outline, plan_slides, total, deck, slide_llm, request_id,
            )
            for index, outline in enumerate(plan_slides, start=1)
        ]
        generated = [future.result() for future in futures]

    for raw_slide, slide, recovery_warning in generated:
        raw_slides.append(raw_slide)
        designer_started_at = time.perf_counter()
        designer_metrics, designer_token = begin_node_metrics()
        try:
            slide = apply_designer_direction(designer_llm, slide, plan_slides[slide.index - 1], theme)
        finally:
            end_node_metrics(designer_token)
        metadata = llm_registry.get("__metadata__") if isinstance(llm_registry.get("__metadata__"), dict) else {}
        designer_model = metadata.get("designer") or {}
        writer(
            events.stage_progress(
                "designer",
                f"Designer selected slide {slide.index} component and variant",
                {
                    "index": slide.index,
                    "metrics": {
                        "duration_ms": round((time.perf_counter() - designer_started_at) * 1000),
                        "provider": designer_model.get("provider", ""),
                        "model": designer_model.get("model", ""),
                        **designer_metrics.snapshot(),
                        "repair_count": int(state.get("repair_round") or 0),
                        "vlm_issue_count": 0,
                        "export_degraded": False,
                    },
                },
            )
        )
        if recovery_warning:
            writer(
                events.stage_progress(
                    "writer",
                    f"Slide {slide.index} writer output required safe recovery: {recovery_warning}",
                    {"index": slide.index, "status": "warning", "warning": recovery_warning},
                )
            )
        slide.layout_variant = "split" if any(issue.kind == "density" for issue in validate_slide_spec(slide)) else slide.layout_variant
        parts = [slide]
        if any(issue.kind == "density" for issue in validate_slide_spec(slide)):
            parts = split_slide_preserving_content(slide) or [slide]
            if len(parts) > 1:
                writer(events.stage_progress("designer", f"Slide {slide.index} was split into {len(parts)} pages to preserve all content.", {"status": "warning", "reason": "density_split"}))

        for part in parts:
            part.index = len(slides) + 1
            spec_issues = validate_slide_spec(part)
            all_issues.extend(issues_to_dicts(spec_issues))
            slides.append(part)
            deck.slides = slides
            writer(
                events.slide_spec_ready(
                    f"Slide {part.index} spec ready: {part.title}",
                    {"index": part.index, "title": part.title, "component": part.component, "role": part.role},
                )
            )

            html = html_renderer.render(deck, part)
            render_started_at = time.perf_counter()
            logger.info("deck slide render start: request_id=%s index=%s", request_id, part.index)
            artifact = browser.render_slide(request_id, part, html, theme=theme)
            logger.info(
                "deck slide render done: request_id=%s index=%s renderer=%s issues=%s elapsed=%.2fs",
                request_id,
                part.index,
                artifact.renderer,
                len(artifact.issues),
                time.perf_counter() - render_started_at,
            )
            artifact_payload = {
                "index": part.index,
                "png": artifact.png_rel,
                "html": artifact.html_rel,
                "version": artifact.version,
                "status": "ready" if not (artifact.issues or spec_issues) else "warning",
                "renderer": artifact.renderer,
            }
            artifacts.append(artifact_payload)
            writer(events.slide_preview(f"Slide {part.index} preview ready", artifact_payload))
            writer(events.slide_ready(f"Slide {part.index} ready: {part.title}", legacy_slide_payload(part.to_dict(), status=artifact_payload["status"])))
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
) -> tuple[dict[str, Any], SlideSpec, str]:
    started_at = time.perf_counter()
    raw_slide: dict[str, Any] = {}
    warning = ""
    try:
        raw_slide = invoke_json_with_retries(
            slide_llm,
            system=load_node_prompt("writer"),
            user=slide_prompt(state, outline, [], total, neighbor_outlines=_neighbor_outlines(plan_slides, index)),
            attempts=3,
        )
    except ValueError as exc:
        warning = str(exc)
        record_fallback()
    slide = normalize_slide_spec(raw_slide, index=index, outline=outline, deck_title=deck.title, narrative=deck.narrative)
    slide = repair_placeholder_slide(slide, outline, deck)
    logger.info(
        "deck slide writer/designer done: request_id=%s index=%s component=%s variant=%s elapsed=%.2fs",
        request_id,
        index,
        slide.component,
        slide.layout_variant,
        time.perf_counter() - started_at,
    )
    return raw_slide, slide, warning


def _neighbor_outlines(plan_slides: list[dict[str, Any]], index: int) -> dict[str, Any]:
    offset = index - 1
    return {
        "previous": plan_slides[offset - 1] if offset > 0 else None,
        "next": plan_slides[offset + 1] if offset + 1 < len(plan_slides) else None,
    }


def apply_designer_direction(designer_llm: Any, slide: SlideSpec, outline: dict[str, Any], theme: ThemeTokens) -> SlideSpec:
    """Invoke Designer and apply only catalogued components/tokens."""

    try:
        direction = invoke_json_with_retries(
            designer_llm,
            system=load_node_prompt("designer"),
            user=compose_context_prompt(
                {
                    "slide": slide.to_dict(),
                    "outline": outline,
                    "theme": theme.to_dict(),
                    "allowed_components": sorted(COMPONENTS),
                    "allowed_layout_variants": ["balanced", "editorial", "split"],
                },
                uploaded_file_text="",
            ),
            attempts=2,
        )
    except ValueError:
        direction = {}
        record_fallback()
    component = str(direction.get("component") or "").strip().lower().replace("_", "-")
    if component in COMPONENTS and _component_accepts_content(component, slide.content, slide.chart):
        slide.component = component
    variant = str(direction.get("layout_variant") or "").strip().lower()
    if variant in {"balanced", "editorial", "split"}:
        slide.layout_variant = variant
    raw_design = direction.get("design") if isinstance(direction.get("design"), dict) else direction
    slide.design = _normalize_design(raw_design)
    return slide


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
    return any(content.get(key) is not None for key in required) if required else True


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
    terms = {term for term in query.replace("，", " ").replace("。", " ").split() if len(term) >= 2}
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
    terms = {term for term in query.replace("，", " ").replace("。", " ").split() if len(term) >= 2}
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


def repair_placeholder_slide(slide: SlideSpec, outline: dict[str, Any], deck: DeckSpec) -> SlideSpec:
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


def legacy_slide_payload(slide: dict[str, Any], *, status: str) -> dict[str, Any]:
    content = slide.get("content") if isinstance(slide.get("content"), dict) else {}
    bullets = content.get("bullets") or content.get("items") or content.get("steps") or []
    if not isinstance(bullets, list):
        bullets = []
    return {
        "index": slide.get("index"),
        "title": slide.get("title", ""),
        "key_message": content.get("body") or content.get("subtitle") or content.get("takeaway") or slide.get("key_message") or "",
        "layout": slide.get("component") or slide.get("layout") or "content-basic",
        "component": slide.get("component") or "",
        "role": slide.get("role") or "",
        "bullets": [str(item) for item in bullets],
        "visual_suggestion": "",
        "speaker_notes": slide.get("notes", ""),
        "status": status,
    }
