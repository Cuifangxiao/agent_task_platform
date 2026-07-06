"""Deck generation service."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from core.deck.contract import GenerateDeckInput, GenerateDeckOutput
from core.deck.graph import build_deck_graph
from core.provider.model_utils import provider_config_manager

logger = logging.getLogger(__name__)


class DeckService:
    def generate_deck(self, inp: GenerateDeckInput) -> Iterator[GenerateDeckOutput]:
        request_id = str(uuid.uuid4())
        sequence = 0

        try:
            topic = inp.topic.strip()
            if not topic:
                raise ValueError("topic is required")

            client = provider_config_manager.build_chat_client(
                provider_name=inp.provider.strip().lower(),
                model_name=inp.model.strip(),
                api_key=inp.api_key.strip(),
                base_url=inp.base_url.strip(),
                temperature=inp.temperature,
                max_tokens=max(int(inp.max_tokens or 0), 2048),
                provider_options=_decode_provider_options(inp.provider_options_json),
            )
            graph = build_deck_graph(client, _artifact_dir())

            initial_state = {
                "request_id": request_id,
                "topic": topic,
                "audience": inp.audience,
                "goal": inp.goal,
                "page_count": inp.page_count,
                "language": inp.language,
                "style": inp.style,
                "source_text": inp.source_text,
            }

            logger.info(
                "generate deck: request_id=%s user_id=%s provider=%s model=%s",
                request_id,
                inp.user_id,
                inp.provider,
                inp.model,
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


def _artifact_dir() -> Path:
    configured = os.getenv("DECK_ARTIFACTS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / "artifacts" / "decks").resolve()

