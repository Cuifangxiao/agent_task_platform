"""Repair loop for must-fix validation issues."""

from __future__ import annotations

import json
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.llm import invoke_json_with_retries, llm_for_node, load_node_prompt
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import issues_to_dicts, validate_slide_spec
from core.deck.rendering.browser import BrowserRenderer
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import DeckSpec, SlideSpec, _soft_limit, normalize_slide_spec

MAX_REPAIR_ROUNDS = 2
DOWNGRADE_BODY_CHARS = 90
DOWNGRADE_ITEM_CHARS = 44
DOWNGRADE_ITEM_COUNT = 2


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
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    if not deck.slides:
        return {}

    round_index = int(state.get("repair_round") or 0) + 1
    issues = list(state.get("validation_issues") or [])
    pages = _fixable_pages(state)
    page_to_issues = {page: [issue for issue in issues if int(issue.get("page") or 0) == page] for page in pages}
    writer(events.repair_progress(f"Repairing {len(pages)} slide(s)", {"round": round_index, "pages": pages}))

    repaired_pages: set[int] = set()
    new_issues: list[dict[str, Any]] = []
    for slide in list(deck.slides):
        if slide.index not in pages:
            continue
        original = slide
        repaired = _rewrite_with_writer(llm_registry, deck, original, page_to_issues[slide.index])
        if repaired is None:
            repaired = deterministic_downgrade(original)
            writer(
                events.stage_progress(
                    "repair",
                    f"Slide {slide.index} was simplified deterministically after validation.",
                    {"index": slide.index, "status": "warning", "round": round_index},
                )
            )
        deck.slides = [repaired if item.index == repaired.index else item for item in deck.slides]
        artifact = browser.render_slide(str(state["request_id"]), repaired, html_renderer.render(deck, repaired), version=round_index + 1, theme=deck.theme)
        if artifact.issues:
            new_issues.extend(issues_to_dicts(artifact.issues))
        repaired_pages.add(repaired.index)
        writer(
            events.slide_preview(
                f"Slide {repaired.index} preview repaired",
                {
                    "index": repaired.index,
                    "png": artifact.png_rel,
                    "html": artifact.html_rel,
                    "thumb_b64": artifact.thumb_b64,
                    "version": artifact.version,
                    "status": "ready" if not artifact.issues else "warning",
                    "renderer": artifact.renderer,
                },
            )
        )

    retained_issues = [issue for issue in issues if int(issue.get("page") or 0) not in repaired_pages] + new_issues
    return {
        "deck_spec": deck.to_dict(),
        "slides": [slide.to_dict() for slide in deck.slides],
        "validation_issues": retained_issues,
        "repair_round": round_index,
    }


def deterministic_downgrade(slide: SlideSpec) -> SlideSpec:
    content = dict(slide.content)
    if slide.component == "content-basic":
        content["body"] = _soft_limit(str(content.get("body") or ""), DOWNGRADE_BODY_CHARS)
        if isinstance(content.get("bullets"), list):
            content["bullets"] = [_soft_limit(str(item), DOWNGRADE_ITEM_CHARS) for item in content["bullets"][:DOWNGRADE_ITEM_COUNT]]
    else:
        items = []
        for key in ("bullets", "items", "steps"):
            if isinstance(content.get(key), list):
                items = [_soft_limit(str(item), DOWNGRADE_ITEM_CHARS) for item in content[key][:DOWNGRADE_ITEM_COUNT]]
                break
        body = _soft_limit(str(content.get("body") or content.get("takeaway") or content.get("subtitle") or slide.title), DOWNGRADE_BODY_CHARS)
        content = {"body": body, "bullets": items}
        slide.component = "content-basic"
    slide.title = _soft_limit(slide.title, DOWNGRADE_ITEM_CHARS)
    slide.content = content
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
        return repaired if not validate_slide_spec(repaired) else deterministic_downgrade(repaired)
    except Exception:  # pylint: disable=broad-exception-caught
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
