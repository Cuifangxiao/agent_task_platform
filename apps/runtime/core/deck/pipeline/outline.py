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
    emit_agent(writer, "outline", "Strategist", "规划叙事顺序并流式输出包含页面节奏、版式意图和证据要求的 Markdown 大纲。")
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
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else {}
    decision = interrupt(
        {
            "stage": "outline_review",
            "outline_markdown": outline_markdown,
            "review_summary": {
                "page_count": len(plan.get("slides") or []),
                "audience": str(brief.get("audience") or ""),
                "goal": str(brief.get("goal") or ""),
                "language": str(brief.get("language") or "zh"),
            },
            "allowed_actions": ["approve_outline", "regenerate_outline"],
            "editable": True,
        }
    )

    if isinstance(decision, dict):
        action = str(decision.get("action") or "approve_outline").strip().lower()
        if action == "regenerate_outline":
            payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
            feedback = str(decision.get("feedback") or payload.get("feedback") or "").strip()
            return {"outline_action": "regenerate", "outline_feedback": feedback}
        if action in {"continue", "approve", "approved", "approve_outline"}:
            edited_outline = decision.get("outline_markdown") or decision.get("content")
            if isinstance(edited_outline, str) and edited_outline.strip():
                edited_outline = normalize_outline_markdown(edited_outline, state)
                return {
                    "outline_markdown": edited_outline,
                    "plan": outline_markdown_to_plan(edited_outline, state),
                    "outline_action": "style",
                    "outline_feedback": "",
                }
    return {"outline_action": "style", "outline_feedback": ""}


def outline_prompt(state: DeckGraphState) -> str:
    return compose_context_prompt(
        {
            "deck_title_hint": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "brief": state.get("brief") or {},
            "outline_feedback": state.get("outline_feedback") or "",
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
    """Parse the confirmed narrative and page-level art-direction hints.

    Markdown remains editable by users, while this normalized structure becomes
    the input to the Art Director and template-layout selection.
    """

    slides: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^##\s*(?:(\d+)[.)、．.]?\s*)?(.+?)\s*$", line)
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
                "page_rhythm": "",
                "layout_intent": "",
                "visualization": "",
                "evidence": "",
            }
            continue
        if current is None or not line.startswith("-"):
            continue
        item = line.lstrip("-").strip()
        label, value = _split_outline_field(item)
        label_key = _normalize_outline_label(label)
        if label_key:
            current[label_key] = value
        elif not current.get("key_message"):
            current["key_message"] = _clean_markdown_inline(item)
    if current is not None:
        slides.append(current)

    normalized: list[dict[str, Any]] = []
    for offset, slide in enumerate(slides[:30]):
        layout = str(slide.get("layout") or ("title" if offset == 0 else "content")).strip()
        component = str(slide.get("component") or component_from_layout(layout, offset)).strip()
        normalized.append(
            {
                "index": offset + 1,
                "title": str(slide.get("title") or f"Slide {offset + 1}").strip(),
                "key_message": str(slide.get("key_message") or "").strip(),
                "role": str(slide.get("role") or ("hook" if offset == 0 else "evidence")).strip(),
                "component": component,
                "layout": layout,
                "page_rhythm": _normalize_page_rhythm(slide.get("page_rhythm"), component=component, index=offset),
                "layout_intent": str(slide.get("layout_intent") or layout).strip(),
                "visualization": str(slide.get("visualization") or "none").strip(),
                "evidence": str(slide.get("evidence") or "").strip(),
            }
        )
    return normalized


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


def _normalize_outline_label(label: str) -> str:
    compact = re.sub(r"[\s_\-/]+", "", (label or "").strip().casefold())
    aliases = {
        "key_message": {"核心信息", "关键信息", "关键结论", "核心观点", "key", "keymessage", "message", "鏍稿績淇℃伅", "鍏抽敭淇℃伅"},
        "role": {"角色", "叙事角色", "role", "storyrole", "瑙掕壊", "鍙欎簨瑙掕壊"},
        "component": {"组件", "component", "缁勪欢"},
        "layout": {"版式", "布局", "layout", "鐗堝紡", "甯冨眬"},
        "page_rhythm": {"页面节奏", "节奏", "pagerhythm", "rhythm"},
        "layout_intent": {"版式意图", "布局意图", "layoutintent", "compositionintent"},
        "visualization": {"可视化", "视觉建议", "图表建议", "visualization", "visual", "visualsuggestion", "瑙嗚寤鸿"},
        "evidence": {"证据", "数据证据", "事实依据", "来源", "evidence", "source", "sources"},
    }
    for key, values in aliases.items():
        if compact in {re.sub(r"[\s_\-/]+", "", value.casefold()) for value in values}:
            return key
    if "key" in compact and "message" in compact:
        return "key_message"
    return ""


def _normalize_page_rhythm(value: Any, *, component: str, index: int) -> str:
    raw = str(value or "").strip().casefold()
    if raw in {"anchor", "锚点", "錨點", "封面", "章节", "章節"}:
        return "anchor"
    if raw in {"breathing", "呼吸", "留白", "舒展", "低密度"}:
        return "breathing"
    if raw in {"dense", "密集", "紧凑", "緊湊", "高密度", "数据"}:
        return "dense"
    if component in {"cover", "section", "end"} or index == 0:
        return "anchor"
    if component in {"quote", "summary"}:
        return "breathing"
    return "dense"


__all__ = [
    "generate_outline",
    "normalize_outline_markdown",
    "outline_markdown_to_plan",
    "outline_prompt",
    "parse_outline_slides",
    "review_outline",
]
