"""Typed event constructors for the deck generation stream."""

from __future__ import annotations

from typing import Any, TypedDict


EVENT_VERSION = 1


class DeckEvent(TypedDict, total=False):
    """Transport-neutral event emitted by a deck graph node."""

    event_version: int
    event_type: str
    stage: str
    stage_status: str
    message: str
    severity: str
    action_required: str
    channel: str
    text_delta: str
    content: str
    outline_markdown: str
    payload: dict[str, Any]
    artifact_path: str
    done: bool
    error: str
    error_code: str
    retryable: bool
    progress: dict[str, int]
    slide: dict[str, int]


class EventType:
    AGENT_MESSAGE = "agent_message"
    ARTIFACT = "artifact"
    DONE = "done"
    ERROR = "error"
    EXPORT_PROGRESS = "export_progress"
    FACT_SHEET_READY = "fact_sheet_ready"
    INTERRUPT = "interrupt"
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


def agent_message(stage: str, agent: str, message: str) -> DeckEvent:
    return event(EventType.AGENT_MESSAGE, stage, message, payload={"agent": agent, "message": message})


def artifact(stage: str, message: str, artifact_path: str, payload: dict[str, Any]) -> DeckEvent:
    has_warning = payload.get("warnings") or payload.get("degraded") or payload.get("needs_review")
    return event(
        EventType.ARTIFACT,
        stage,
        message,
        artifact_path=artifact_path,
        payload=payload,
        stage_status="done",
        severity="warning" if has_warning else "info",
    )


def export_progress(message: str, current: int, total: int) -> DeckEvent:
    return event(
        EventType.EXPORT_PROGRESS,
        "export",
        message,
        progress={"current": current, "total": total},
        payload={"current": current, "total": total},
    )


def fact_sheet_ready(payload: dict[str, Any]) -> DeckEvent:
    return event(EventType.FACT_SHEET_READY, "brief", "Fact sheet ready", payload=payload)


def generation_done() -> DeckEvent:
    return event(
        EventType.DONE,
        "export",
        "Deck generation complete",
        done=True,
        stage_status="done",
    )


def interrupt_requested(payload: dict[str, Any]) -> DeckEvent:
    """Build the public event for a LangGraph human-in-the-loop interrupt."""

    stage = normalize_stage(str(payload.get("stage") or "outline_review"))
    outline_markdown = str(payload.get("outline_markdown") or "")
    message = {
        "style_review": "Style options are ready for review",
    }.get(stage, "Outline is ready for review")
    return event(
        EventType.INTERRUPT,
        stage,
        message,
        payload=payload,
        outline_markdown=outline_markdown,
        action_required=default_action_required(EventType.INTERRUPT, stage),
        done=True,
        stage_status="waiting",
    )


def outline_ready(outline_markdown: str) -> DeckEvent:
    return event(
        EventType.OUTLINE,
        "outline",
        "Outline ready",
        content=outline_markdown,
        outline_markdown=outline_markdown,
        payload={"outline_markdown": outline_markdown},
    )


def repair_progress(message: str, payload: dict[str, Any]) -> DeckEvent:
    return event(EventType.REPAIR_PROGRESS, "repair", message, payload=payload)


def slide_preview(message: str, payload: dict[str, Any]) -> DeckEvent:
    return event(EventType.SLIDE_PREVIEW, "designer", message, payload=payload, slide=_slide_ref(payload))


def slide_ready(message: str, payload: dict[str, Any]) -> DeckEvent:
    return event(EventType.SLIDE_READY, "writer", message, payload=payload, slide=_slide_ref(payload))


def slide_spec_ready(message: str, payload: dict[str, Any]) -> DeckEvent:
    return event(EventType.SLIDE_SPEC_READY, "writer", message, payload=payload, slide=_slide_ref(payload))


def slide_started(message: str, payload: dict[str, Any]) -> DeckEvent:
    return event(EventType.SLIDE_STARTED, "writer", message, payload=payload, slide=_slide_ref(payload))


def stage_progress(stage: str, message: str, payload: dict[str, Any] | None = None) -> DeckEvent:
    return event(EventType.STAGE_PROGRESS, stage, message, payload=payload)


def stage_started(stage: str, message: str, payload: dict[str, Any] | None = None) -> DeckEvent:
    return event(EventType.STAGE_STARTED, stage, message, payload=payload)


def style_options(payload: dict[str, Any]) -> DeckEvent:
    return event(EventType.STYLE_OPTIONS, "style", "Style options ready", payload=payload)


def text_delta(stage: str, channel: str, delta: str, message: str) -> DeckEvent:
    return event(EventType.TEXT_DELTA, stage, message, channel=channel, text_delta=delta)


def validation_report(issues: list[dict[str, Any]], *, stage: str = "validate") -> DeckEvent:
    return event(
        EventType.VALIDATION_REPORT,
        stage,
        "Validation complete",
        payload={"issues": issues},
        severity="warning" if issues else "info",
        stage_status="warning" if issues else "done",
    )


def event(
    event_type: str,
    stage: str,
    message: str = "",
    *,
    payload: dict[str, Any] | None = None,
    **extra: Any,
) -> DeckEvent:
    if not event_type:
        raise ValueError("event_type is required")
    normalized_stage = normalize_stage(stage)
    payload_status = str((payload or {}).get("status") or "") if isinstance(payload, dict) else ""
    known_statuses = {"pending", "running", "waiting", "done", "warning", "error"}
    stage_status = (
        str(extra.pop("stage_status", ""))
        or (payload_status if payload_status in known_statuses else "")
        or default_stage_status(event_type)
    )
    default_severity = "error" if stage_status == "error" else "warning" if stage_status == "warning" else "info"
    data: DeckEvent = {
        "event_version": EVENT_VERSION,
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


def normalize_stage(stage: str) -> str:
    """Map internal node names to the stable public stage names."""

    normalized = stage.strip()
    aliases = {
        "slides": "writer",
        "write_slides": "writer",
        "design_slides": "designer",
        "render": "designer",
        "review": "review_vlm",
        "done": "export",
    }
    return aliases.get(normalized, normalized)


def default_stage_status(event_type: str) -> str:
    if event_type == EventType.INTERRUPT:
        return "waiting"
    if event_type in {EventType.ARTIFACT, EventType.DONE}:
        return "done"
    if event_type == EventType.ERROR:
        return "error"
    return "running"


def default_severity(stage_status: str, payload: Any) -> str:
    if stage_status == "error":
        return "error"
    if isinstance(payload, dict) and (
        payload.get("warning")
        or payload.get("warnings")
        or payload.get("status") == "warning"
    ):
        return "warning"
    return "info"


def default_action_required(event_type: str, stage: str) -> str:
    if event_type != EventType.INTERRUPT:
        return ""
    return "choose_style" if normalize_stage(stage) == "style_review" else "approve_outline"


def _slide_ref(payload: dict[str, Any]) -> dict[str, int] | None:
    try:
        index = int(payload.get("index") or 0)
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        return None
    return {"index": index, "version": version} if index > 0 else None
