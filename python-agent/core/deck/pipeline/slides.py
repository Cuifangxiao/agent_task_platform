"""Slide writer loop, HTML rendering, and preview artifact events."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.helpers import clamp_slide_count, component_from_layout, fallback_slide_title
from core.deck.pipeline.llm import compose_context_prompt, invoke_json_with_retries, llm_for_node, load_node_prompt
from core.deck.pipeline.outline import parse_outline_slides
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import issues_to_dicts, validate_slide_spec
from core.deck.rendering.browser import BrowserRenderer
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import DeckSpec, SlideSpec, ThemeTokens, default_theme, normalize_slide_spec


def write_and_design_slides(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
) -> dict[str, Any]:
    writer = get_stream_writer()
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
    writer(events.stage_started("slides", f"Generating {total} HTML slide previews", {"total": total}))

    slides: list[SlideSpec] = []
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

        raw_slide: dict[str, Any] = {}
        try:
            raw_slide = invoke_json_with_retries(
                slide_llm,
                system=load_node_prompt("writer"),
                user=slide_prompt(state, outline, slides, total),
                attempts=3,
            )
        except ValueError as exc:
            writer(
                events.stage_progress(
                    "slides",
                    f"Slide {index} writer output needed deterministic recovery: {exc}",
                    {"index": index, "status": "warning", "warning": str(exc)},
                )
            )

        slide = normalize_slide_spec(raw_slide, index=index, outline=outline, deck_title=deck.title, narrative=deck.narrative)
        slide = repair_placeholder_slide(slide, outline, deck)
        spec_issues = validate_slide_spec(slide)
        if spec_issues:
            all_issues.extend(issues_to_dicts(spec_issues))

        slides.append(slide)
        deck.slides = slides
        writer(
            events.slide_spec_ready(
                f"Slide {slide.index} spec ready: {slide.title}",
                {"index": slide.index, "title": slide.title, "component": slide.component, "role": slide.role},
            )
        )

        html = html_renderer.render(deck, slide)
        artifact = browser.render_slide(str(state["request_id"]), slide, html, theme=theme)
        artifact_payload = {
            "index": slide.index,
            "png": artifact.png_rel,
            "html": artifact.html_rel,
            "thumb_b64": artifact.thumb_b64,
            "version": artifact.version,
            "status": "ready" if not (artifact.issues or spec_issues) else "warning",
            "renderer": artifact.renderer,
        }
        artifacts.append(artifact_payload)
        writer(events.slide_preview(f"Slide {slide.index} preview ready", artifact_payload))
        writer(events.slide_ready(f"Slide {slide.index} ready: {slide.title}", legacy_slide_payload(slide.to_dict(), status=artifact_payload["status"])))
        if artifact.issues:
            all_issues.extend(issues_to_dicts(artifact.issues))

    deck.slides = slides
    return {
        "slides": [slide.to_dict() for slide in slides],
        "deck_spec": deck.to_dict(),
        "artifacts": artifacts,
        "validation_issues": all_issues,
    }


def slide_prompt(
    state: DeckGraphState,
    current_slide_outline: dict[str, Any],
    previous_slides: list[SlideSpec],
    total_slides: int,
) -> str:
    return compose_context_prompt(
        {
            "deck_title": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "brief": state.get("brief") or {},
            "outline_markdown": state.get("outline_markdown", ""),
            "total_slides": total_slides,
            "current_slide_outline": current_slide_outline,
            "previous_slides": [slide.to_dict() for slide in previous_slides[-2:]],
        },
        uploaded_file_text=state.get("uploaded_file_text", ""),
    )


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
        slide.content = {"body": key_message, "bullets": [key_message] if key_message else []}
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
