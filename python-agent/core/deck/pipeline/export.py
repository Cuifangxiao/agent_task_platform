"""Export node that writes the final PPTX artifact."""

from __future__ import annotations

import logging
import json
import time
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.export import DeckExporter
from core.deck.pipeline.state import DeckGraphState
from core.deck.spec import DeckSpec

logger = logging.getLogger(__name__)


def export_deck(state: DeckGraphState, exporter: DeckExporter) -> dict[str, Any]:
    writer = get_stream_writer()
    request_id = str(state["request_id"])
    writer(events.stage_started("export", "Exporting PPTX file"))
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    _attach_source_notes(deck, state.get("brief") or {})
    total = len(deck.slides)
    writer(events.export_progress("Preparing PPTX", 0, total))
    logger.info("deck export start: request_id=%s slides=%s", request_id, total)
    started_at = time.perf_counter()
    result = exporter.export(request_id, deck)
    result.path.parent.joinpath("spec.raw.json").write_text(
        json.dumps({"slides": state.get("raw_slides") or []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result.path.parent.joinpath("spec.final.json").write_text(
        json.dumps(deck.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result.path.parent.joinpath("source-chunks.json").write_text(
        json.dumps(state.get("source_chunks") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result.path.parent.joinpath("fact-ledger.json").write_text(
        json.dumps((state.get("brief") or {}).get("facts") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    issues = list(state.get("validation_issues") or [])
    must_fix = [issue for issue in issues if issue.get("severity") == "must_fix"]
    quality_status = "needs_review" if must_fix else ("success_with_warnings" if result.warnings or issues else "success")
    logger.info("deck export done: request_id=%s path=%s warnings=%s elapsed=%.2fs", request_id, result.path, len(result.warnings), time.perf_counter() - started_at)
    for warning in result.warnings:
        writer(
            events.stage_progress("export", warning, {"status": "warning", "warning": warning})
        )
    writer(events.export_progress("PPTX export complete", total, total))
    writer(
        events.artifact(
            "export",
            "PPTX file ready",
            str(result.path),
            {
                "request_id": request_id,
                "filename": result.path.name,
                "path": str(result.path),
                "relative_path": "deck.pptx",
                "warnings": result.warnings,
                "issues": issues,
                "needs_review": bool(must_fix),
                "quality_status": quality_status,
                "degraded": bool(result.manifest.get("degraded")),
                "export_manifest": "export-manifest.json",
                "degraded_pages": result.manifest.get("degraded_pages", []),
                "editable_text_boxes": result.manifest.get("structure", {}).get("editable_text_boxes", []),
                "fonts": result.manifest.get("fonts", []),
            },
        )
    )
    return {"artifact_path": str(result.path), "quality_status": quality_status, "export_degraded": bool(result.manifest.get("degraded"))}


def _attach_source_notes(deck: DeckSpec, brief: dict[str, Any]) -> None:
    facts = {str(item.get("id") or ""): item for item in brief.get("facts") or [] if isinstance(item, dict)}
    for slide in deck.slides:
        citations = []
        for source_id in slide.sources:
            fact = facts.get(source_id)
            if not fact:
                continue
            location = " ".join(
                part
                for part in (
                    str(fact.get("source_file") or ""),
                    f"p.{fact.get('page')}" if fact.get("page") else "",
                    str(fact.get("section") or ""),
                )
                if part
            )
            excerpt = str(fact.get("source_excerpt") or fact.get("statement") or "").strip()
            citations.append(f"{source_id} — {location}: {excerpt}".strip())
        if citations:
            slide.notes = (slide.notes.strip() + "\n\nSource details:\n" + "\n".join(citations)).strip()
