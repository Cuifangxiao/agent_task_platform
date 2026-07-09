"""Deck generation service."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from core.deck.contract import GenerateDeckInput, GenerateDeckOutput, ResumeDeckInput
from core.deck.errors import DeckError
from core.deck.model_config import build_deck_llms, describe_model_assignment
from core.deck.paths import artifact_dir
from core.deck.pipeline import build_deck_graph
from core.deck.pipeline.regenerate import regenerate_slide_from_artifacts
from core.deck.rendering.browser import BrowserSession
from core.deck.sources import derive_deck_title, parse_uploaded_files

logger = logging.getLogger(__name__)
PAYLOAD_JSON_MAX_BYTES = 64_000


class DeckService:
    def __init__(self) -> None:
        self._checkpointer = InMemorySaver()

    def generate_deck(self, inp: GenerateDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = str(uuid.uuid4())

        try:
            description = inp.user_description.strip()
            if not description:
                raise ValueError("user_description is required")

            llms = build_deck_llms(inp)
            browser_session = BrowserSession()
            graph = build_deck_graph(llms, artifact_dir(), self._checkpointer, browser_session)
            uploaded_file_names, uploaded_file_text = parse_uploaded_files(inp)
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
                "user_description": description,
            }

            logger.info(
                "generate deck: request_id=%s user_id=%s models=%s",
                request_id,
                inp.user_id,
                describe_model_assignment(inp),
            )

            try:
                yield from _stream_graph(request_id, graph, initial_state, _thread_config(request_id))
            finally:
                browser_session.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("generate deck failed: request_id=%s", request_id)
            yield _error_output(request_id, "Deck generation failed", exc)

    def resume_deck(self, inp: ResumeDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = inp.request_id.strip()
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
                yield from _stream_graph(request_id, graph, Command(resume=resume_payload), _thread_config(request_id))
            finally:
                browser_session.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("resume deck failed: request_id=%s", request_id)
            yield _error_output(request_id, "Deck resume failed", exc)


def _thread_config(request_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": request_id}}


def _error_output(request_id: str, message: str, exc: Exception) -> GenerateDeckOutput:
    if isinstance(exc, DeckError):
        return GenerateDeckOutput(request_id=request_id, event_type="error", stage=exc.stage, message=str(exc), done=True, error=str(exc), sequence=1)
    return GenerateDeckOutput(request_id=request_id, event_type="error", stage="error", message=message, done=True, error=str(exc), sequence=1)


def _stream_graph(request_id: str, graph: Any, stream_input: Any, config: dict[str, Any]) -> Iterator[GenerateDeckOutput]:
    sequence = 0
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
        output = _custom_event_to_output(request_id, sequence, data)
        if output.done:
            terminal_sent = True
        yield output

    if not terminal_sent:
        sequence += 1
        yield GenerateDeckOutput(
            request_id=request_id,
            event_type="done",
            stage="done",
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


def _custom_event_to_output(request_id: str, sequence: int, data: dict[str, Any]) -> GenerateDeckOutput:
    payload = data.get("payload")
    payload_json = ""
    if payload is not None:
        payload_json = json.dumps(payload, ensure_ascii=False)
    outline_markdown = str(data.get("outline_markdown") or "")
    if not outline_markdown and isinstance(payload, dict):
        outline_markdown = str(payload.get("outline_markdown") or "")

    return GenerateDeckOutput(
        request_id=request_id,
        event_type=str(data.get("event_type") or "progress"),
        stage=str(data.get("stage") or ""),
        message=str(data.get("message") or ""),
        channel=str(data.get("channel") or ""),
        text_delta=str(data.get("text_delta") or ""),
        content=str(data.get("content") or ""),
        outline_markdown=outline_markdown,
        payload_json=payload_json,
        artifact_path=str(data.get("artifact_path") or ""),
        done=bool(data.get("done") or False),
        error=str(data.get("error") or ""),
        sequence=sequence,
    )


def _normalize_resume_action(inp: ResumeDeckInput) -> str:
    action = (inp.action or "").strip().lower()
    if not action:
        return "approve_outline"
    if action in {"continue", "approve", "approved"}:
        return "approve_outline"
    if action in {"approve_outline", "choose_style", "regenerate_slide"}:
        return action
    raise ValueError(f"unsupported resume action: {inp.action}")


def _build_resume_payload(inp: ResumeDeckInput, action: str) -> dict[str, Any]:
    payload = _decode_payload_json(inp.payload_json)
    if action == "approve_outline":
        outline_markdown = inp.outline_markdown.strip()
        if not outline_markdown:
            raise ValueError("outline_markdown is required")
        return {"action": action, "outline_markdown": outline_markdown}
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
