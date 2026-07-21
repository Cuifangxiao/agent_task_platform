"""Typed constructors for LangGraph custom events."""

from __future__ import annotations

from typing import Any

from core.deck.contract import (
    EventType,
    PageDetail,
    PipelineEvent,
    PptStage,
    Progress,
    ResourceRef,
    ResourceType,
)


_STAGES = {
    "brief": PptStage.PREPARING,
    "outline": PptStage.PLANNING,
    "template_selection": PptStage.PLANNING,
    "design_lock": PptStage.GENERATING_ASSETS,
    "writer": PptStage.GENERATING_PAGES,
    "slides": PptStage.GENERATING_PAGES,
    "designer": PptStage.GENERATING_PAGES,
    "render": PptStage.GENERATING_PAGES,
    "export": PptStage.ASSEMBLING_PPT,
    "normalize_request": PptStage.PREPARING,
    "plan_deck": PptStage.PLANNING,
    "select_template": PptStage.PLANNING,
    "create_design_spec": PptStage.GENERATING_ASSETS,
    # Once a page starts, keep the externally visible stage monotonic through
    # its content, HTML, repair, and preview work.
    "prepare_slide": PptStage.GENERATING_PAGES,
    "generate_slide_content": PptStage.GENERATING_PAGES,
    "generate_slide_html": PptStage.GENERATING_PAGES,
    "sanitize_and_assemble_html": PptStage.GENERATING_PAGES,
    "repair_slide_html": PptStage.GENERATING_PAGES,
    "build_fallback_html": PptStage.GENERATING_PAGES,
    "render_html_and_measure_dom": PptStage.RENDERING,
    "validate_dom": PptStage.RENDERING,
    "capture_png": PptStage.RENDERING,
    "persist_slide_artifacts": PptStage.RENDERING,
    "emit_slide_preview": PptStage.RENDERING,
    "advance_slide": PptStage.GENERATING_PAGES,
    "assemble_image_pptx": PptStage.ASSEMBLING_PPT,
    # FINISHED remains owned by the service's terminal TASK_COMPLETED event.
    "finalize_deck": PptStage.ASSEMBLING_PPT,
}


def stage_for(name: str) -> PptStage:
    return _STAGES.get(str(name or "").strip(), PptStage.PREPARING)


def agent_message(stage: str, agent: str, message: str) -> PipelineEvent:
    return PipelineEvent(EventType.STEP_PROGRESS, stage_for(stage), f"{agent}: {message}")


def stage_started(stage: str, message: str, payload: dict[str, Any] | None = None) -> PipelineEvent:
    del payload
    return PipelineEvent(EventType.STEP_STARTED, stage_for(stage), message)


def stage_progress(stage: str, message: str, payload: dict[str, Any] | None = None) -> PipelineEvent:
    payload = payload or {}
    event_type = EventType.STEP_COMPLETED if payload.get("status") == "done" else EventType.STEP_PROGRESS
    progress = _progress(payload)
    return PipelineEvent(event_type, stage_for(stage), message, progress=progress, page=_page(payload))


def fact_sheet_ready(payload: dict[str, Any]) -> PipelineEvent:
    return PipelineEvent(
        EventType.STEP_COMPLETED,
        PptStage.PREPARING,
        f"Brief ready with {int(payload.get('facts_count') or 0)} source facts",
    )


def outline_ready(outline_markdown: str) -> PipelineEvent:
    del outline_markdown
    return PipelineEvent(EventType.STEP_COMPLETED, PptStage.PLANNING, "Outline ready")


def template_selected(name: str, template_id: str) -> PipelineEvent:
    label = str(name or template_id or "").strip()
    return PipelineEvent(
        EventType.STEP_COMPLETED,
        PptStage.PLANNING,
        f"Template selected: {label}" if label else "Template selected",
    )


def slide_started(message: str, payload: dict[str, Any]) -> PipelineEvent:
    return PipelineEvent(
        EventType.STEP_STARTED,
        PptStage.GENERATING_PAGES,
        message,
        progress=_page_progress(payload),
        page=_page(payload),
    )


def slide_preview(message: str, payload: dict[str, Any]) -> PipelineEvent:
    index = _positive_int(payload.get("index"))
    slide_id = str(payload.get("slide_id") or f"s{index:02d}").strip()
    resources: list[ResourceRef] = []
    # Production v2 emits only ``png``.  Keeping the helper compatible with
    # legacy sanitized HTML/SVG callers avoids breaking the transport layer.
    for key, resource_type, mime_type, suffix in (
        ("png", ResourceType.PAGE_PREVIEW, "image/png", "preview"),
        ("html", ResourceType.HTML, "text/html", "html"),
        ("svg", ResourceType.SVG, "image/svg+xml", "svg"),
    ):
        relative_path = str(payload.get(key) or "").strip()
        if not relative_path:
            continue
        resources.append(
            ResourceRef(
                # Stable per-slide keys let current web clients replace a
                # regenerated preview instead of showing duplicate cards.
                resource_id=f"{slide_id}-{suffix}",
                type=resource_type,
                name=relative_path.rsplit("/", 1)[-1],
                relative_path=relative_path,
                mime_type=mime_type,
            )
        )
    return PipelineEvent(
        EventType.ARTIFACT_READY,
        PptStage.GENERATING_PAGES,
        message,
        progress=_page_progress(payload),
        resources=tuple(resources),
        page=_page(payload),
        event_id=str(payload.get("event_id") or "").strip(),
        revision=str(payload.get("artifact_hash") or "").strip(),
    )


def artifact(stage: str, message: str, artifact_path: str, payload: dict[str, Any]) -> PipelineEvent:
    relative_path = str(payload.get("relative_path") or "").strip()
    name = str(payload.get("filename") or relative_path.rsplit("/", 1)[-1] or "deck.pptx")
    del artifact_path
    return PipelineEvent(
        EventType.ARTIFACT_READY,
        stage_for(stage),
        message,
        resources=(
            ResourceRef(
                resource_id="pptx",
                type=ResourceType.PPTX,
                name=name,
                relative_path=relative_path,
                mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        ),
    )


def _progress(payload: dict[str, Any]) -> Progress | None:
    source = payload.get("progress") if isinstance(payload.get("progress"), dict) else payload
    current = _positive_int(source.get("current"))
    total = _positive_int(source.get("total"))
    return _make_progress(current, total, str(source.get("unit") or "")) if current or total else None


def _make_progress(current: int, total: int, unit: str) -> Progress:
    percent = min(100.0, max(0.0, (current / total * 100.0) if total else 0.0))
    return Progress(current=current, total=total, percent=percent, unit=unit)


def _page_progress(payload: dict[str, Any]) -> Progress | None:
    index = _positive_int(payload.get("index"))
    total = _positive_int(payload.get("total") or payload.get("page_count"))
    return _make_progress(index, total, "page") if index and total else None


def _page(payload: dict[str, Any]) -> PageDetail | None:
    index = _positive_int(payload.get("index"))
    if not index:
        return None
    total = _positive_int(payload.get("total") or payload.get("page_count"))
    slide_id = str(payload.get("slide_id") or f"s{index:02d}").strip()
    return PageDetail(
        page_id=slide_id,
        page_index=index,
        page_count=total,
        title=str(payload.get("title") or ""),
    )


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "agent_message",
    "artifact",
    "fact_sheet_ready",
    "outline_ready",
    "slide_preview",
    "slide_started",
    "stage_for",
    "stage_progress",
    "stage_started",
    "template_selected",
]
