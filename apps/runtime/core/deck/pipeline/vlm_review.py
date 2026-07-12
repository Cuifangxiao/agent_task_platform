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
from core.deck.runtime_metrics import record_fallback, record_model_response
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
        message = "Visual review model is required but was not configured."
        record_fallback()
        logger.info("deck vlm review missing model: request_id=%s", request_id)
        writer(events.stage_progress("review_vlm", message, {"status": "error", "error_code": "vlm_model_missing"}))
        issue = {
            "page": 0,
            "severity": "must_fix",
            "kind": "vlm_model_missing",
            "description": message,
            "suggestion": "Bind an image-capable model to the review node.",
        }
        return {"validation_issues": dedupe_issue_dicts(list(state.get("validation_issues") or []) + [issue]), "vlm_reviewed": True}

    capabilities = llm_registry.get("__capabilities__") if isinstance(llm_registry.get("__capabilities__"), dict) else {}
    review_capability = capabilities.get("vlm_review") or capabilities.get("review") or {}
    if not bool(review_capability.get("supports_image_input")):
        message = "Configured visual review model does not declare image-input support."
        writer(events.stage_progress("review_vlm", message, {"status": "error", "error_code": "vlm_image_unsupported"}))
        issue = {
            "page": 0,
            "severity": "must_fix",
            "kind": "vlm_image_unsupported",
            "description": message,
            "suggestion": "Bind an image-capable model to the review node.",
        }
        return {"validation_issues": dedupe_issue_dicts(list(state.get("validation_issues") or []) + [issue]), "vlm_reviewed": True}

    request_dir = output_dir / request_id
    slide_paths = [request_dir / "slides" / f"{slide.get('index')}.png" for slide in state.get("slides") or []]
    logger.info(
        "deck vlm review contact sheet start: request_id=%s existing_pngs=%s/%s",
        request_id,
        sum(1 for path in slide_paths if path.exists()),
        len(slide_paths),
    )
    review_dir = request_dir / "review"
    contact_sheet = build_contact_sheet(slide_paths, review_dir / "contact_sheet.png")
    sheets = [contact_sheet]
    if len(slide_paths) > 8:
        sheets.extend(
            build_contact_sheet(
                slide_paths[offset : offset + 8],
                review_dir / f"batch_{offset // 8 + 1}.png",
                start_index=offset + 1,
            )
            for offset in range(0, len(slide_paths), 8)
        )
    logger.info("deck vlm review llm start: request_id=%s sheets=%s", request_id, len(sheets))
    started_at = time.perf_counter()
    vlm_issues: list[dict[str, Any]] = []
    try:
        for sheet in sheets:
            response = review_llm.invoke(
                [
                    SystemMessage(
                        content=(
                            "You are a senior presentation Art Director. Be visually exacting, "
                            "ground every issue in the supplied image, and return only the requested JSON."
                        )
                    ),
                    HumanMessage(
                        content=review_message_content(
                            sheet,
                            design_context=_design_review_context(state),
                        )
                    ),
                ]
            )
            record_model_response(response)
            vlm_issues.extend(issues_to_dicts(parse_vlm_issues(response)))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        record_fallback()
        message = f"Visual review failed and was not treated as passed: {exc}"
        writer(events.stage_progress("review_vlm", message, {"status": "error", "error_code": "vlm_review_failed"}))
        vlm_issues.append(
            {
                "page": 0,
                "severity": "must_fix",
                "kind": "vlm_review_failed",
                "description": message,
                "suggestion": "Retry with an available image-capable review model.",
            }
        )
    logger.info("deck vlm review llm done: request_id=%s elapsed=%.2fs", request_id, time.perf_counter() - started_at)
    current = list(state.get("validation_issues") or [])
    current.extend(vlm_issues)
    current = dedupe_issue_dicts(current)
    if _has_must_fix(vlm_issues) and int(state.get("repair_round") or 0) >= MAX_REPAIR_ROUNDS:
        writer(events.stage_progress("review_vlm", "视觉审查发现 must_fix 问题，但修复预算已用尽，已继续导出。", {"status": "warning"}))
    logger.info("deck vlm review done: request_id=%s vlm_issues=%s total_issues=%s must_fix=%s", request_id, len(vlm_issues), len(current), _has_must_fix(vlm_issues))
    writer(events.validation_report(current, stage="review_vlm"))
    return {"validation_issues": current, "vlm_reviewed": True}


def _has_must_fix(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("severity") == "must_fix" and int(issue.get("page") or 0) > 0 for issue in issues)


def _design_review_context(state: DeckGraphState) -> dict[str, Any]:
    contract = state.get("design_contract") if isinstance(state.get("design_contract"), dict) else {}
    template = contract.get("template") if isinstance(contract.get("template"), dict) else {}
    pages = state.get("page_blueprints") if isinstance(state.get("page_blueprints"), list) else contract.get("pages") or []
    return {
        "template": {
            "template_id": template.get("template_id") or state.get("template_id") or "",
            "template_mode": template.get("template_mode") or "",
            "applied": template.get("applied"),
        },
        "art_direction": state.get("art_direction") or {},
        "page_blueprints": [
            {
                "index": item.get("index"),
                "layout_id": item.get("layout_id"),
                "page_rhythm": item.get("page_rhythm") or item.get("rhythm"),
                "composition": item.get("composition"),
                "visualization": item.get("visualization"),
            }
            for item in pages
            if isinstance(item, dict)
        ],
    }
