"""LangGraph workflow for deck generation."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from core.deck.renderer import DeckRenderer


PROMPT_DIR = Path(__file__).with_name("prompts")


class DeckGraphState(TypedDict, total=False):
    request_id: str
    deck_title: str
    user_description: str
    uploaded_file_name: str
    uploaded_file_text: str
    outline_markdown: str
    plan: dict[str, Any]
    slides: list[dict[str, Any]]
    review: dict[str, Any]
    artifact_path: str


def build_deck_graph(llms: Any, output_dir: Path, checkpointer: Any = None):
    renderer = DeckRenderer(output_dir)
    llm_registry = _normalize_llm_registry(llms)

    def normalize_brief(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        writer(
            {
                "event_type": "progress",
                "stage": "brief",
                "message": "Normalizing deck brief",
            }
        )

        deck_title = str(state.get("deck_title") or "").strip() or "Untitled presentation"
        return {
            "deck_title": _truncate(deck_title, 120),
            "user_description": _truncate(str(state.get("user_description") or "").strip(), 5000),
            "uploaded_file_name": str(state.get("uploaded_file_name") or "").strip(),
            "uploaded_file_text": _truncate(str(state.get("uploaded_file_text") or "").strip(), 20000),
        }

    def generate_outline(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        writer(
            {
                "event_type": "progress",
                "stage": "outline",
                "message": "Generating deck outline",
            }
        )

        parts: list[str] = []
        messages = [
            ("system", _load_node_prompt("outline")),
            ("human", _outline_prompt(state)),
        ]
        for delta in _stream_llm_text(_llm_for_node(llm_registry, "outline"), messages):
            if not delta:
                continue
            parts.append(delta)
            writer(
                {
                    "event_type": "text_delta",
                    "stage": "outline",
                    "message": "Streaming outline markdown",
                    "channel": "outline_markdown",
                    "text_delta": delta,
                }
            )

        outline_markdown = _normalize_outline_markdown("".join(parts), state)
        plan = _outline_markdown_to_plan(outline_markdown, state)
        writer(
            {
                "event_type": "outline",
                "stage": "outline",
                "message": "Outline ready",
                "content": outline_markdown,
                "outline_markdown": outline_markdown,
                "payload": {"outline_markdown": outline_markdown},
            }
        )
        return {"outline_markdown": outline_markdown, "plan": plan}

    def generate_slides(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        writer(
            {
                "event_type": "progress",
                "stage": "slides",
                "message": "Generating slide specs",
            }
        )

        slides = _normalize_slides(
            _invoke_json(
                _llm_for_node(llm_registry, "slides"),
                system=_load_node_prompt("slides"),
                user=_slides_prompt(state),
            ),
            state,
        )
        for slide in slides:
            writer(
                {
                    "event_type": "slide",
                    "stage": "slides",
                    "message": f"Slide {slide['index']} ready",
                    "payload": slide,
                }
            )
        return {"slides": slides}

    def review_outline(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        outline_markdown = str(state.get("outline_markdown") or "").strip()
        writer(
            {
                "event_type": "progress",
                "stage": "outline_review",
                "message": "Waiting for outline confirmation",
            }
        )
        decision = interrupt({"outline_markdown": outline_markdown})

        if isinstance(decision, dict):
            action = str(decision.get("action") or "continue").strip().lower()
            if action in {"continue", "approve", "approved"}:
                edited_outline = decision.get("outline_markdown") or decision.get("content")
                if isinstance(edited_outline, str) and edited_outline.strip():
                    edited_outline = _normalize_outline_markdown(edited_outline, state)
                    return {
                        "outline_markdown": edited_outline,
                        "plan": _outline_markdown_to_plan(edited_outline, state),
                    }
        return {}

    def review_deck(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        review_llm = _explicit_llm_for_node(llm_registry, "review")
        if review_llm is None:
            review = _review(state.get("slides") or [], _target_slide_count(state))
        else:
            review = _normalize_review(
                _invoke_json(
                    review_llm,
                    system=_load_node_prompt("review"),
                    user=_review_prompt(state),
                )
            )
        writer(
            {
                "event_type": "review",
                "stage": "review",
                "message": "Deck review complete",
                "payload": review,
            }
        )
        return {"review": review}

    def render_deck(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        writer(
            {
                "event_type": "progress",
                "stage": "render",
                "message": "Rendering PPTX file",
            }
        )

        artifact_path = renderer.render(
            str(state["request_id"]),
            state.get("plan") or {},
            state.get("slides") or [],
        )
        writer(
            {
                "event_type": "artifact",
                "stage": "render",
                "message": "PPTX file ready",
                "artifact_path": str(artifact_path),
                "payload": {
                    "request_id": state["request_id"],
                    "filename": artifact_path.name,
                },
            }
        )
        return {"artifact_path": str(artifact_path)}

    graph = StateGraph(DeckGraphState)
    graph.add_node("brief", normalize_brief)
    graph.add_node("outline", generate_outline)
    graph.add_node("outline_review", review_outline)
    graph.add_node("slides", generate_slides)
    graph.add_node("review", review_deck)
    graph.add_node("render", render_deck)
    graph.add_edge(START, "brief")
    graph.add_edge("brief", "outline")
    graph.add_edge("outline", "outline_review")
    graph.add_edge("outline_review", "slides")
    graph.add_edge("slides", "review")
    graph.add_edge("review", "render")
    graph.add_edge("render", END)
    return graph.compile(checkpointer=checkpointer)


@lru_cache(maxsize=None)
def _load_node_prompt(node_key: str) -> str:
    path = PROMPT_DIR / f"{node_key}.md"
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(f"missing deck node prompt file: {path}") from exc
    if not prompt:
        raise ValueError(f"deck node prompt file is empty: {path}")
    return prompt


def _normalize_llm_registry(llms: Any) -> dict[str, Any]:
    if isinstance(llms, dict):
        registry: dict[str, Any] = {}
        for key, value in llms.items():
            node_key = str(key).strip().lower()
            if node_key and value is not None:
                registry[node_key] = value
        return registry

    return {"default": llms}


def _llm_for_node(registry: dict[str, Any], node_key: str) -> Any:
    llm = registry.get(node_key) or registry.get("default")
    if llm is None:
        raise ValueError(f"missing llm for deck node '{node_key}'")
    return llm


def _explicit_llm_for_node(registry: dict[str, Any], node_key: str) -> Any | None:
    return registry.get(node_key)


def _outline_prompt(state: DeckGraphState) -> str:
    return _compose_context_prompt(
        {
            "deck_title_hint": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
        },
        uploaded_file_name=state.get("uploaded_file_name", ""),
        uploaded_file_text=state.get("uploaded_file_text", ""),
    )


def _slides_prompt(state: DeckGraphState) -> str:
    return _compose_context_prompt(
        {
            "deck_title": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "outline_markdown": state.get("outline_markdown", ""),
        },
        uploaded_file_name=state.get("uploaded_file_name", ""),
        uploaded_file_text=state.get("uploaded_file_text", ""),
    )


def _review_prompt(state: DeckGraphState) -> str:
    return _compose_context_prompt(
        {
            "deck_title": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "outline_markdown": state.get("outline_markdown", ""),
            "slides": state.get("slides") or [],
        },
        uploaded_file_name=state.get("uploaded_file_name", ""),
        uploaded_file_text=state.get("uploaded_file_text", ""),
    )


def _compose_context_prompt(
    payload: dict[str, Any],
    *,
    uploaded_file_name: str,
    uploaded_file_text: str,
) -> str:
    parts = [
        "Request context:",
        json.dumps(payload, ensure_ascii=False),
    ]

    uploaded_file_text = (uploaded_file_text or "").strip()
    if uploaded_file_text:
        file_name = (uploaded_file_name or "uploaded file").strip()
        parts.extend(["", f"Uploaded file: {file_name}", "File content:", uploaded_file_text])

    return "\n".join(parts)


def _stream_llm_text(llm: Any, messages: list[tuple[str, str]]):
    if hasattr(llm, "stream"):
        for chunk in llm.stream(messages):
            text = _extract_text(chunk)
            if text:
                yield text
        return

    response = llm.invoke(messages)
    text = _extract_text(response)
    if text:
        yield text


def _invoke_json(llm: Any, system: str, user: str) -> dict[str, Any]:
    response = llm.invoke([("system", system), ("human", user)])
    text = _extract_text(response)
    return _parse_json_object(text)


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("model did not return a JSON object")
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("model JSON output must be an object")
    return parsed


def _normalize_outline_markdown(markdown: str, state: DeckGraphState) -> str:
    cleaned = (markdown or "").strip()
    cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if cleaned:
        return cleaned

    title = str(state.get("deck_title") or "Untitled presentation").strip()
    return f"# {title}\n> Outline generated from the user request."


def _outline_markdown_to_plan(markdown: str, state: DeckGraphState) -> dict[str, Any]:
    title = _first_markdown_heading(markdown) or str(state.get("deck_title") or "Untitled presentation").strip()
    narrative = _first_blockquote(markdown)
    return {
        "title": title,
        "narrative": narrative,
        "slides": _parse_outline_slides(markdown),
    }


def _first_markdown_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if match:
            return match.group(1).strip()
    return ""


def _first_blockquote(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            return stripped.lstrip(">").strip()
    return ""


def _parse_outline_slides(markdown: str) -> list[dict[str, Any]]:
    slides: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^##\s*(?:(\d+)[.)、-]?\s*)?(.+?)\s*$", line)
        if heading:
            if current is not None:
                slides.append(current)
            current = {
                "index": int(heading.group(1) or (len(slides) + 1)),
                "title": _clean_markdown_inline(heading.group(2)),
                "key_message": "",
                "layout": "content",
                "visual_suggestion": "",
            }
            continue

        if current is None or not line.startswith("-"):
            continue

        item = line.lstrip("-").strip()
        label, value = _split_outline_field(item)
        label_lower = label.lower()
        if label in {"核心信息", "关键信息"} or "key" in label_lower:
            current["key_message"] = value
        elif label in {"版式", "布局"} or "layout" in label_lower:
            current["layout"] = _normalize_layout(value)
        elif label in {"视觉建议", "视觉"} or "visual" in label_lower:
            current["visual_suggestion"] = value
        elif not current.get("key_message"):
            current["key_message"] = _clean_markdown_inline(item)

    if current is not None:
        slides.append(current)

    return [
        {
            "index": index + 1,
            "title": str(slide.get("title") or f"Slide {index + 1}").strip(),
            "key_message": str(slide.get("key_message") or "").strip(),
            "layout": _normalize_layout(str(slide.get("layout") or ("title" if index == 0 else "content"))),
            "visual_suggestion": str(slide.get("visual_suggestion") or "").strip(),
        }
        for index, slide in enumerate(slides[:30])
    ]


def _split_outline_field(item: str) -> tuple[str, str]:
    match = re.match(r"^([^:：]+)[:：]\s*(.*)$", item)
    if not match:
        return "", _clean_markdown_inline(item)
    return match.group(1).strip(), _clean_markdown_inline(match.group(2))


def _clean_markdown_inline(value: str) -> str:
    value = re.sub(r"[*_`]+", "", value or "")
    return value.strip()


def _normalize_slides(raw: dict[str, Any], state: DeckGraphState) -> list[dict[str, Any]]:
    raw_slides = raw.get("slides") if isinstance(raw.get("slides"), list) else []
    page_count = _target_slide_count(state, raw_slides)
    plan_slides = (state.get("plan") or {}).get("slides") or []
    slides: list[dict[str, Any]] = []

    for index in range(page_count):
        outline = plan_slides[index] if index < len(plan_slides) and isinstance(plan_slides[index], dict) else {}
        source = raw_slides[index] if index < len(raw_slides) and isinstance(raw_slides[index], dict) else {}
        layout = _normalize_layout(str(source.get("layout") or outline.get("layout") or ("title" if index == 0 else "content")))
        bullets = _normalize_bullets(source.get("bullets"))
        if not bullets and layout not in {"title", "section"}:
            bullets = _fallback_bullets(index + 1, state)

        slides.append(
            {
                "index": index + 1,
                "title": str(source.get("title") or outline.get("title") or _fallback_slide_title(index + 1, state)).strip(),
                "key_message": str(source.get("key_message") or outline.get("key_message") or "").strip(),
                "layout": layout,
                "bullets": bullets[:5],
                "visual_suggestion": str(source.get("visual_suggestion") or outline.get("visual_suggestion") or "").strip(),
                "speaker_notes": str(source.get("speaker_notes") or "").strip(),
            }
        )

    return slides


def _target_slide_count(state: DeckGraphState, raw_slides: list[Any] | None = None) -> int:
    plan_slides = (state.get("plan") or {}).get("slides") or []
    if isinstance(plan_slides, list) and plan_slides:
        return _clamp_slide_count(len(plan_slides))
    if raw_slides:
        return _clamp_slide_count(len(raw_slides))
    parsed = _parse_outline_slides(str(state.get("outline_markdown") or ""))
    if parsed:
        return _clamp_slide_count(len(parsed))
    return 8


def _review(slides: list[dict[str, Any]], page_count: int) -> dict[str, Any]:
    warnings: list[str] = []
    if len(slides) != page_count:
        warnings.append(f"Expected {page_count} slides, got {len(slides)}")

    titles = [str(slide.get("title") or "").strip().lower() for slide in slides]
    duplicates = sorted({title for title in titles if title and titles.count(title) > 1})
    if duplicates:
        warnings.append("Duplicate slide titles: " + ", ".join(duplicates))

    for slide in slides:
        if not str(slide.get("title") or "").strip():
            warnings.append(f"Slide {slide.get('index')} is missing a title")

    return {
        "status": "ok" if not warnings else "warning",
        "warnings": warnings,
    }


def _normalize_review(raw: dict[str, Any]) -> dict[str, Any]:
    status = str(raw.get("status") or "").strip().lower()
    if status not in {"ok", "warning"}:
        status = "warning"

    warnings: list[str] = []
    raw_warnings = raw.get("warnings")
    if isinstance(raw_warnings, list):
        for item in raw_warnings:
            text = str(item).strip()
            if text:
                warnings.append(text)

    if warnings and status == "ok":
        status = "warning"

    return {
        "status": status,
        "warnings": warnings[:10],
    }


def _normalize_bullets(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    bullets: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            bullets.append(text)
    return bullets


def _normalize_layout(value: str) -> str:
    layout = value.strip().lower()
    allowed = {"title", "section", "content", "comparison", "summary"}
    return layout if layout in allowed else "content"


def _fallback_bullets(index: int, state: DeckGraphState) -> list[str]:
    deck_title = str(state.get("deck_title") or "the deck")
    return [
        f"Key point {index} for {deck_title}",
        "Why it matters to the audience",
        "Practical implication or next step",
    ]


def _fallback_slide_title(index: int, state: DeckGraphState) -> str:
    deck_title = str(state.get("deck_title") or "Untitled presentation")
    if index == 1:
        return deck_title
    return f"{deck_title} - Part {index}"


def _clamp_slide_count(value: Any) -> int:
    try:
        page_count = int(value)
    except (TypeError, ValueError):
        page_count = 8
    return max(1, min(page_count, 30))


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars]
