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

from core.deck.contract import DeckNodeModelConfig, GenerateDeckInput, GenerateDeckOutput
from core.deck.graph import build_deck_graph
from core.provider.model_utils import provider_config_manager
from core.tools.pdf_parser import parse_pdf_text

logger = logging.getLogger(__name__)


class DeckService:
    def generate_deck(self, inp: GenerateDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = str(uuid.uuid4())
        sequence = 0

        try:
            description = inp.user_description.strip()
            if not description:
                raise ValueError("user_description is required")

            llms = _build_deck_llms(inp)
            graph = build_deck_graph(llms, _artifact_dir())
            parsed_pdf_text = _parse_uploaded_pdf(inp)
            source_text = _merge_source_text(inp.source_text, parsed_pdf_text, inp.source_pdf_filename)
            deck_title = _derive_deck_title(description, source_text, inp.source_pdf_filename)

            initial_state = {
                "request_id": request_id,
                "deck_title": deck_title,
                "audience": inp.audience,
                "goal": inp.goal,
                "page_count": inp.page_count,
                "language": inp.language,
                "style": inp.style,
                "source_text": source_text,
                "user_description": description,
            }

            logger.info(
                "generate deck: request_id=%s user_id=%s models=%s",
                request_id,
                inp.user_id,
                _describe_model_assignment(inp),
            )

            for part in graph.stream(
                initial_state,
                stream_mode=["custom", "updates"],
                version="v2",
            ):
                if part.get("type") != "custom":
                    continue

                data = part.get("data")
                if not isinstance(data, dict):
                    continue

                sequence += 1
                yield _custom_event_to_output(request_id, sequence, data)

            sequence += 1
            yield GenerateDeckOutput(
                request_id=request_id,
                event_type="done",
                stage="done",
                message="Deck generation complete",
                done=True,
                sequence=sequence,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("generate deck failed: request_id=%s", request_id)
            sequence += 1
            yield GenerateDeckOutput(
                request_id=request_id,
                event_type="error",
                stage="error",
                message="Deck generation failed",
                done=True,
                error=str(exc),
                sequence=sequence,
            )


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


def _decode_provider_options(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}

    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("provider_options_json must be a JSON object")
    return decoded


def _parse_uploaded_pdf(inp: GenerateDeckInput) -> str:
    if not inp.source_pdf:
        return ""
    return parse_pdf_text(inp.source_pdf)


def _merge_source_text(source_text: str, pdf_text: str, pdf_filename: str) -> str:
    parts: list[str] = []
    source_text = (source_text or "").strip()
    if source_text:
        parts.append("User pasted source text:\n" + source_text)
    if pdf_text:
        label = (pdf_filename or "uploaded.pdf").strip()
        parts.append(f"Parsed PDF text from {label}:\n{pdf_text}")
    return "\n\n".join(parts)


def _derive_deck_title(description: str, source_text: str, pdf_filename: str) -> str:
    for candidate in (description, pdf_filename, source_text):
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
