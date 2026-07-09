"""Event dict constructors for deck generation streams."""

from __future__ import annotations

from typing import Any


class EventType:
    AGENT_MESSAGE = "agent_message"
    ARTIFACT = "artifact"
    EXPORT_PROGRESS = "export_progress"
    FACT_SHEET_READY = "fact_sheet_ready"
    OUTLINE = "outline"
    REPAIR_PROGRESS = "repair_progress"
    SLIDE_PREVIEW = "slide_preview"
    SLIDE_READY = "slide_ready"
    SLIDE_SPEC_READY = "slide_spec_ready"
    SLIDE_STARTED = "slide_started"
    STAGE_PROGRESS = "stage_progress"
    STAGE_STARTED = "stage_started"
    STYLE_OPTIONS = "style_options"
    TEXT_DELTA = "text_delta"
    VALIDATION_REPORT = "validation_report"


def agent_message(stage: str, agent: str, message: str) -> dict[str, Any]:
    return event(EventType.AGENT_MESSAGE, stage, message, payload={"agent": agent, "message": message})


def artifact(stage: str, message: str, artifact_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.ARTIFACT, stage, message, artifact_path=artifact_path, payload=payload)


def export_progress(message: str, current: int, total: int) -> dict[str, Any]:
    return event(EventType.EXPORT_PROGRESS, "export", message, payload={"current": current, "total": total})


def fact_sheet_ready(payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.FACT_SHEET_READY, "brief", "Fact sheet ready", payload=payload)


def outline_ready(outline_markdown: str) -> dict[str, Any]:
    return event(
        EventType.OUTLINE,
        "outline",
        "Outline ready",
        content=outline_markdown,
        outline_markdown=outline_markdown,
        payload={"outline_markdown": outline_markdown},
    )


def repair_progress(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.REPAIR_PROGRESS, "repair", message, payload=payload)


def slide_preview(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.SLIDE_PREVIEW, "design_slides", message, payload=payload)


def slide_ready(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.SLIDE_READY, "slides", message, payload=payload)


def slide_spec_ready(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.SLIDE_SPEC_READY, "slides", message, payload=payload)


def slide_started(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.SLIDE_STARTED, "slides", message, payload=payload)


def stage_progress(stage: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return event(EventType.STAGE_PROGRESS, stage, message, payload=payload)


def stage_started(stage: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return event(EventType.STAGE_STARTED, stage, message, payload=payload)


def style_options(payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.STYLE_OPTIONS, "style", "Style options ready", payload=payload)


def text_delta(stage: str, channel: str, delta: str, message: str) -> dict[str, Any]:
    return event(EventType.TEXT_DELTA, stage, message, channel=channel, text_delta=delta)


def validation_report(issues: list[dict[str, Any]]) -> dict[str, Any]:
    return event(EventType.VALIDATION_REPORT, "validate", "Validation complete", payload={"issues": issues})


def event(
    event_type: str,
    stage: str,
    message: str = "",
    *,
    payload: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if not event_type:
        raise ValueError("event_type is required")
    data: dict[str, Any] = {"event_type": event_type, "stage": stage, "message": message}
    if payload is not None:
        data["payload"] = payload
    for key, value in extra.items():
        if value is not None:
            data[key] = value
    return data
