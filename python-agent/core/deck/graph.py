"""LangGraph workflow for deck generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from core.deck.renderer import DeckRenderer


class DeckGraphState(TypedDict, total=False):
    request_id: str
    topic: str
    audience: str
    goal: str
    page_count: int
    language: str
    style: str
    source_text: str
    plan: dict[str, Any]
    slides: list[dict[str, Any]]
    review: dict[str, Any]
    artifact_path: str


def build_deck_graph(llms: Any, output_dir: Path):
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

        return {
            "topic": str(state.get("topic") or "").strip(),
            "audience": str(state.get("audience") or "general audience").strip(),
            "goal": str(state.get("goal") or "explain the topic clearly").strip(),
            "page_count": _clamp_page_count(state.get("page_count")),
            "language": str(state.get("language") or "zh-CN").strip(),
            "style": str(state.get("style") or "concise business deck").strip(),
            "source_text": _truncate(str(state.get("source_text") or "").strip(), 12000),
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

        plan = _normalize_plan(
            _invoke_json(
                _llm_for_node(llm_registry, "outline"),
                system=(
                    "You are a senior presentation strategist. Return only valid JSON. "
                    "Design a coherent presentation outline with a clear narrative arc."
                ),
                user=_outline_prompt(state),
            ),
            state,
        )
        writer(
            {
                "event_type": "outline",
                "stage": "outline",
                "message": "Outline ready",
                "payload": plan,
            }
        )
        return {"plan": plan}

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
                system=(
                    "You are an expert slide writer. Return only valid JSON. "
                    "Write concise slide content with strong titles and practical visuals."
                ),
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

    def review_deck(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        review_llm = _explicit_llm_for_node(llm_registry, "review")
        if review_llm is None:
            review = _review(state.get("slides") or [], _clamp_page_count(state.get("page_count")))
        else:
            review = _normalize_review(
                _invoke_json(
                    review_llm,
                    system=(
                        "You are a presentation QA reviewer. Return only valid JSON. "
                        "Check slide count, duplication, clarity, and completeness."
                    ),
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
    graph.add_node("slides", generate_slides)
    graph.add_node("review", review_deck)
    graph.add_node("render", render_deck)
    graph.add_edge(START, "brief")
    graph.add_edge("brief", "outline")
    graph.add_edge("outline", "slides")
    graph.add_edge("slides", "review")
    graph.add_edge("review", "render")
    graph.add_edge("render", END)
    return graph.compile()


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
    return json.dumps(
        {
            "task": "Create a presentation outline.",
            "topic": state["topic"],
            "audience": state["audience"],
            "goal": state["goal"],
            "page_count": state["page_count"],
            "language": state["language"],
            "style": state["style"],
            "source_text": state.get("source_text", ""),
            "required_json_shape": {
                "title": "string",
                "narrative": "string",
                "slides": [
                    {
                        "index": 1,
                        "title": "string",
                        "key_message": "string",
                        "layout": "title|section|content|comparison|summary",
                        "visual_suggestion": "string",
                    }
                ],
            },
        },
        ensure_ascii=False,
    )


def _slides_prompt(state: DeckGraphState) -> str:
    return json.dumps(
        {
            "task": "Expand the outline into exact slide specs.",
            "brief": {
                "topic": state["topic"],
                "audience": state["audience"],
                "goal": state["goal"],
                "page_count": state["page_count"],
                "language": state["language"],
                "style": state["style"],
            },
            "outline": state.get("plan") or {},
            "requirements": [
                "Return exactly page_count slides.",
                "Each slide should have 3 to 5 concise bullets except title or section slides.",
                "Do not use Markdown fences.",
                "Keep all visible slide text in the requested language.",
            ],
            "required_json_shape": {
                "slides": [
                    {
                        "index": 1,
                        "title": "string",
                        "key_message": "string",
                        "layout": "title|section|content|comparison|summary",
                        "bullets": ["string"],
                        "visual_suggestion": "string",
                        "speaker_notes": "string",
                    }
                ]
            },
        },
        ensure_ascii=False,
    )


def _review_prompt(state: DeckGraphState) -> str:
    return json.dumps(
        {
            "task": "Review these slide specs before rendering.",
            "brief": {
                "topic": state["topic"],
                "audience": state["audience"],
                "goal": state["goal"],
                "page_count": state["page_count"],
                "language": state["language"],
                "style": state["style"],
            },
            "slides": state.get("slides") or [],
            "required_json_shape": {
                "status": "ok|warning",
                "warnings": ["string"],
            },
        },
        ensure_ascii=False,
    )


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


def _normalize_plan(raw: dict[str, Any], state: DeckGraphState) -> dict[str, Any]:
    page_count = _clamp_page_count(state.get("page_count"))
    raw_slides = raw.get("slides") if isinstance(raw.get("slides"), list) else []
    slides: list[dict[str, Any]] = []

    for index in range(page_count):
        source = raw_slides[index] if index < len(raw_slides) and isinstance(raw_slides[index], dict) else {}
        slides.append(
            {
                "index": index + 1,
                "title": str(source.get("title") or _fallback_slide_title(index + 1, state["topic"])).strip(),
                "key_message": str(source.get("key_message") or source.get("message") or "").strip(),
                "layout": _normalize_layout(str(source.get("layout") or ("title" if index == 0 else "content"))),
                "visual_suggestion": str(source.get("visual_suggestion") or "").strip(),
            }
        )

    return {
        "title": str(raw.get("title") or state["topic"]).strip(),
        "narrative": str(raw.get("narrative") or state["goal"]).strip(),
        "slides": slides,
    }


def _normalize_slides(raw: dict[str, Any], state: DeckGraphState) -> list[dict[str, Any]]:
    page_count = _clamp_page_count(state.get("page_count"))
    plan_slides = (state.get("plan") or {}).get("slides") or []
    raw_slides = raw.get("slides") if isinstance(raw.get("slides"), list) else []
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
                "title": str(source.get("title") or outline.get("title") or _fallback_slide_title(index + 1, state["topic"])).strip(),
                "key_message": str(source.get("key_message") or outline.get("key_message") or "").strip(),
                "layout": layout,
                "bullets": bullets[:5],
                "visual_suggestion": str(source.get("visual_suggestion") or outline.get("visual_suggestion") or "").strip(),
                "speaker_notes": str(source.get("speaker_notes") or "").strip(),
            }
        )

    return slides


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
    topic = state["topic"]
    return [
        f"Key point {index} about {topic}",
        "Why it matters to the audience",
        "Practical implication or next step",
    ]


def _fallback_slide_title(index: int, topic: str) -> str:
    if index == 1:
        return topic
    return f"{topic} - Part {index}"


def _clamp_page_count(value: Any) -> int:
    try:
        page_count = int(value)
    except (TypeError, ValueError):
        page_count = 8
    return max(1, min(page_count, 30))


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars]
