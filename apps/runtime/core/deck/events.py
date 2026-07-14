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
    "style": PptStage.PLANNING,
    "design_lock": PptStage.GENERATING_ASSETS,
    "writer": PptStage.GENERATING_CONTENT,
    "slides": PptStage.GENERATING_PAGES,
    "designer": PptStage.GENERATING_PAGES,
    "render": PptStage.RENDERING,
    "export": PptStage.ASSEMBLING_PPT,
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


def text_delta(stage: str, channel: str, delta: str, message: str) -> PipelineEvent:
    del channel, delta
    return PipelineEvent(EventType.STEP_PROGRESS, stage_for(stage), message)


def style_options(payload: dict[str, Any]) -> PipelineEvent:
    options = payload.get("options") if isinstance(payload, dict) else []
    name = str(options[0].get("name") or "") if isinstance(options, list) and options else ""
    return PipelineEvent(
        EventType.STEP_COMPLETED,
        PptStage.PLANNING,
        f"Recommended style selected: {name}" if name else "Recommended style selected",
    )


def slide_started(message: str, payload: dict[str, Any]) -> PipelineEvent:
    return PipelineEvent(EventType.STEP_STARTED, PptStage.GENERATING_CONTENT, message, page=_page(payload))


def slide_spec_ready(message: str, payload: dict[str, Any]) -> PipelineEvent:
    return PipelineEvent(EventType.STEP_COMPLETED, PptStage.GENERATING_CONTENT, message, page=_page(payload))


def slide_ready(message: str, payload: dict[str, Any]) -> PipelineEvent:
    return PipelineEvent(EventType.STEP_COMPLETED, PptStage.GENERATING_PAGES, message, page=_page(payload))


def slide_preview(message: str, payload: dict[str, Any]) -> PipelineEvent:
    index = _positive_int(payload.get("index"))
    resources: list[ResourceRef] = []
    for key, resource_type, mime_type in (
        ("png", ResourceType.PAGE_PREVIEW, "image/png"),
        ("html", ResourceType.HTML, "text/html"),
        ("svg", ResourceType.SVG, "image/svg+xml"),
    ):
        relative_path = str(payload.get(key) or "").strip()
        if not relative_path:
            continue
        resources.append(
            ResourceRef(
                resource_id=f"page-{index:03d}-{key}",
                type=resource_type,
                name=relative_path.rsplit("/", 1)[-1],
                relative_path=relative_path,
                mime_type=mime_type,
            )
        )
    return PipelineEvent(
        EventType.ARTIFACT_READY,
        PptStage.RENDERING,
        message,
        resources=tuple(resources),
        page=_page(payload),
    )


def export_progress(message: str, current: int, total: int) -> PipelineEvent:
    return PipelineEvent(
        EventType.STEP_PROGRESS,
        PptStage.ASSEMBLING_PPT,
        message,
        progress=_make_progress(current, total, "slides"),
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


def _page(payload: dict[str, Any]) -> PageDetail | None:
    index = _positive_int(payload.get("index"))
    if not index:
        return None
    total = _positive_int(payload.get("total") or payload.get("page_count"))
    return PageDetail(
        page_id=f"page-{index:03d}",
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
    "export_progress",
    "fact_sheet_ready",
    "outline_ready",
    "slide_preview",
    "slide_ready",
    "slide_spec_ready",
    "slide_started",
    "stage_for",
    "stage_progress",
    "stage_started",
    "style_options",
    "text_delta",
]
