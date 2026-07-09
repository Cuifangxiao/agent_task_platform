"""Optional visual review node."""

from __future__ import annotations

import logging
import time
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

logger = logging.getLogger(__name__)


def review_vlm(state: DeckGraphState, llm_registry: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    writer = get_stream_writer()
    request_id = str(state["request_id"])
    logger.info("deck vlm review start: request_id=%s slides=%s", request_id, len(state.get("slides") or []))
    try:
        review_llm = llm_for_node(llm_registry, "vlm_review", aliases=("review",))
    except ValueError:
        logger.info("deck vlm review skipped missing model: request_id=%s", request_id)
        writer(events.stage_progress("review_vlm", "未配置视觉审查模型，已跳过"))
        return {"vlm_reviewed": True}

    request_dir = output_dir / request_id
    slide_paths = [request_dir / "slides" / f"{slide.get('index')}.png" for slide in state.get("slides") or []]
    logger.info(
        "deck vlm review contact sheet start: request_id=%s existing_pngs=%s/%s",
        request_id,
        sum(1 for path in slide_paths if path.exists()),
        len(slide_paths),
    )
    contact_sheet = build_contact_sheet(slide_paths, request_dir / "review" / "contact_sheet.png")
    logger.info("deck vlm review llm start: request_id=%s contact_sheet=%s bytes=%s", request_id, contact_sheet, contact_sheet.stat().st_size if contact_sheet.exists() else 0)
    started_at = time.perf_counter()
    response = review_llm.invoke(
        [
            SystemMessage(content="You are a visual QA reviewer for presentation slides."),
            HumanMessage(content=review_message_content(contact_sheet)),
        ]
    )
    logger.info("deck vlm review llm done: request_id=%s elapsed=%.2fs", request_id, time.perf_counter() - started_at)
    vlm_issues = issues_to_dicts(parse_vlm_issues(response))
    current = list(state.get("validation_issues") or [])
    current.extend(vlm_issues)
    current = dedupe_issue_dicts(current)
    if _has_must_fix(vlm_issues) and int(state.get("repair_round") or 0) >= MAX_REPAIR_ROUNDS:
        writer(events.stage_progress("review_vlm", "视觉审查发现 must_fix 问题，但修复预算已用尽，已继续导出。", {"status": "warning"}))
    logger.info("deck vlm review done: request_id=%s vlm_issues=%s total_issues=%s must_fix=%s", request_id, len(vlm_issues), len(current), _has_must_fix(vlm_issues))
    writer(events.validation_report(current))
    return {"validation_issues": current, "vlm_reviewed": True}


def _has_must_fix(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("severity") == "must_fix" and int(issue.get("page") or 0) > 0 for issue in issues)
