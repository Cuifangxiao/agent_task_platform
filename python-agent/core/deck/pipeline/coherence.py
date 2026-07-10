"""Cross-slide narrative review with constrained, auditable repairs."""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.llm import invoke_json_with_retries, llm_for_node, load_node_prompt
from core.deck.runtime_metrics import record_fallback
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import dedupe_issue_dicts, issues_to_dicts
from core.deck.rendering.browser import BrowserRenderer
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import DeckSpec, SlideSpec, slide_text_values


def review_coherence(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    html_renderer: HTMLSlideRenderer | None = None,
    browser: BrowserRenderer | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("coherence", "Checking and repairing cross-slide coherence"))
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    if not deck.slides:
        return {"coherence_reviewed": True}

    original = copy.deepcopy(deck)
    model_failed = False
    try:
        coherence_llm = llm_for_node(llm_registry, "coherence")
        model_result = invoke_json_with_retries(
            coherence_llm,
            system=load_node_prompt("coherence"),
            user=json.dumps({"deck": deck.to_dict()}, ensure_ascii=False),
            attempts=2,
        )
    except ValueError as exc:
        model_failed = True
        record_fallback()
        model_result = {"issues": [{"kind": "coherence_degraded", "description": str(exc), "page": 0}]}

    issues = [
        issue
        for issue in state.get("validation_issues") or []
        if str(issue.get("kind") or "") not in {"duplicate_title", "cross_slide_duplicate", "page_order"}
    ]
    changed_pages: set[int] = set()

    global_replacements = model_result.get("term_replacements") if isinstance(model_result.get("term_replacements"), dict) else {}
    if global_replacements:
        for slide in deck.slides:
            if _apply_replacements(slide, global_replacements):
                changed_pages.add(slide.index)

    updates = model_result.get("slide_updates") if isinstance(model_result.get("slide_updates"), dict) else {}
    title_updates = model_result.get("title_updates") if isinstance(model_result.get("title_updates"), dict) else {}
    for slide in deck.slides:
        raw_update = updates.get(str(slide.index), updates.get(slide.index, {}))
        update = raw_update if isinstance(raw_update, dict) else {}
        candidate_title = str(update.get("title") or title_updates.get(str(slide.index)) or title_updates.get(slide.index) or "").strip()
        if candidate_title and candidate_title != slide.title and _safe_text_change(slide.title, candidate_title):
            slide.title = candidate_title
            changed_pages.add(slide.index)
            writer(events.stage_progress("coherence", f"Updated slide {slide.index} title for narrative coherence", {"index": slide.index, "status": "done", "reason": "title_update"}))
        replacements = update.get("replacements") if isinstance(update.get("replacements"), dict) else {}
        if replacements and _apply_replacements(slide, replacements):
            changed_pages.add(slide.index)
            writer(events.stage_progress("coherence", f"Applied source-preserving coherence replacements on slide {slide.index}", {"index": slide.index, "status": "done", "reason": "content_replacement"}))
        notes_append = str(update.get("notes_append") or "").strip()
        if notes_append and _safe_generated_text(notes_append):
            slide.notes = (slide.notes.rstrip() + "\n\n" + notes_append).strip()
            changed_pages.add(slide.index)

    order_changed, order_issue = _apply_page_order(deck, model_result.get("slide_order"))
    if order_changed:
        changed_pages.update(slide.index for slide in deck.slides)
        writer(events.stage_progress("coherence", "Applied a complete slide-order repair and renumbered every page", {"status": "done", "reason": "page_order"}))
    if order_issue:
        issues.append(order_issue)

    seen_titles: Counter[str] = Counter()
    for slide in deck.slides:
        title_key = _normalize_text(slide.title)
        seen_titles[title_key] += 1
        if seen_titles[title_key] > 1:
            slide.title = f"{slide.title} · {seen_titles[title_key]}"
            changed_pages.add(slide.index)
            writer(events.stage_progress("coherence", f"Disambiguated repeated title on slide {slide.index}", {"index": slide.index, "status": "done"}))

    seen_content: dict[str, int] = {}
    for slide in deck.slides:
        for text in slide_text_values(slide):
            normalized = _normalize_text(text)
            if len(normalized) < 24:
                continue
            previous = seen_content.get(normalized)
            if previous and previous != slide.index:
                issues.append(_issue(slide.index, "cross_slide_duplicate", f"Content still repeats slide {previous}."))
            else:
                seen_content[normalized] = slide.index

    for raw in model_result.get("issues") or []:
        if not isinstance(raw, dict) or raw.get("resolved") is True:
            continue
        issues.append(
            _issue(
                int(raw.get("page") or 0),
                str(raw.get("kind") or "coherence"),
                str(raw.get("description") or "Coherence review issue remains unresolved."),
            )
        )

    artifacts = list(state.get("artifacts") or [])
    render_issues: list[dict[str, Any]] = []
    if html_renderer is not None and browser is not None:
        versions = {
            int(item.get("index") or 0): int(item.get("version") or 1)
            for item in artifacts
            if isinstance(item, dict)
        }
        for slide in deck.slides:
            before = next((item for item in original.slides if item.index == slide.index), None)
            if before is not None and before.to_dict() != slide.to_dict():
                changed_pages.add(slide.index)
            if slide.index not in changed_pages:
                continue
            version = max(2, versions.get(slide.index, 1) + 1)
            artifact = browser.render_slide(
                str(state.get("request_id") or ""),
                slide,
                html_renderer.render(deck, slide),
                version=version,
                theme=deck.theme,
            )
            render_issues.extend(issues_to_dicts(artifact.issues))
            payload = {
                "index": slide.index,
                "title": slide.title,
                "png": artifact.png_rel,
                "html": artifact.html_rel,
                "version": artifact.version,
                "status": "warning" if artifact.issues else "ready",
                "reason": "coherence_repair",
            }
            artifacts = [item for item in artifacts if int(item.get("index") or 0) != slide.index]
            artifacts.append(payload)
            writer(events.slide_preview(f"Slide {slide.index} updated by coherence review", payload))

    if model_failed:
        writer(events.stage_progress("coherence", "Coherence model failed; unresolved quality risk was retained.", {"status": "warning"}))
    issues = dedupe_issue_dicts(issues + render_issues)
    writer(events.validation_report(issues, stage="coherence"))
    return {
        "deck_spec": deck.to_dict(),
        "slides": [slide.to_dict() for slide in deck.slides],
        "final_slides": [slide.to_dict() for slide in deck.slides],
        "artifacts": artifacts,
        "validation_issues": issues,
        "coherence_reviewed": True,
    }


def _apply_page_order(deck: DeckSpec, raw_order: Any) -> tuple[bool, dict[str, Any] | None]:
    existing = [slide.index for slide in deck.slides]
    requested: list[int] = []
    if isinstance(raw_order, list):
        try:
            requested = [int(item) for item in raw_order]
        except (TypeError, ValueError):
            requested = []
        if sorted(requested) != sorted(existing) or len(requested) != len(existing):
            return False, _issue(0, "page_order", "The proposed slide order was not a complete permutation and was not applied.")
    elif existing != list(range(1, len(existing) + 1)):
        requested = sorted(existing)
    if not requested or requested == existing:
        for index, slide in enumerate(deck.slides, start=1):
            slide.index = index
        return False, None
    by_index = {slide.index: slide for slide in deck.slides}
    deck.slides = [by_index[index] for index in requested]
    for index, slide in enumerate(deck.slides, start=1):
        slide.index = index
    return True, None


def _apply_replacements(slide: SlideSpec, replacements: dict[Any, Any]) -> bool:
    safe = {
        str(old): str(new)
        for old, new in replacements.items()
        if str(old) and str(old) != str(new) and _safe_text_change(str(old), str(new))
    }
    if not safe:
        return False
    changed = False

    def replace(value: Any) -> Any:
        nonlocal changed
        if isinstance(value, str):
            result = value
            for old, new in safe.items():
                result = result.replace(old, new)
            changed = changed or result != value
            return result
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    slide.title = replace(slide.title)
    slide.content = replace(slide.content)
    slide.notes = replace(slide.notes)
    return changed


def _safe_text_change(before: str, after: str) -> bool:
    return _numeric_tokens(before) == _numeric_tokens(after) and _safe_generated_text(after)


def _safe_generated_text(value: str) -> bool:
    lowered = value.casefold()
    return "<script" not in lowered and "http://" not in lowered and "https://" not in lowered


def _numeric_tokens(value: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?%?", value or "")


def _normalize_text(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _issue(page: int, kind: str, description: str) -> dict[str, Any]:
    return {
        "page": page,
        "severity": "must_fix",
        "kind": kind,
        "description": description,
        "suggestion": "Apply a source-preserving coherence update or review this deck before sharing.",
    }
