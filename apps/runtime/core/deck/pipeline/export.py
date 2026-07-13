"""Export node that writes the final PPTX artifact."""

from __future__ import annotations

import logging
import json
import time
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.export import DeckExporter
from core.deck.errors import ExportError
from core.deck.paths import DeckProjectPaths
from core.deck.pipeline.state import DeckGraphState
from core.deck.spec import DeckSpec

logger = logging.getLogger(__name__)
SVG_EXPORT_BLOCKING_KINDS = frozenset(
    {
        "svg_invalid_xml",
        "svg_missing_root",
        "svg_unsupported_element",
        "svg_forbidden_attribute",
        "svg_image_href",
        "svg_viewbox",
        "svg_canvas_size",
        "svg_spec_drift",
        "svg_text_box",
        "svg_text_out_of_bounds",
        "svg_content_omission",
        "svg_fabricated_number",
    }
)


def export_deck(state: DeckGraphState, exporter: DeckExporter) -> dict[str, Any]:
    writer = get_stream_writer()
    request_id = str(state["request_id"])
    writer(events.stage_started("export", "Exporting PPTX file"))
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    _attach_source_notes(deck, state.get("brief") or {})
    _enforce_svg_export_gate(state, deck)
    total = len(deck.slides)
    writer(events.export_progress("Preparing PPTX", 0, total))
    logger.info("deck export start: request_id=%s slides=%s", request_id, total)
    started_at = time.perf_counter()
    result = exporter.export(request_id, deck)
    project = exporter.project_paths(request_id)
    project.sources.joinpath("spec.raw.json").write_text(
        json.dumps({"slides": state.get("raw_slides") or []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    project.sources.joinpath("spec.final.json").write_text(
        json.dumps(deck.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    project.sources.joinpath("source-chunks.json").write_text(
        json.dumps(state.get("source_chunks") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    project.sources.joinpath("fact-ledger.json").write_text(
        json.dumps((state.get("brief") or {}).get("facts") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_speaker_notes(project, deck)
    issues = list(state.get("validation_issues") or [])
    must_fix = [issue for issue in issues if issue.get("severity") == "must_fix"]
    quality_status = "needs_review" if must_fix else ("success_with_warnings" if result.warnings or issues else "success")
    generation_manifest = _write_generation_manifest(
        state,
        deck,
        result.manifest,
        result.warnings,
        issues,
        quality_status,
        project,
    )
    _write_quality_report(project, issues, quality_status, result.warnings)
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
                "relative_path": project.relative(result.path),
                "warnings": result.warnings,
                "issues": issues,
                "needs_review": bool(must_fix),
                "quality_status": quality_status,
                "degraded": bool(result.manifest.get("degraded")),
                "export_manifest": project.relative(project.export_manifest),
                "degraded_pages": result.manifest.get("degraded_pages", []),
                "editable_text_boxes": result.manifest.get("structure", {}).get("editable_text_boxes", []),
                "fonts": result.manifest.get("fonts", []),
                "native_svg_pages": [
                    item.get("index")
                    for item in result.manifest.get("slides", [])
                    if item.get("export_mode") == "native_svg"
                ],
                "svg_sources": [item.get("svg") for item in result.manifest.get("slides", []) if item.get("svg")],
                "design_spec": "design-spec.json",
                "design_spec_markdown": "design-spec.md",
                "spec_lock": "spec-lock.json",
                "spec_lock_markdown": "spec-lock.md",
                "page_blueprints": "page-blueprints.json",
                "asset_manifest": "asset-manifest.json",
                "template_manifest": "template-manifest.json",
                "generation_manifest": project.relative(project.generation_manifest),
                "quality_report": project.relative(project.quality_report),
                "speaker_notes": project.relative(project.notes_total),
                "template": generation_manifest.get("template", {}),
            },
        )
    )
    return {
        "artifact_path": str(result.path),
        "quality_status": quality_status,
        "export_degraded": bool(result.manifest.get("degraded")),
        "generation_manifest": generation_manifest,
    }


def _write_generation_manifest(
    state: DeckGraphState,
    deck: DeckSpec,
    export_manifest: dict[str, Any],
    export_warnings: list[str],
    issues: list[dict[str, Any]],
    quality_status: str,
    project: DeckProjectPaths,
) -> dict[str, Any]:
    """Write one audit trail covering strategy, page authoring, QA, and export."""

    contract = state.get("design_contract") if isinstance(state.get("design_contract"), dict) else {}
    template = contract.get("template") if isinstance(contract.get("template"), dict) else {}
    if not template:
        template = state.get("template_manifest") if isinstance(state.get("template_manifest"), dict) else {}
    blueprints = state.get("page_blueprints") if isinstance(state.get("page_blueprints"), list) else contract.get("pages") or []
    blueprints_by_page = {
        int(item.get("index") or 0): item for item in blueprints if isinstance(item, dict) and int(item.get("index") or 0) > 0
    }
    artifacts_by_page = {
        int(item.get("index") or 0): item
        for item in state.get("artifacts") or []
        if isinstance(item, dict) and int(item.get("index") or 0) > 0
    }
    exports_by_page = {
        int(item.get("index") or 0): item
        for item in export_manifest.get("slides") or []
        if isinstance(item, dict) and int(item.get("index") or 0) > 0
    }
    issues_by_page: dict[int, list[dict[str, Any]]] = {}
    for issue in issues:
        page = _positive_int(issue.get("page"))
        issues_by_page.setdefault(page, []).append(issue)

    pages: list[dict[str, Any]] = []
    for slide in deck.slides:
        blueprint = blueprints_by_page.get(slide.index, {})
        artifact = artifacts_by_page.get(slide.index, {})
        export = exports_by_page.get(slide.index, {})
        pages.append(
            {
                "index": slide.index,
                "title": slide.title,
                "role": slide.role,
                "component": slide.component,
                "template_id": str(blueprint.get("template_id") or template.get("template_id") or state.get("template_id") or ""),
                "layout_id": str(blueprint.get("layout_id") or artifact.get("layout_id") or ""),
                "page_rhythm": str(blueprint.get("page_rhythm") or blueprint.get("rhythm") or artifact.get("page_rhythm") or ""),
                "render_mode": str(artifact.get("svg_renderer") or artifact.get("render_mode") or artifact.get("renderer") or ""),
                "svg": str(artifact.get("svg") or export.get("svg") or ""),
                "preview": str(artifact.get("png") or export.get("preview") or ""),
                "qa_issues": issues_by_page.get(slide.index, []),
                "export": export,
            }
        )

    manifest = {
        "schema": "deck-generation-manifest",
        "request_id": str(state.get("request_id") or ""),
        "spec_hash": str(contract.get("spec_hash") or ""),
        "art_direction": state.get("art_direction") or {},
        "template": template,
        "inputs": {
            "source_chunks": len(state.get("source_chunks") or []),
            "source_images": len(state.get("source_images") or []),
            "generation_mode": str(state.get("generation_mode") or ""),
        },
        "design_files": {
            "design_spec": "design-spec.json",
            "design_spec_markdown": "design-spec.md",
            "spec_lock": "spec-lock.json",
            "spec_lock_markdown": "spec-lock.md",
            "page_blueprints": "page-blueprints.json",
            "asset_manifest": "asset-manifest.json",
            "template_manifest": "template-manifest.json",
        },
        "pages": pages,
        "quality": {
            "status": quality_status,
            "must_fix_count": sum(1 for item in issues if item.get("severity") == "must_fix"),
            "issue_count": len(issues),
            "repair_rounds": int(state.get("repair_round") or 0),
            "vlm_reviewed": bool(state.get("vlm_reviewed")),
            "coherence_reviewed": bool(state.get("coherence_reviewed")),
        },
        "export": {
            "engine": "native-svg-shapes",
            "degraded": bool(export_manifest.get("degraded")),
            "warnings": list(export_warnings),
            "manifest": project.relative(project.export_manifest),
            "artifact": project.relative(project.deck_pptx),
        },
    }
    project.generation_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _write_speaker_notes(project: DeckProjectPaths, deck: DeckSpec) -> None:
    blocks: list[str] = []
    for slide in deck.slides:
        notes = slide.notes.strip()
        if slide.sources:
            notes = (notes + "\n\nSources: " + ", ".join(slide.sources)).strip()
        blocks.extend([f"# Slide {slide.index}: {slide.title}", "", notes, ""])
    project.notes_total.write_text("\n".join(blocks).rstrip() + "\n", encoding="utf-8")


def _write_quality_report(
    project: DeckProjectPaths,
    issues: list[dict[str, Any]],
    status: str,
    export_warnings: list[str],
) -> None:
    report = {
        "schema": "deck-quality-report",
        "status": status,
        "must_fix_count": sum(1 for item in issues if item.get("severity") == "must_fix"),
        "issue_count": len(issues),
        "issues": issues,
        "export_warnings": list(export_warnings),
    }
    project.quality_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _enforce_svg_export_gate(state: DeckGraphState, deck: DeckSpec) -> None:
    if not isinstance(state.get("design_contract"), dict):
        return
    contract = state["design_contract"]
    locked_pages = [
        _positive_int(item.get("index"))
        for item in contract.get("pages") or []
        if isinstance(item, dict)
    ]
    actual_pages = [slide.index for slide in deck.slides]
    if locked_pages != actual_pages:
        raise ExportError(
            f"Locked page roster mismatch: expected {locked_pages}, got {actual_pages}.",
            stage="export",
        )
    blocking = [
        issue
        for issue in state.get("validation_issues") or []
        if issue.get("severity") == "must_fix"
        and str(issue.get("kind") or "") in SVG_EXPORT_BLOCKING_KINDS
    ]
    if blocking:
        pages = sorted({page for issue in blocking if (page := _positive_int(issue.get("page")))})
        page_summary = ", ".join(map(str, pages)) if pages else "unknown"
        raise ExportError(f"SVG quality gate failed for slide(s): {page_summary}.", stage="export")
    svg_pages = {
        page
        for item in state.get("artifacts") or []
        if isinstance(item, dict)
        and str(item.get("svg") or "").strip()
        and (page := _positive_int(item.get("index")))
    }
    missing = [slide.index for slide in deck.slides if slide.index not in svg_pages]
    if missing:
        raise ExportError(f"Canonical SVG source is missing for slide(s): {', '.join(map(str, missing))}.", stage="export")


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


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
