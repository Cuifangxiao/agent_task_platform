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
    severity = "warning" if payload.get("warnings") or payload.get("degraded") or payload.get("needs_review") else "info"
    return event(EventType.ARTIFACT, stage, message, artifact_path=artifact_path, payload=payload, stage_status="done", severity=severity)


def export_progress(message: str, current: int, total: int) -> dict[str, Any]:
    return event(
        EventType.EXPORT_PROGRESS,
        "export",
        message,
        progress={"current": current, "total": total},
        payload={"current": current, "total": total},
    )


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
    return event(EventType.SLIDE_PREVIEW, "designer", message, payload=payload, slide=_slide_ref(payload))


def slide_ready(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.SLIDE_READY, "writer", message, payload=payload, slide=_slide_ref(payload))


def slide_spec_ready(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.SLIDE_SPEC_READY, "writer", message, payload=payload, slide=_slide_ref(payload))


def slide_started(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.SLIDE_STARTED, "writer", message, payload=payload, slide=_slide_ref(payload))


def stage_progress(stage: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return event(EventType.STAGE_PROGRESS, stage, message, payload=payload)


def stage_started(stage: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return event(EventType.STAGE_STARTED, stage, message, payload=payload)


def style_options(payload: dict[str, Any]) -> dict[str, Any]:
    return event(EventType.STYLE_OPTIONS, "style", "Style options ready", payload=payload)


def text_delta(stage: str, channel: str, delta: str, message: str) -> dict[str, Any]:
    return event(EventType.TEXT_DELTA, stage, message, channel=channel, text_delta=delta)


def validation_report(issues: list[dict[str, Any]], *, stage: str = "validate") -> dict[str, Any]:
    severity = "warning" if issues else "info"
    return event(EventType.VALIDATION_REPORT, stage, "Validation complete", payload={"issues": issues}, severity=severity, stage_status="warning" if issues else "done")


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
    normalized_stage = {
        "slides": "writer",
        "design_slides": "designer",
        "review": "review_vlm",
        "render": "designer",
    }.get(stage, stage)
    payload_status = str((payload or {}).get("status") or "") if isinstance(payload, dict) else ""
    stage_status = str(extra.pop("stage_status", "")) or (payload_status if payload_status in {"pending", "running", "waiting", "done", "warning", "error"} else "running")
    default_severity = "error" if stage_status == "error" else "warning" if stage_status == "warning" else "info"
    data: dict[str, Any] = {
        "event_version": 1,
        "event_type": event_type,
        "stage": normalized_stage,
        "stage_status": stage_status,
        "message": message,
        "severity": str(extra.pop("severity", default_severity)),
    }
    if payload is not None:
        data["payload"] = payload
    for key, value in extra.items():
        if value is not None:
            data[key] = value
    return data


def _slide_ref(payload: dict[str, Any]) -> dict[str, int] | None:
    try:
        index = int(payload.get("index") or 0)
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        return None
    return {"index": index, "version": version} if index > 0 else None
