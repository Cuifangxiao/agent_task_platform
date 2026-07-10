"""Outline nodes and Markdown outline parsing."""

from __future__ import annotations

import re
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

import core.deck.events as events
from core.deck.pipeline.helpers import component_from_layout, emit_agent
from core.deck.pipeline.llm import compose_context_prompt, llm_for_node, load_node_prompt, stream_llm_text
from core.deck.pipeline.state import DeckGraphState


def generate_outline(state: DeckGraphState, llm_registry: dict[str, Any]) -> dict[str, Any]:
    writer = get_stream_writer()
    emit_agent(writer, "outline", "Strategist", "规划叙事顺序并流式输出 Markdown 大纲。")
    writer(events.stage_started("outline", "Generating deck outline"))

    parts: list[str] = []
    messages = [("system", load_node_prompt("outline")), ("human", outline_prompt(state))]
    for delta in stream_llm_text(llm_for_node(llm_registry, "outline"), messages):
        if not delta:
            continue
        parts.append(delta)
        writer(events.text_delta("outline", "outline_markdown", delta, "Streaming outline markdown"))

    outline_markdown = normalize_outline_markdown("".join(parts), state)
    plan = outline_markdown_to_plan(outline_markdown, state)
    writer(events.outline_ready(outline_markdown))
    return {"outline_markdown": outline_markdown, "plan": plan, "outline_action": ""}


def review_outline(state: DeckGraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    outline_markdown = str(state.get("outline_markdown") or "").strip()
    if str(state.get("generation_mode") or "") == "quick":
        writer(events.stage_progress("outline_review", "Quick mode approved the generated outline", {"status": "done"}))
        return {"outline_action": "style"}
    writer(events.stage_progress("outline_review", "Waiting for outline confirmation"))
    decision = interrupt({"stage": "outline_review", "outline_markdown": outline_markdown})

    if isinstance(decision, dict):
        action = str(decision.get("action") or "approve_outline").strip().lower()
        if action == "regenerate_outline":
            return {"outline_action": "regenerate"}
        if action in {"continue", "approve", "approved", "approve_outline"}:
            edited_outline = decision.get("outline_markdown") or decision.get("content")
            if isinstance(edited_outline, str) and edited_outline.strip():
                edited_outline = normalize_outline_markdown(edited_outline, state)
                return {"outline_markdown": edited_outline, "plan": outline_markdown_to_plan(edited_outline, state), "outline_action": "style"}
    return {"outline_action": "style"}


def outline_prompt(state: DeckGraphState) -> str:
    return compose_context_prompt(
        {
            "deck_title_hint": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "brief": state.get("brief") or {},
        },
        uploaded_file_text=state.get("uploaded_file_text", ""),
    )


def normalize_outline_markdown(markdown: str, state: DeckGraphState) -> str:
    cleaned = (markdown or "").strip()
    cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if cleaned:
        return cleaned
    title = str(state.get("deck_title") or "Untitled presentation").strip()
    return f"# {title}\n> Outline generated from the user request."


def outline_markdown_to_plan(markdown: str, state: DeckGraphState) -> dict[str, Any]:
    title = _first_markdown_heading(markdown) or str(state.get("deck_title") or "Untitled presentation").strip()
    narrative = _first_blockquote(markdown)
    return {"title": title, "narrative": narrative, "slides": parse_outline_slides(markdown)}


def parse_outline_slides(markdown: str) -> list[dict[str, Any]]:
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
                "role": "",
                "component": "",
                "layout": "content",
            }
            continue
        if current is None or not line.startswith("-"):
            continue
        item = line.lstrip("-").strip()
        label, value = _split_outline_field(item)
        label_lower = label.lower()
        if label in {"核心信息", "关键信息"} or "key" in label_lower:
            current["key_message"] = value
        elif label in {"角色", "叙事角色"} or "role" in label_lower:
            current["role"] = value
        elif label in {"组件", "component"} or "component" in label_lower:
            current["component"] = value
        elif label in {"版式", "布局"} or "layout" in label_lower:
            current["layout"] = value
        elif not current.get("key_message"):
            current["key_message"] = _clean_markdown_inline(item)
    if current is not None:
        slides.append(current)
    return [
        {
            "index": index + 1,
            "title": str(slide.get("title") or f"Slide {index + 1}").strip(),
            "key_message": str(slide.get("key_message") or "").strip(),
            "role": str(slide.get("role") or ("hook" if index == 0 else "evidence")).strip(),
            "component": str(slide.get("component") or component_from_layout(slide.get("layout"), index)).strip(),
            "layout": str(slide.get("layout") or ("title" if index == 0 else "content")).strip(),
        }
        for index, slide in enumerate(slides[:30])
    ]


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


def _split_outline_field(item: str) -> tuple[str, str]:
    match = re.match(r"^([^:：]+)[:：]\s*(.*)$", item)
    if not match:
        return "", _clean_markdown_inline(item)
    return match.group(1).strip(), _clean_markdown_inline(match.group(2))


def _clean_markdown_inline(value: str) -> str:
    return re.sub(r"[*_`]+", "", value or "").strip()
