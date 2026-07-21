"""Planning graph: normalize source facts and generate the narrative outline."""

from __future__ import annotations

import re
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

import core.deck.events as events
from core.deck.helpers import clamp_slide_count, component_from_layout, truncate
from core.deck.instrumentation import instrument_node, run_phase_graph
from core.deck.llm import (
    compose_context_prompt,
    invoke_json_with_retries,
    llm_for_node,
    load_node_prompt,
    normalize_llm_registry,
    stream_llm_text,
)
from core.deck.runtime_metrics import record_fallback
from core.deck.state import DeckGraphState

UPLOADED_FILE_TEXT_LIMIT_CHARS = 60_000


class Planning:
    """Brief and outline phase."""

    def __init__(self, llms: Any) -> None:
        self.llm_registry = normalize_llm_registry(llms)
        self.graph = self.compile_graph()

    def brief(self, state: DeckGraphState) -> dict[str, Any]:
        return prepare_brief(state, self.llm_registry)

    def outline(self, state: DeckGraphState) -> dict[str, Any]:
        return generate_outline(state, self.llm_registry)

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        builder.add_node("brief", instrument_node("brief", self.brief, self.llm_registry))
        builder.add_node("outline", instrument_node("outline", self.outline, self.llm_registry))
        builder.add_edge(START, "brief")
        builder.add_edge("brief", "outline")
        builder.add_edge("outline", END)
        return builder.compile()

    def run(self, state: DeckGraphState) -> dict[str, Any]:
        return run_phase_graph(self.graph, state)


# ─────────────────────────────── brief node ─────────────────────────────────


def prepare_brief(state: DeckGraphState, llm_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("brief", "Preparing deck brief"))

    deck_title = str(state.get("deck_title") or "").strip() or "Untitled presentation"
    description = truncate(str(state.get("user_description") or "").strip(), 5000)
    uploaded_file_text = truncate(str(state.get("uploaded_file_text") or "").strip(), UPLOADED_FILE_TEXT_LIMIT_CHARS)
    source_chunks = list(state.get("source_chunks") or [])
    brief = build_brief(deck_title, description, uploaded_file_text, source_chunks=source_chunks)
    if llm_registry and llm_registry.get("brief") is not None:
        try:
            brief = merge_brief_context(
                invoke_json_with_retries(
                    llm_registry["brief"],
                    system=load_node_prompt("brief"),
                    user=brief_prompt(deck_title, description, uploaded_file_text),
                    attempts=2,
                ),
                deterministic_brief=brief,
            )
        except ValueError as exc:
            record_fallback()
            writer(
                events.stage_progress(
                    "brief",
                    f"Brief model output was unusable; used deterministic extraction: {exc}",
                    {"status": "warning"},
                )
            )
    else:
        record_fallback()
        writer(events.stage_progress("brief", "使用了确定性素材提取"))
    expected_slide_count = int(state.get("expected_slide_count") or 0)
    if expected_slide_count > 0:
        brief["suggested_pages"] = clamp_slide_count(expected_slide_count)
    requested_language = str(state.get("language") or "").strip()
    if requested_language:
        brief["language"] = requested_language
    writer(
        events.fact_sheet_ready(
            {
                "facts_count": len(brief.get("facts") or []),
                "audience": brief.get("audience", ""),
                "goal": brief.get("goal", ""),
                "suggested_pages": brief.get("suggested_pages", 8),
            }
        )
    )
    return {
        "deck_title": truncate(deck_title, 120),
        "user_description": description,
        "uploaded_file_name": str(state.get("uploaded_file_name") or "").strip(),
        "uploaded_file_text": uploaded_file_text,
        "source_chunks": source_chunks,
        "brief": brief,
    }


def build_brief(deck_title: str, description: str, uploaded_file_text: str, *, source_chunks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    facts = []
    chunks = [{"source_file": "user_request", "page": 0, "section": "description", "excerpt": description}]
    chunks.extend(source_chunks or [])
    for chunk in chunks:
        for sentence in _extract_fact_sentences(str(chunk.get("excerpt") or "")):
            value, unit = _extract_value_and_unit(sentence)
            facts.append(
                {
                    "id": f"fact#{len(facts) + 1}",
                    "statement": sentence,
                    "value": value,
                    "unit": unit,
                    "source_file": str(chunk.get("source_file") or ""),
                    "page": int(chunk.get("page") or 0),
                    "section": str(chunk.get("section") or ""),
                    "source_excerpt": str(chunk.get("excerpt") or sentence)[:500],
                }
            )
    language = "en" if _looks_english(description or deck_title) else "zh"
    return {
        "facts": facts,
        "audience": _infer_audience(description),
        "goal": _infer_goal(description),
        "occasion": "",
        "language": language,
        "suggested_pages": _infer_page_count(description),
    }


def brief_prompt(deck_title: str, description: str, uploaded_file_text: str) -> str:
    return compose_context_prompt(
        {
            "deck_title": deck_title,
            "user_description": description,
            "required_shape": {
                "audience": "string",
                "goal": "string",
                "occasion": "string",
                "language": "zh|en",
                "suggested_pages": 8,
            },
        },
        uploaded_file_text=uploaded_file_text,
    )


def merge_brief_context(
    model_context: dict[str, Any],
    *,
    deterministic_brief: dict[str, Any],
) -> dict[str, Any]:
    """Merge model-inferred presentation context into the source fact ledger."""

    facts = [item for item in deterministic_brief.get("facts") or [] if isinstance(item, dict)]
    return {
        "facts": facts,
        "audience": str(
            model_context.get("audience")
            or deterministic_brief.get("audience")
            or "general audience"
        ).strip(),
        "goal": str(
            model_context.get("goal")
            or deterministic_brief.get("goal")
            or "communicate clearly"
        ).strip(),
        "occasion": str(
            model_context.get("occasion") or deterministic_brief.get("occasion") or ""
        ).strip(),
        "language": str(
            model_context.get("language") or deterministic_brief.get("language") or "zh"
        ).strip()
        or "zh",
        "suggested_pages": clamp_slide_count(
            model_context.get("suggested_pages")
            or deterministic_brief.get("suggested_pages")
            or 8
        ),
    }


def _extract_fact_sentences(text: str) -> list[str]:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    parts: list[str] = []
    for line in normalized.splitlines():
        cleaned_line = re.sub(r"^\s*(?:[-*+]\s+|#{1,6}\s+|\d+[.)、]\s+)", "", line).strip()
        if not cleaned_line:
            continue
        parts.extend(re.split(r"(?<=[。！？!?；;])\s*", cleaned_line))
    facts: list[str] = []
    for part in parts:
        item = re.sub(r"\s+", " ", part).strip(" -#\t")
        if len(item) < 8:
            continue
        if item not in facts and (any(char.isdigit() for char in item) or len(item) <= 120):
            facts.append(item)
    return facts


def _infer_audience(description: str) -> str:
    match = re.search(r"(?:面向|给|for)\s*([^,，。.!?\n]{2,24})", description or "", flags=re.IGNORECASE)
    return match.group(1).strip() if match else "general audience"


def _infer_goal(description: str) -> str:
    text = (description or "").strip()
    if not text:
        return "communicate the main idea clearly"
    return truncate(text.splitlines()[0], 120)


def _infer_page_count(description: str) -> int:
    match = re.search(r"(\d{1,2})\s*(?:页|slides?|pages?)", description or "", flags=re.IGNORECASE)
    if match:
        return clamp_slide_count(match.group(1))
    return 8


def _looks_english(text: str) -> bool:
    if not text:
        return False
    ascii_letters = sum(1 for char in text if "a" <= char.lower() <= "z")
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return ascii_letters > cjk * 2


def _extract_value_and_unit(statement: str) -> tuple[str, str]:
    match = re.search(r"(?<!\w)([-+]?\d[\d,.]*)(\s*(?:%|％|亿元|万元|元|美元|人|次|天|年|月|GB|MB|ms|s))?", statement, flags=re.IGNORECASE)
    if not match:
        return "", ""
    return match.group(1), (match.group(2) or "").strip()


# ─────────────────────────────── outline node ───────────────────────────────


def generate_outline(state: DeckGraphState, llm_registry: dict[str, Any]) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("outline", "Generating deck outline"))

    parts: list[str] = []
    messages = [("system", load_node_prompt("outline")), ("human", outline_prompt(state))]
    for delta in stream_llm_text(llm_for_node(llm_registry, "outline"), messages):
        if not delta:
            continue
        parts.append(delta)

    outline_markdown = normalize_outline_markdown("".join(parts), state)
    plan = outline_markdown_to_plan(outline_markdown, state)
    writer(events.outline_ready(outline_markdown))
    return {"outline_markdown": outline_markdown, "plan": plan}


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
    """Parse the narrative and page-level art-direction hints."""

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
    "Planning",
    "build_brief",
    "generate_outline",
    "merge_brief_context",
    "normalize_outline_markdown",
    "outline_markdown_to_plan",
    "outline_prompt",
    "parse_outline_slides",
    "prepare_brief",
]
