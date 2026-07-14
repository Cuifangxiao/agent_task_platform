"""Deck generation service."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from typing import Any

from langgraph.types import Command

import core.deck.events as events
from core.deck.checkpoint import PersistentSQLiteSaver
from core.deck.contract import GenerateDeckInput, GenerateDeckOutput, ResumeDeckInput
from core.deck.errors import DeckError
from core.deck.model_config import build_deck_llms, describe_model_assignment
from core.deck.paths import DeckProjectPaths, artifact_dir
from core.deck.pipeline import build_deck_graph
from core.deck.pipeline.regenerate import regenerate_slide_from_artifacts
from core.deck.rendering.browser import BrowserSession
from core.deck.sources import (
    derive_deck_title,
    materialize_source_images,
    parse_uploaded_files_with_provenance,
    persist_normalized_source_bundle,
)
from core.deck.streaming import stream_graph_outputs

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
            project = DeckProjectPaths.for_request(artifacts_root, request_id).ensure()
            graph = build_deck_graph(llms, artifacts_root, self._checkpointer, browser_session)
            parse_started_at = time.perf_counter()
            source_images = materialize_source_images(inp, project.root)
            uploaded_file_names, uploaded_file_text, source_chunks = parse_uploaded_files_with_provenance(inp)
            persist_normalized_source_bundle(
                project.root,
                source_names=uploaded_file_names,
                normalized_text=uploaded_file_text,
                source_chunks=source_chunks,
            )
            parse_duration_ms = round((time.perf_counter() - parse_started_at) * 1000)
            deck_title = derive_deck_title(
                description,
                uploaded_file_names,
                uploaded_file_text,
            )

            initial_state = {
                "request_id": request_id,
                "project_dir": str(project.root),
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
                yield from stream_graph_outputs(
                    job_id,
                    request_id,
                    graph,
                    initial_state,
                    _thread_config(request_id),
                    initial_sequence=1,
                )
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
                yield from stream_graph_outputs(
                    job_id,
                    request_id,
                    graph,
                    Command(resume=resume_payload),
                    _thread_config(request_id),
                )
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
            stage=events.normalize_stage(exc.stage),
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
