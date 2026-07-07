"""Deck generation service."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from core.deck.contract import DeckNodeModelConfig, GenerateDeckInput, GenerateDeckOutput, ResumeDeckInput
from core.deck.graph import build_deck_graph
from core.provider.model_utils import provider_config_manager
from core.tools.document_parser import parse_uploaded_document

logger = logging.getLogger(__name__)


class DeckService:
    def __init__(self) -> None:
        self._checkpointer = InMemorySaver()

    def generate_deck(self, inp: GenerateDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = str(uuid.uuid4())

        try:
            description = inp.user_description.strip()
            if not description:
                raise ValueError("user_description is required")

            llms = _build_deck_llms(inp)
            graph = build_deck_graph(llms, _artifact_dir(), self._checkpointer)
            source_text = (inp.source_text or "").strip()
            uploaded_file_text = _parse_uploaded_file(inp)
            deck_title = _derive_deck_title(
                description,
                inp.source_file_filename,
                source_text,
                uploaded_file_text,
            )

            initial_state = {
                "request_id": request_id,
                "deck_title": deck_title,
                "audience": inp.audience,
                "goal": inp.goal,
                "page_count": inp.page_count,
                "language": inp.language,
                "style": inp.style,
                "source_text": source_text,
                "uploaded_file_name": inp.source_file_filename,
                "uploaded_file_text": uploaded_file_text,
                "user_description": description,
            }

            logger.info(
                "generate deck: request_id=%s user_id=%s models=%s",
                request_id,
                inp.user_id,
                _describe_model_assignment(inp),
            )

            yield from _stream_graph(
                request_id,
                graph,
                initial_state,
                _thread_config(request_id),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("generate deck failed: request_id=%s", request_id)
            yield GenerateDeckOutput(
                request_id=request_id,
                event_type="error",
                stage="error",
                message="Deck generation failed",
                done=True,
                error=str(exc),
                sequence=1,
            )

    def resume_deck(self, inp: ResumeDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = inp.request_id.strip()
        try:
            if not request_id:
                raise ValueError("request_id is required")

            llms = _build_deck_llms(inp)
            graph = build_deck_graph(llms, _artifact_dir(), self._checkpointer)
            resume_payload = {
                "action": inp.action or "continue",
                "outline": _decode_outline_json(inp.outline_json),
            }

            logger.info(
                "resume deck: request_id=%s user_id=%s models=%s",
                request_id,
                inp.user_id,
                _describe_model_assignment(inp),
            )
            yield from _stream_graph(
                request_id,
                graph,
                Command(resume=resume_payload),
                _thread_config(request_id),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("resume deck failed: request_id=%s", request_id)
            yield GenerateDeckOutput(
                request_id=request_id,
                event_type="error",
                stage="error",
                message="Deck resume failed",
                done=True,
                error=str(exc),
                sequence=1,
            )


def _thread_config(request_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": request_id}}


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
    interrupt_id = getattr(first, "id", "")
    payload = value if isinstance(value, dict) else {"value": value}
    payload = dict(payload)
    payload.setdefault("request_id", request_id)
    if interrupt_id:
        payload["interrupt_id"] = interrupt_id
    return {
        "event_type": "interrupt",
        "stage": str(payload.get("stage") or "outline_review"),
        "message": str(payload.get("message") or "Waiting for user input"),
        "payload": payload,
        "done": True,
    }


def _custom_event_to_output(request_id: str, sequence: int, data: dict[str, Any]) -> GenerateDeckOutput:
    payload = data.get("payload")
    payload_json = ""
    if payload is not None:
        payload_json = json.dumps(payload, ensure_ascii=False)

    return GenerateDeckOutput(
        request_id=request_id,
        event_type=str(data.get("event_type") or "progress"),
        stage=str(data.get("stage") or ""),
        message=str(data.get("message") or ""),
        payload_json=payload_json,
        artifact_path=str(data.get("artifact_path") or ""),
        done=bool(data.get("done") or False),
        error=str(data.get("error") or ""),
        sequence=sequence,
    )


def _decode_outline_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("outline_json must be a JSON object")
    return decoded


def _decode_provider_options(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}

    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("provider_options_json must be a JSON object")
    return decoded


def _parse_uploaded_file(inp: GenerateDeckInput) -> str:
    if not inp.source_file:
        return ""
    return parse_uploaded_document(inp.source_file_filename, inp.source_file)


def _derive_deck_title(description: str, file_name: str, source_text: str, uploaded_file_text: str) -> str:
    for candidate in (description, file_name, source_text, uploaded_file_text):
        candidate = (candidate or "").strip()
        if candidate:
            return _compact_title(candidate)
    return "Untitled presentation"


def _compact_title(value: str) -> str:
    first_line = value.splitlines()[0].strip()
    first_sentence = re_split_sentence(first_line)[0].strip()
    title = first_sentence or first_line
    if len(title) > 80:
        return title[:77].rstrip() + "..."
    return title


def re_split_sentence(value: str) -> list[str]:
    return re.split(r"[。.!?！？]", value, maxsplit=1)


def _build_deck_llms(inp: GenerateDeckInput) -> Any:
    if not inp.node_model_configs:
        raise ValueError("node_model_configs is required")

    clients: dict[str, Any] = {}
    for config in inp.node_model_configs:
        node_key = config.node_key.strip().lower()
        if not node_key:
            raise ValueError("node_model_configs[].node_key is required")
        clients[node_key] = _build_chat_client_from_node(config)

    return clients


def _build_chat_client_from_node(config: DeckNodeModelConfig) -> Any:
    return _build_chat_client(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        provider_options_json=config.provider_options_json,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )


def _build_chat_client(
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    provider_options_json: str,
    temperature: float,
    max_tokens: int,
) -> Any:
    return provider_config_manager.build_chat_client(
        provider_name=provider.strip().lower(),
        model_name=model.strip(),
        api_key=api_key.strip(),
        base_url=base_url.strip(),
        temperature=temperature,
        max_tokens=max(int(max_tokens or 0), 2048),
        provider_options=_decode_provider_options(provider_options_json),
    )


def _describe_model_assignment(inp: GenerateDeckInput) -> str:
    parts = [
        f"{item.node_key}={item.provider}/{item.model}"
        for item in inp.node_model_configs
    ]
    return ", ".join(parts)


def _artifact_dir() -> Path:
    configured = os.getenv("DECK_ARTIFACTS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / "artifacts" / "decks").resolve()
