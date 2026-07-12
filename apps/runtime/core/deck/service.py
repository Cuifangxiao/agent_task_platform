"""Deck generation service."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from typing import Any

from langgraph.types import Command

from core.deck.checkpoint import PersistentSQLiteSaver
from core.deck.contract import GenerateDeckInput, GenerateDeckOutput, ResumeDeckInput
from core.deck.errors import DeckError
from core.deck.model_config import build_deck_llms, describe_model_assignment
from core.deck.paths import artifact_dir
from core.deck.pipeline import build_deck_graph
from core.deck.pipeline.regenerate import regenerate_slide_from_artifacts
from core.deck.rendering.browser import BrowserSession
from core.deck.sources import derive_deck_title, materialize_source_images, parse_uploaded_files_with_provenance

logger = logging.getLogger(__name__)
PAYLOAD_JSON_MAX_BYTES = 64_000


class DeckService:
    def __init__(self) -> None:
        self._checkpointer = PersistentSQLiteSaver(artifact_dir().parent / "deck-checkpoints.sqlite3")

    def generate_deck(self, inp: GenerateDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = str(uuid.uuid4())
        job_id = inp.job_id.strip()

        try:
            description = inp.user_description.strip()
            if not description:
                raise ValueError("user_description is required")

            llms = build_deck_llms(inp)
            browser_session = BrowserSession()
            artifacts_root = artifact_dir()
            graph = build_deck_graph(llms, artifacts_root, self._checkpointer, browser_session)
            parse_started_at = time.perf_counter()
            source_images = materialize_source_images(inp, artifacts_root / request_id)
            uploaded_file_names, uploaded_file_text, source_chunks = parse_uploaded_files_with_provenance(inp)
            parse_duration_ms = round((time.perf_counter() - parse_started_at) * 1000)
            deck_title = derive_deck_title(
                description,
                uploaded_file_names,
                uploaded_file_text,
            )

            initial_state = {
                "request_id": request_id,
                "deck_title": deck_title,
                "uploaded_file_name": uploaded_file_names,
                "uploaded_file_text": uploaded_file_text,
                "source_chunks": source_chunks,
                "source_images": source_images,
                "generation_mode": inp.generation_mode,
                "user_description": description,
            }

            logger.info(
                "generate deck: request_id=%s user_id=%s models=%s",
                request_id,
                inp.user_id,
                describe_model_assignment(inp),
            )

            try:
                yield GenerateDeckOutput(
                    job_id=job_id,
                    request_id=request_id,
                    event_type="stage_progress",
                    stage="brief",
                    stage_status="done",
                    message="Attachment parsing complete" if uploaded_file_names else "No attachments to parse",
                    payload_json=json.dumps(
                        {
                            "phase": "parse",
                            "source_files": len(inp.source_files),
                            "source_chunks": len(source_chunks),
                            "source_images": len(source_images),
                            "metrics": {"duration_ms": parse_duration_ms},
                        },
                        ensure_ascii=False,
                    ),
                    sequence=1,
                )
                yield from _stream_graph(job_id, request_id, graph, initial_state, _thread_config(request_id), initial_sequence=1)
            finally:
                browser_session.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("generate deck failed: request_id=%s", request_id)
            yield _error_output(job_id, request_id, "Deck generation failed", exc)

    def resume_deck(self, inp: ResumeDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = inp.request_id.strip()
        job_id = inp.job_id.strip()
        try:
            if not request_id:
                raise ValueError("request_id is required")

            action = _normalize_resume_action(inp)
            if action == "regenerate_slide":
                yield from regenerate_slide_from_artifacts(request_id, inp)
                return

            llms = build_deck_llms(inp)
            browser_session = BrowserSession()
            graph = build_deck_graph(llms, artifact_dir(), self._checkpointer, browser_session)
            resume_payload = _build_resume_payload(inp, action)

            logger.info(
                "resume deck: request_id=%s user_id=%s action=%s models=%s",
                request_id,
                inp.user_id,
                action,
                describe_model_assignment(inp),
            )
            try:
                yield from _stream_graph(job_id, request_id, graph, Command(resume=resume_payload), _thread_config(request_id))
            finally:
                browser_session.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("resume deck failed: request_id=%s", request_id)
            yield _error_output(job_id, request_id, "Deck resume failed", exc)


def _thread_config(request_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": request_id}}


def _error_output(job_id: str, request_id: str, message: str, exc: Exception) -> GenerateDeckOutput:
    if isinstance(exc, DeckError):
        return GenerateDeckOutput(
            job_id=job_id,
            request_id=request_id,
            event_type="error",
            stage=_normalize_stage(exc.stage),
            stage_status="error",
            severity="error",
            message=str(exc),
            done=True,
            error=str(exc),
            error_code=type(exc).__name__.lower(),
            retryable=False,
            sequence=1,
        )
    return GenerateDeckOutput(
        job_id=job_id,
        request_id=request_id,
        event_type="error",
        stage="brief",
        stage_status="error",
        severity="error",
        message=message,
        done=True,
        error=str(exc),
        error_code="deck_runtime_error",
        retryable=True,
        sequence=1,
    )


def _stream_graph(
    job_id: str,
    request_id: str,
    graph: Any,
    stream_input: Any,
    config: dict[str, Any],
    *,
    initial_sequence: int = 0,
) -> Iterator[GenerateDeckOutput]:
    sequence = initial_sequence
    terminal_sent = False
    for part in graph.stream(
        stream_input,
        config=config,
        stream_mode=["custom", "updates"],
    ):
        data = _event_from_graph_part(request_id, part)
        if data is None:
            continue

        sequence += 1
        output = _custom_event_to_output(job_id, request_id, sequence, data)
        if output.done:
            terminal_sent = True
        yield output

    if not terminal_sent:
        sequence += 1
        yield GenerateDeckOutput(
            job_id=job_id,
            request_id=request_id,
            event_type="done",
            stage="export",
            stage_status="done",
            message="Deck generation complete",
            done=True,
            sequence=sequence,
        )


def _event_from_graph_part(request_id: str, part: Any) -> dict[str, Any] | None:
    mode = ""
    data: Any = None
    if isinstance(part, tuple) and len(part) == 2:
        mode = str(part[0])
        data = part[1]
    elif isinstance(part, dict):
        mode = str(part.get("type") or "")
        data = part.get("data", part)

    if mode == "custom" and isinstance(data, dict):
        return data
    if mode == "updates" and isinstance(data, dict):
        return _interrupt_event_from_update(request_id, data)
    if isinstance(data, dict) and "__interrupt__" in data:
        return _interrupt_event_from_update(request_id, data)
    return None


def _interrupt_event_from_update(request_id: str, update: dict[str, Any]) -> dict[str, Any] | None:
    raw_interrupts = update.get("__interrupt__")
    if not raw_interrupts:
        return None
    first = raw_interrupts[0]
    value = getattr(first, "value", first)
    raw_payload = value if isinstance(value, dict) else {"value": value}
    raw_payload = dict(raw_payload)
    stage = str(raw_payload.get("stage") or "outline_review")
    outline_markdown = str(raw_payload.get("outline_markdown") or "")
    payload = raw_payload
    message = "Outline is ready for review"
    if stage == "style_review":
        message = "Style options are ready for review"
    return {
        "event_type": "interrupt",
        "stage": stage,
        "message": message,
        "outline_markdown": outline_markdown,
        "payload": payload,
        "done": True,
    }


def _custom_event_to_output(job_id: str, request_id: str, sequence: int, data: dict[str, Any]) -> GenerateDeckOutput:
    payload = data.get("payload")
    payload_json = ""
    if payload is not None:
        payload_json = json.dumps(payload, ensure_ascii=False)
    outline_markdown = str(data.get("outline_markdown") or "")
    if not outline_markdown and isinstance(payload, dict):
        outline_markdown = str(payload.get("outline_markdown") or "")

    stage = _normalize_stage(str(data.get("stage") or ""))
    event_type = str(data.get("event_type") or "progress")
    stage_status = str(data.get("stage_status") or _default_stage_status(event_type))
    progress_current, progress_total = _progress_values(data, payload)
    slide_index, slide_version = _slide_values(data, payload)
    severity = str(data.get("severity") or _default_severity(stage_status, payload))
    action_required = str(data.get("action_required") or _default_action_required(event_type, stage))
    return GenerateDeckOutput(
        job_id=job_id,
        request_id=request_id,
        event_type=event_type,
        event_version=1,
        stage=stage,
        stage_status=stage_status,
        message=str(data.get("message") or ""),
        channel=str(data.get("channel") or ""),
        text_delta=str(data.get("text_delta") or ""),
        content=str(data.get("content") or ""),
        outline_markdown=outline_markdown,
        payload_json=payload_json,
        artifact_path=str(data.get("artifact_path") or ""),
        done=bool(data.get("done") or False),
        error=str(data.get("error") or ""),
        error_code=str(data.get("error_code") or ""),
        retryable=bool(data.get("retryable") or False),
        severity=severity,
        action_required=action_required,
        progress_current=progress_current,
        progress_total=progress_total,
        slide_index=slide_index,
        slide_version=slide_version,
        sequence=sequence,
    )


def _normalize_stage(stage: str) -> str:
    aliases = {
        "slides": "writer",
        "write_slides": "writer",
        "design_slides": "designer",
        "render": "designer",
        "review": "review_vlm",
        "done": "export",
    }
    return aliases.get(stage.strip(), stage.strip())


def _default_stage_status(event_type: str) -> str:
    if event_type == "interrupt":
        return "waiting"
    if event_type in {"artifact", "done"}:
        return "done"
    if event_type == "error":
        return "error"
    return "running"


def _default_severity(stage_status: str, payload: Any) -> str:
    if stage_status == "error":
        return "error"
    if isinstance(payload, dict) and (payload.get("warning") or payload.get("warnings") or payload.get("status") == "warning"):
        return "warning"
    return "info"


def _default_action_required(event_type: str, stage: str) -> str:
    if event_type != "interrupt":
        return ""
    return "choose_style" if stage == "style_review" else "approve_outline"


def _progress_values(data: dict[str, Any], payload: Any) -> tuple[int, int]:
    progress = data.get("progress") if isinstance(data.get("progress"), dict) else payload
    if not isinstance(progress, dict):
        return 0, 0
    return _safe_int(progress.get("current")), _safe_int(progress.get("total"))


def _slide_values(data: dict[str, Any], payload: Any) -> tuple[int, int]:
    slide = data.get("slide") if isinstance(data.get("slide"), dict) else payload
    if not isinstance(slide, dict):
        return 0, 0
    return _safe_int(slide.get("index")), _safe_int(slide.get("version"))


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_resume_action(inp: ResumeDeckInput) -> str:
    action = (inp.action or "").strip().lower()
    if not action:
        return "approve_outline"
    if action in {"continue", "approve", "approved"}:
        return "approve_outline"
    if action in {"approve_outline", "regenerate_outline", "choose_style", "regenerate_slide"}:
        return action
    raise ValueError(f"unsupported resume action: {inp.action}")


def _build_resume_payload(inp: ResumeDeckInput, action: str) -> dict[str, Any]:
    payload = _decode_payload_json(inp.payload_json)
    if action == "approve_outline":
        outline_markdown = inp.outline_markdown.strip()
        if not outline_markdown:
            raise ValueError("outline_markdown is required")
        return {"action": action, "outline_markdown": outline_markdown}
    if action == "regenerate_outline":
        return {"action": action, "feedback": str(payload.get("feedback") or "").strip(), "payload": payload}
    if action == "choose_style":
        style_id = str(payload.get("style_id") or payload.get("styleId") or "").strip()
        feedback = str(payload.get("feedback") or "").strip()
        return {"action": action, "style_id": style_id, "feedback": feedback, "payload": payload}
    return {"action": action, "payload": payload}


def _decode_payload_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if len(raw.encode("utf-8")) > PAYLOAD_JSON_MAX_BYTES:
        raise ValueError("payload_json exceeds 64KB")
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("payload_json must be a JSON object")
    return decoded
