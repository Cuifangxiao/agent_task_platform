"""Adapt LangGraph v2 stream parts to the deck service contract.

Graph nodes emit transport-neutral :class:`DeckEvent` dictionaries through
``get_stream_writer()``.  This module is the single boundary that understands
LangGraph's stream envelope, human-in-the-loop interrupts, sequencing, and the
public ``GenerateDeckOutput`` shape.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Literal, Protocol

from langgraph.types import StreamPart

import core.deck.events as events
from core.deck.contract import GenerateDeckOutput


STREAM_VERSION: Literal["v2"] = "v2"
STREAM_MODES = ("custom", "values")


class StreamableDeckGraph(Protocol):
    """Minimal graph interface used by the deck stream adapter."""

    def stream(
        self,
        stream_input: Any,
        *,
        config: dict[str, Any],
        stream_mode: Sequence[str],
        version: Literal["v2"],
        output_keys: Sequence[str],
    ) -> Iterator[StreamPart]: ...


def stream_graph_outputs(
    job_id: str,
    request_id: str,
    graph: StreamableDeckGraph,
    stream_input: Any,
    config: dict[str, Any],
    *,
    initial_sequence: int = 0,
) -> Iterator[GenerateDeckOutput]:
    """Run a deck graph and yield the stable application-level event stream."""

    sequence = initial_sequence
    terminal_sent = False

    # ``values`` carries v2 interrupt metadata. Empty output keys keep the large
    # deck state (source text, SVG, and specs) out of the transport stream.
    parts = graph.stream(
        stream_input,
        config=config,
        stream_mode=list(STREAM_MODES),
        version=STREAM_VERSION,
        output_keys=[],
    )
    for part in parts:
        event = event_from_stream_part(part)
        if event is None:
            continue

        sequence += 1
        output = event_to_output(job_id, request_id, sequence, event)
        terminal_sent = terminal_sent or output.done
        yield output

    if not terminal_sent:
        sequence += 1
        yield event_to_output(job_id, request_id, sequence, events.generation_done())


def event_from_stream_part(part: StreamPart) -> events.DeckEvent | None:
    """Project one LangGraph v2 ``StreamPart`` into a deck event."""

    if part["type"] == "custom":
        data = part["data"]
        return dict(data) if isinstance(data, Mapping) else None

    if part["type"] == "values":
        return _interrupt_event(part.get("interrupts") or ())

    return None


def event_to_output(
    job_id: str,
    request_id: str,
    sequence: int,
    event: Mapping[str, Any],
) -> GenerateDeckOutput:
    """Map a transport-neutral deck event to the service output contract."""

    payload = event.get("payload")
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
    outline_markdown = str(event.get("outline_markdown") or "")
    if not outline_markdown and isinstance(payload, Mapping):
        outline_markdown = str(payload.get("outline_markdown") or "")

    event_type = str(event.get("event_type") or "progress")
    stage = events.normalize_stage(str(event.get("stage") or ""))
    stage_status = str(event.get("stage_status") or events.default_stage_status(event_type))
    progress_current, progress_total = _progress_values(event, payload)
    slide_index, slide_version = _slide_values(event, payload)

    return GenerateDeckOutput(
        job_id=job_id,
        request_id=request_id,
        event_type=event_type,
        event_version=_safe_int(event.get("event_version")) or events.EVENT_VERSION,
        stage=stage,
        stage_status=stage_status,
        message=str(event.get("message") or ""),
        channel=str(event.get("channel") or ""),
        text_delta=str(event.get("text_delta") or ""),
        content=str(event.get("content") or ""),
        outline_markdown=outline_markdown,
        payload_json=payload_json,
        artifact_path=str(event.get("artifact_path") or ""),
        done=bool(event.get("done") or False),
        error=str(event.get("error") or ""),
        error_code=str(event.get("error_code") or ""),
        retryable=bool(event.get("retryable") or False),
        severity=str(event.get("severity") or events.default_severity(stage_status, payload)),
        action_required=str(
            event.get("action_required") or events.default_action_required(event_type, stage)
        ),
        progress_current=progress_current,
        progress_total=progress_total,
        slide_index=slide_index,
        slide_version=slide_version,
        sequence=sequence,
    )


def _interrupt_event(interrupts: Sequence[Any]) -> events.DeckEvent | None:
    if not interrupts:
        return None

    first = interrupts[0]
    value = getattr(first, "value", first)
    payload = dict(value) if isinstance(value, Mapping) else {"value": value}
    return events.interrupt_requested(payload)


def _progress_values(event: Mapping[str, Any], payload: Any) -> tuple[int, int]:
    progress = event.get("progress") if isinstance(event.get("progress"), Mapping) else payload
    if not isinstance(progress, Mapping):
        return 0, 0
    return _safe_int(progress.get("current")), _safe_int(progress.get("total"))


def _slide_values(event: Mapping[str, Any], payload: Any) -> tuple[int, int]:
    slide = event.get("slide") if isinstance(event.get("slide"), Mapping) else payload
    if not isinstance(slide, Mapping):
        return 0, 0
    return _safe_int(slide.get("index")), _safe_int(slide.get("version"))


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "STREAM_MODES",
    "STREAM_VERSION",
    "event_from_stream_part",
    "event_to_output",
    "stream_graph_outputs",
]
