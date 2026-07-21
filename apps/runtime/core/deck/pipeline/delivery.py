"""Terminal nodes for the HTML-first, image-only PPTX workflow.

The generation graph owns HTML authoring and PNG capture.  This module has a
deliberately narrow responsibility: validate the completed PNG roster, assemble
the delivery PPTX, persist public audit reports, publish the PPTX resource, and
mark the graph state complete.  It never re-renders HTML or creates editable
PowerPoint shapes.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.export.image_pptx import ImagePptxExporter
from core.deck.paths import DeckProjectPaths
from core.deck.schemas import PROMPT_VERSION, RENDERER_VERSION, SCHEMA_VERSION
from core.deck.state import DeckGraphState


GENERATION_MANIFEST_SCHEMA = "deck-generation-manifest.v2"
QUALITY_REPORT_SCHEMA = "deck-quality-report.v2"


class DeliveryNodes:
    """Dependencies and callable nodes used by the flat root graph."""

    def __init__(
        self,
        output_dir: Path,
        *,
        exporter: ImagePptxExporter | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.exporter = exporter or ImagePptxExporter(self.output_dir)

    def assemble_image_pptx(self, state: DeckGraphState) -> dict[str, Any]:
        return assemble_image_pptx(state, self.exporter)

    def finalize_deck(self, state: DeckGraphState) -> dict[str, Any]:
        return finalize_deck(state)


def assemble_image_pptx(
    state: DeckGraphState,
    exporter: ImagePptxExporter,
) -> dict[str, Any]:
    """Assemble an image-only PPTX and publish its public artifact event."""

    request_id = str(state.get("request_id") or state.get("task_id") or "").strip()
    if not request_id:
        raise ValueError("request_id is required before PPTX assembly")

    project = exporter.project_paths(request_id)
    _assert_project_identity(state, project)
    completed = state.get("completed_slides") or {}
    planned_slides = (
        state.get("deck_plan", {}).get("slides")
        if isinstance(state.get("deck_plan"), dict)
        else None
    )
    if isinstance(planned_slides, list) and len(completed) != len(planned_slides):
        raise ValueError(
            f"cannot export an incomplete deck: {len(completed)} of {len(planned_slides)} slides completed"
        )
    writer = get_stream_writer()
    writer(events.stage_started("assemble_image_pptx", "Assembling image-only PPTX"))

    result = exporter.export(request_id, completed)
    ordered_slides = _ordered_completed_slides(completed)
    diagnostics = _collect_diagnostics(project, ordered_slides, state.get("validation_issues") or [])
    quality_report = _build_quality_report(
        request_id=request_id,
        slides=ordered_slides,
        diagnostics=diagnostics,
        export_warnings=list(result.warnings),
    )
    generation_manifest = _build_generation_manifest(
        state=state,
        project=project,
        slides=ordered_slides,
        diagnostics=diagnostics,
        export_manifest=result.manifest,
        export_warnings=list(result.warnings),
        quality_report=quality_report,
    )
    _atomic_write_json(project.quality_report, quality_report)
    _atomic_write_json(project.generation_manifest, generation_manifest)

    artifact = {
        "resource_id": "pptx",
        "filename": result.path.name,
        "path": str(result.path),
        "relative_path": project.relative(result.path),
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "size_bytes": result.path.stat().st_size,
        "sha256": hashlib.sha256(result.path.read_bytes()).hexdigest(),
        "slide_count": len(ordered_slides),
        "image_based": True,
        "editable": False,
        "export_manifest": project.relative(project.export_manifest),
        "generation_manifest": project.relative(project.generation_manifest),
        "quality_report": project.relative(project.quality_report),
    }
    for warning in result.warnings:
        writer(
            events.stage_progress(
                "assemble_image_pptx",
                warning,
                {"status": "warning", "warning": warning},
            )
        )
    writer(
        events.artifact(
            "assemble_image_pptx",
            "Image-only PPTX ready",
            str(result.path),
            artifact,
        )
    )
    return {
        "artifact_path": str(result.path),
        "status": "exporting",
    }


def finalize_deck(state: DeckGraphState) -> dict[str, Any]:
    """Verify the delivery artifact and leave terminal ownership to the service."""

    raw_path = str(state.get("artifact_path") or "").strip()
    if not raw_path:
        raise ValueError("cannot finalize a deck without artifact_path")
    artifact_path = Path(raw_path).resolve()
    if not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
        raise ValueError("cannot finalize a missing or empty PPTX artifact")

    get_stream_writer()(
        events.stage_progress(
            "finalize_deck",
            "Deck artifacts finalized",
            {"status": "done"},
        )
    )
    return {"status": "completed"}


def _build_generation_manifest(
    *,
    state: DeckGraphState,
    project: DeckProjectPaths,
    slides: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    export_manifest: dict[str, Any],
    export_warnings: list[str],
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    diagnostics_by_slide: dict[str, list[dict[str, Any]]] = {}
    for diagnostic in diagnostics:
        slide_id = str(diagnostic.get("slide_id") or "")
        diagnostics_by_slide.setdefault(slide_id, []).append(diagnostic)

    pages = []
    for slide in slides:
        slide_id = str(slide.get("slide_id") or "")
        pages.append(
            {
                "slide_id": slide_id,
                "index": int(slide.get("index") or 0),
                "title": str(slide.get("title") or ""),
                "layout_family": str(slide.get("layout_family") or ""),
                "layout_variant": str(slide.get("layout_variant") or ""),
                "composition_summary": str(slide.get("composition_summary") or ""),
                "html": str(slide.get("html_path") or ""),
                "preview": str(slide.get("preview_path") or ""),
                "diagnostics": str(slide.get("diagnostics_path") or ""),
                "hashes": {
                    "content": str(slide.get("content_hash") or ""),
                    "html": str(slide.get("html_hash") or ""),
                    "preview": str(slide.get("preview_hash") or ""),
                    "artifact": str(slide.get("artifact_hash") or ""),
                },
                "renderer_version": str(slide.get("renderer_version") or ""),
                "prompt_version": str(slide.get("prompt_version") or ""),
                "base_css_hash": str(slide.get("base_css_hash") or ""),
                "icon_registry_version": str(slide.get("icon_registry_version") or ""),
                "render_environment": dict(slide.get("render_environment") or {}),
                "model_metadata": dict(slide.get("model_metadata") or {}),
                "repair_attempts": max(0, int(slide.get("repair_attempts") or 0)),
                "fallback_used": bool(slide.get("fallback_used")),
                "diagnostic_count": len(diagnostics_by_slide.get(slide_id, [])),
            }
        )

    deck_plan = state.get("deck_plan") if isinstance(state.get("deck_plan"), dict) else {}
    design_spec = state.get("design_spec") if isinstance(state.get("design_spec"), dict) else {}
    public_design_files = {
        "design_spec": _relative_if_file(project, project.design_spec_json),
        "design_spec_markdown": _relative_if_file(project, project.design_spec_markdown),
        "execution_lock": _relative_if_file(project, project.spec_lock_json),
        "execution_lock_markdown": _relative_if_file(project, project.spec_lock_markdown),
        "slide_artifacts": _relative_if_file(project, project.slide_artifact_manifest),
    }
    return {
        "schema": GENERATION_MANIFEST_SCHEMA,
        "request_id": str(state.get("request_id") or state.get("task_id") or ""),
        "versions": {
            "state_schema": str(state.get("schema_version") or SCHEMA_VERSION),
            "prompt": str(state.get("prompt_version") or PROMPT_VERSION),
            "renderer": str(state.get("renderer_version") or RENDERER_VERSION),
        },
        "deck": {
            "title": str(deck_plan.get("deck_title") or state.get("deck_title") or ""),
            "slide_count": len(slides),
            "core_message": str(deck_plan.get("core_message") or ""),
        },
        "design": {
            "template_id": str(
                state.get("selected_template_id") or design_spec.get("theme_id") or ""
            ),
            "theme_id": str(design_spec.get("theme_id") or ""),
            "files": {key: value for key, value in public_design_files.items() if value},
        },
        "pages": pages,
        "quality": {
            "report": project.relative(project.quality_report),
            "passed": bool(quality_report["passed"]),
            "diagnostic_count": int(quality_report["diagnostic_count"]),
            "error_count": int(quality_report["error_count"]),
            "warning_count": int(quality_report["warning_count"]),
            "fallback_page_count": int(quality_report["fallback_page_count"]),
        },
        "export": {
            "engine": "python-pptx-image-only",
            "image_based": True,
            "editable": False,
            "degraded": False,
            "warnings": export_warnings,
            "manifest": project.relative(project.export_manifest),
            "artifact": project.relative(project.deck_pptx),
            "structure": export_manifest.get("structure") or {},
        },
    }


def _build_quality_report(
    *,
    request_id: str,
    slides: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    export_warnings: list[str],
) -> dict[str, Any]:
    severities = Counter(str(item.get("severity") or "error").casefold() for item in diagnostics)
    categories = Counter(str(item.get("category") or "content").casefold() for item in diagnostics)
    error_count = severities["error"] + severities["must_fix"]
    warning_count = severities["warning"]
    fallback_pages = [
        str(item.get("slide_id") or "") for item in slides if bool(item.get("fallback_used"))
    ]
    repaired_pages = [
        {
            "slide_id": str(item.get("slide_id") or ""),
            "attempts": max(0, int(item.get("repair_attempts") or 0)),
        }
        for item in slides
        if int(item.get("repair_attempts") or 0) > 0
    ]
    return {
        "schema": QUALITY_REPORT_SCHEMA,
        "request_id": request_id,
        "passed": error_count == 0 and not export_warnings,
        "slide_count": len(slides),
        "diagnostic_count": len(diagnostics),
        "error_count": error_count,
        "warning_count": warning_count,
        "counts_by_severity": dict(sorted(severities.items())),
        "counts_by_category": dict(sorted(categories.items())),
        "fallback_page_count": len(fallback_pages),
        "fallback_pages": fallback_pages,
        "repaired_pages": repaired_pages,
        "diagnostics": diagnostics,
        "export_warnings": export_warnings,
    }


def _collect_diagnostics(
    project: DeckProjectPaths,
    slides: list[dict[str, Any]],
    state_diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prefer persisted per-page diagnostics and use state as a safe fallback."""

    collected: list[dict[str, Any]] = []
    seen_slides: set[str] = set()
    for slide in slides:
        slide_id = str(slide.get("slide_id") or "")
        relative = str(slide.get("diagnostics_path") or "").strip()
        payload = _read_project_json(project, relative) if relative else None
        items = payload.get("diagnostics") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        seen_slides.add(slide_id)
        for item in items:
            if isinstance(item, dict):
                collected.append(_normalize_diagnostic(item, slide))

    indexes = {int(item.get("index") or 0): item for item in slides}
    ids = {str(item.get("slide_id") or ""): item for item in slides}
    for item in state_diagnostics:
        if not isinstance(item, dict):
            continue
        slide_id = str(item.get("slide_id") or "")
        page = int(item.get("page") or item.get("index") or 0)
        slide = ids.get(slide_id) or indexes.get(page) or {}
        resolved_id = str(slide.get("slide_id") or slide_id)
        if resolved_id in seen_slides:
            continue
        collected.append(_normalize_diagnostic(item, slide))
    return collected


def _normalize_diagnostic(item: dict[str, Any], slide: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized["slide_id"] = str(slide.get("slide_id") or item.get("slide_id") or "")
    normalized["index"] = int(slide.get("index") or item.get("page") or item.get("index") or 0)
    normalized["severity"] = str(item.get("severity") or "error")
    normalized["category"] = str(item.get("category") or "content")
    return normalized


def _ordered_completed_slides(
    completed: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values = completed.values() if isinstance(completed, dict) else completed
    slides = [dict(item) for item in values if isinstance(item, dict)]
    slides.sort(key=lambda item: (int(item.get("index") or 0), str(item.get("slide_id") or "")))
    return slides


def _read_project_json(project: DeckProjectPaths, relative_value: str) -> dict[str, Any] | None:
    relative = PurePosixPath(relative_value)
    if not relative_value or relative.is_absolute() or ".." in relative.parts:
        return None
    path = project.root.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(project.root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _relative_if_file(project: DeckProjectPaths, path: Path) -> str:
    return project.relative(path) if path.is_file() else ""


def _assert_project_identity(state: DeckGraphState, project: DeckProjectPaths) -> None:
    claimed = str(state.get("project_dir") or "").strip()
    if claimed and Path(claimed).resolve() != project.root:
        raise ValueError("project_dir does not match the request-scoped artifact directory")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "GENERATION_MANIFEST_SCHEMA",
    "DeliveryNodes",
    "QUALITY_REPORT_SCHEMA",
    "assemble_image_pptx",
    "finalize_deck",
]
