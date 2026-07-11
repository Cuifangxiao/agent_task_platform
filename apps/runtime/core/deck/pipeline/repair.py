"""Repair loop for must-fix validation issues."""

from __future__ import annotations

import json
import logging
import re
import time
import copy
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.llm import invoke_json_with_retries, llm_for_node, load_node_prompt
from core.deck.pipeline.slides import legacy_slide_payload
from core.deck.runtime_metrics import record_fallback
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import issues_to_dicts, validate_slide_spec
from core.deck.rendering.browser import BrowserRenderer
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import DeckSpec, SlideSpec, normalize_slide_spec

logger = logging.getLogger(__name__)

MAX_REPAIR_ROUNDS = 2


def has_fixable_must_fix(state: DeckGraphState) -> bool:
    if int(state.get("repair_round") or 0) >= MAX_REPAIR_ROUNDS:
        return False
    return bool(_fixable_pages(state))


def repair_deck(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
) -> dict[str, Any]:
    writer = get_stream_writer()
    request_id = str(state["request_id"])
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    if not deck.slides:
        logger.info("deck repair skipped empty deck: request_id=%s", request_id)
        return {}

    round_index = int(state.get("repair_round") or 0) + 1
    issues = list(state.get("validation_issues") or [])
    pages = _fixable_pages(state)
    page_to_issues = {page: [issue for issue in issues if int(issue.get("page") or 0) == page] for page in pages}
    writer(events.repair_progress(f"Repairing {len(pages)} slide(s)", {"round": round_index, "pages": pages}))
    logger.info("deck repair start: request_id=%s round=%s pages=%s issues=%s", request_id, round_index, pages, len(issues))

    repaired_pages: set[int] = set()
    new_issues: list[dict[str, Any]] = []
    for slide in list(deck.slides):
        if slide.index not in pages:
            continue
        original = slide
        slide_started_at = time.perf_counter()
        logger.info("deck repair slide start: request_id=%s round=%s index=%s issue_count=%s", request_id, round_index, slide.index, len(page_to_issues[slide.index]))
        repaired = _rewrite_with_writer(llm_registry, deck, original, page_to_issues[slide.index])
        if repaired is None:
            candidate = copy.deepcopy(original)
            candidate.layout_variant = "split"
            if not validate_slide_spec(candidate):
                repaired = candidate
                writer(
                    events.stage_progress(
                        "repair",
                        f"Slide {slide.index} switched to the lower-density split variant without deleting content.",
                        {"index": slide.index, "status": "warning", "round": round_index, "reason": "designer_layout_fallback"},
                    )
                )
            else:
                writer(
                    events.stage_progress(
                        "repair",
                        f"Slide {slide.index} could not be repaired without risking content loss; original content was retained.",
                        {"index": slide.index, "status": "warning", "round": round_index, "reason": "rewrite_failed"},
                    )
                )
                continue
        deck.slides = [repaired if item.index == repaired.index else item for item in deck.slides]
        logger.info("deck repair slide render start: request_id=%s round=%s index=%s", request_id, round_index, repaired.index)
        artifact = browser.render_slide(request_id, repaired, html_renderer.render(deck, repaired), version=round_index + 1, theme=deck.theme)
        logger.info(
            "deck repair slide done: request_id=%s round=%s index=%s renderer=%s issues=%s elapsed=%.2fs",
            request_id,
            round_index,
            repaired.index,
            artifact.renderer,
            len(artifact.issues),
            time.perf_counter() - slide_started_at,
        )
        if artifact.issues:
            new_issues.extend(issues_to_dicts(artifact.issues))
        new_issues.extend(issues_to_dicts(validate_slide_spec(repaired)))
        repaired_pages.add(repaired.index)
        preview_status = "ready" if not artifact.issues else "warning"
        preview_payload = legacy_slide_payload(repaired.to_dict(), status=preview_status)
        preview_payload.update(
            {
                "png": artifact.png_rel,
                "html": artifact.html_rel,
                "version": artifact.version,
                "renderer": artifact.renderer,
            }
        )
        writer(events.slide_preview(f"Slide {repaired.index} preview repaired", preview_payload))

    retained_issues = [issue for issue in issues if int(issue.get("page") or 0) not in repaired_pages] + new_issues
    logger.info("deck repair done: request_id=%s round=%s repaired_pages=%s retained_issues=%s", request_id, round_index, sorted(repaired_pages), len(retained_issues))
    return {
        "deck_spec": deck.to_dict(),
        "slides": [slide.to_dict() for slide in deck.slides],
        "final_slides": [slide.to_dict() for slide in deck.slides],
        "validation_issues": retained_issues,
        "repair_round": round_index,
        "coherence_reviewed": False if repaired_pages else bool(state.get("coherence_reviewed")),
    }


def deterministic_downgrade(slide: SlideSpec) -> SlideSpec:
    """Legacy compatibility hook that intentionally preserves all semantic content."""
    return slide


def _rewrite_with_writer(
    llm_registry: dict[str, Any],
    deck: DeckSpec,
    slide: SlideSpec,
    issues: list[dict[str, Any]],
) -> SlideSpec | None:
    try:
        writer_llm = llm_for_node(llm_registry, "writer", aliases=("slides",))
        raw = invoke_json_with_retries(
            writer_llm,
            system=load_node_prompt("repair"),
            user=_repair_prompt(deck, slide, issues),
            attempts=2,
        )
        repaired = normalize_slide_spec(raw, index=slide.index, outline={"title": slide.title, "role": slide.role, "component": slide.component}, deck_title=deck.title, narrative=deck.narrative)
        return repaired if not validate_slide_spec(repaired) and _preserves_source_facts(slide, repaired) else None
    except Exception:  # pylint: disable=broad-exception-caught
        record_fallback()
        return None


def _repair_prompt(deck: DeckSpec, slide: SlideSpec, issues: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "task": "Repair this single SlideSpec v2 so it passes validation.",
            "deck": {"title": deck.title, "language": deck.language, "narrative": deck.narrative},
            "slide": slide.to_dict(),
            "issues": issues,
            "rules": [
                "Return one valid JSON object.",
                "Keep the same slide index.",
                "Reduce text density when overflow, overlap, or density issues are present.",
                "Preserve every source id, number, and semantic point; condense phrasing rather than deleting evidence.",
                "Do not introduce new facts or placeholders.",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _fixable_pages(state: DeckGraphState) -> list[int]:
    pages = {
        int(issue.get("page") or 0)
        for issue in state.get("validation_issues") or []
        if issue.get("severity") == "must_fix" and int(issue.get("page") or 0) > 0
    }
    return sorted(pages)


def _preserves_source_facts(original: SlideSpec, repaired: SlideSpec) -> bool:
    if not set(original.sources).issubset(set(repaired.sources)):
        return False
    original_numbers = set(re.findall(r"\d[\d,]*(?:\.\d+)?%?", json.dumps(original.to_dict(), ensure_ascii=False)))
    repaired_numbers = set(re.findall(r"\d[\d,]*(?:\.\d+)?%?", json.dumps(repaired.to_dict(), ensure_ascii=False)))
    return original_numbers.issubset(repaired_numbers)
