"""Optional visual review node."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer
from langchain_core.messages import HumanMessage, SystemMessage

import core.deck.events as events
from core.deck.pipeline.llm import llm_for_node
from core.deck.pipeline.repair import MAX_REPAIR_ROUNDS
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import dedupe_issue_dicts, issues_to_dicts
from core.deck.quality.vlm import build_contact_sheet, parse_vlm_issues, review_message_content


def review_vlm(state: DeckGraphState, llm_registry: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    writer = get_stream_writer()
    try:
        review_llm = llm_for_node(llm_registry, "review", aliases=("vlm_review",))
    except ValueError:
        writer(events.stage_progress("review_vlm", "未配置视觉审查模型，已跳过"))
        return {"vlm_reviewed": True}

    request_dir = output_dir / str(state["request_id"])
    slide_paths = [request_dir / "slides" / f"{slide.get('index')}.png" for slide in state.get("slides") or []]
    contact_sheet = build_contact_sheet(slide_paths, request_dir / "review" / "contact_sheet.png")
    response = review_llm.invoke(
        [
            SystemMessage(content="You are a visual QA reviewer for presentation slides."),
            HumanMessage(content=review_message_content(contact_sheet)),
        ]
    )
    vlm_issues = issues_to_dicts(parse_vlm_issues(response))
    current = list(state.get("validation_issues") or [])
    current.extend(vlm_issues)
    current = dedupe_issue_dicts(current)
    if _has_must_fix(vlm_issues) and int(state.get("repair_round") or 0) >= MAX_REPAIR_ROUNDS:
        writer(events.stage_progress("review_vlm", "视觉审查发现 must_fix 问题，但修复预算已用尽，已继续导出。", {"status": "warning"}))
    writer(events.validation_report(current))
    return {"validation_issues": current, "vlm_reviewed": True}


def _has_must_fix(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("severity") == "must_fix" and int(issue.get("page") or 0) > 0 for issue in issues)
