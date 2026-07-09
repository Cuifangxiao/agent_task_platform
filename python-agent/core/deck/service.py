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

from core.deck.contract import DeckNodeModelConfig, DeckSourceFile, GenerateDeckInput, GenerateDeckOutput, ResumeDeckInput
from core.deck.browser import BrowserRenderer
from core.deck.exporter import DeckExporter
from core.deck.graph import build_deck_graph
from core.deck.html_renderer import HTMLSlideRenderer
from core.deck.spec import DeckSpec
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
            uploaded_file_names, uploaded_file_text = _parse_uploaded_files(inp)
            deck_title = _derive_deck_title(
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

            action = _normalize_resume_action(inp)
            if action == "regenerate_slide":
                yield from _regenerate_slide_from_artifacts(request_id, inp)
                return

            llms = _build_deck_llms(inp)
            graph = build_deck_graph(llms, _artifact_dir(), self._checkpointer)
            resume_payload = _build_resume_payload(inp, action)

            logger.info(
                "resume deck: request_id=%s user_id=%s action=%s models=%s",
                request_id,
                inp.user_id,
                action,
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


def _decode_provider_options(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}

    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("provider_options_json must be a JSON object")
    return decoded


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
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("payload_json must be a JSON object")
    return decoded


def _regenerate_slide_from_artifacts(request_id: str, inp: ResumeDeckInput) -> Iterator[GenerateDeckOutput]:
    payload = _decode_payload_json(inp.payload_json)
    try:
        index = int(payload.get("index") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload_json.index is required for regenerate_slide") from exc
    feedback = str(payload.get("feedback") or "").strip()
    if index <= 0:
        raise ValueError("payload_json.index is required for regenerate_slide")

    output_root = _artifact_dir()
    request_dir = output_root / request_id
    spec_path = request_dir / "spec.json"
    if not spec_path.exists():
        raise ValueError("deck spec artifact is missing; regenerate_slide requires an exported deck")
    deck = DeckSpec.from_dict(json.loads(spec_path.read_text(encoding="utf-8")))
    slide = next((item for item in deck.slides if item.index == index), None)
    if slide is None:
        raise ValueError(f"slide {index} not found in deck spec")

    if feedback:
        slide.notes = (slide.notes + "\n\nFeedback: " + feedback).strip()
        if slide.component == "content-basic":
            slide.content["body"] = feedback
        elif slide.component in {"cover", "section", "end"}:
            slide.content["subtitle"] = feedback
        elif isinstance(slide.content.get("items"), list) and slide.content["items"]:
            slide.content["items"][0] = feedback

    html_renderer = HTMLSlideRenderer()
    browser = BrowserRenderer(output_root)
    exporter = DeckExporter(output_root)

    yield GenerateDeckOutput(
        request_id=request_id,
        event_type="repair_progress",
        stage="repair",
        message=f"Regenerating slide {index}",
        payload_json=json.dumps({"index": index, "round": 1, "kind": "user_feedback"}, ensure_ascii=False),
        sequence=1,
    )
    artifact = browser.render_slide(request_id, slide, html_renderer.render(deck, slide), version=_next_slide_version(request_dir, index), theme=deck.theme)
    yield GenerateDeckOutput(
        request_id=request_id,
        event_type="slide_preview",
        stage="design_slides",
        message=f"Slide {index} preview updated",
        payload_json=json.dumps(
            {
                "index": index,
                "png": artifact.png_rel,
                "html": artifact.html_rel,
                "thumb_b64": artifact.thumb_b64,
                "version": artifact.version,
                "status": "ready" if not artifact.issues else "warning",
            },
            ensure_ascii=False,
        ),
        sequence=2,
    )
    result = exporter.export(request_id, deck)
    yield GenerateDeckOutput(
        request_id=request_id,
        event_type="artifact",
        stage="export",
        message="PPTX file ready",
        artifact_path=str(result.path),
        payload_json=json.dumps(
            {"request_id": request_id, "filename": result.path.name, "path": str(result.path), "relative_path": "deck.pptx", "warnings": result.warnings},
            ensure_ascii=False,
        ),
        sequence=3,
    )
    yield GenerateDeckOutput(
        request_id=request_id,
        event_type="done",
        stage="done",
        message="Deck regeneration complete",
        done=True,
        sequence=4,
    )


def _next_slide_version(request_dir: Path, index: int) -> int:
    slides_dir = request_dir / "slides"
    version = 1
    for path in slides_dir.glob(f"{index}_v*.html"):
        match = re.search(r"_v(\d+)\.html$", path.name)
        if match:
            version = max(version, int(match.group(1)) + 1)
    return max(2, version)


def _parse_uploaded_files(inp: GenerateDeckInput, *, max_total_chars: int = 60000, max_chars_per_file: int = 20000) -> tuple[str, str]:
    source_files = _collect_source_files(inp)
    if not source_files:
        return "", ""

    sections: list[str] = []
    remaining = max_total_chars
    for index, source_file in enumerate(source_files):
        filename = source_file.filename.strip() or "uploaded-file"

        if remaining <= 0:
            omitted_count = len(source_files) - index
            sections.append(f"[{omitted_count} additional attachment(s) omitted because the context budget was reached.]")
            break

        text = parse_uploaded_document(
            filename,
            source_file.content,
            max_chars=min(max_chars_per_file, remaining),
        ).strip()
        if not text:
            continue

        sections.append(f"### {filename}\n{text}")
        remaining -= len(text)

    return _join_source_file_names(source_files), "\n\n".join(sections)


def _collect_source_files(inp: GenerateDeckInput) -> list[DeckSourceFile]:
    source_files = [
        DeckSourceFile(filename=item.filename.strip(), content=item.content)
        for item in inp.source_files
        if item.content
    ]
    if not source_files and inp.source_file:
        source_files.append(
            DeckSourceFile(
                filename=(inp.source_file_filename or "uploaded-file").strip(),
                content=inp.source_file,
            )
        )
    return source_files


def _join_source_file_names(source_files: list[DeckSourceFile], *, max_chars: int = 2000) -> str:
    names: list[str] = []
    used = 0
    for index, source_file in enumerate(source_files):
        name = source_file.filename.strip() or "uploaded-file"
        next_used = used + len(name) + (2 if names else 0)
        if next_used > max_chars:
            names.append(f"... (+{len(source_files) - index} more)")
            break
        names.append(name)
        used = next_used
    return ", ".join(names)


def _derive_deck_title(description: str, file_name: str, uploaded_file_text: str) -> str:
    for candidate in (description, file_name, uploaded_file_text):
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
