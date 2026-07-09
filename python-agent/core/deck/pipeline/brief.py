"""Brief node: build a deterministic fact sheet from request context."""

from __future__ import annotations

import re
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.helpers import clamp_slide_count, emit_agent, truncate
from core.deck.pipeline.llm import compose_context_prompt, invoke_json_with_retries, load_node_prompt
from core.deck.pipeline.state import DeckGraphState

UPLOADED_FILE_TEXT_LIMIT_CHARS = 60_000


def prepare_brief(state: DeckGraphState, llm_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    writer = get_stream_writer()
    emit_agent(writer, "brief", "Researcher", "整理输入材料和事实表。")
    writer(events.stage_started("brief", "Preparing deck brief"))

    deck_title = str(state.get("deck_title") or "").strip() or "Untitled presentation"
    description = truncate(str(state.get("user_description") or "").strip(), 5000)
    uploaded_file_text = truncate(str(state.get("uploaded_file_text") or "").strip(), UPLOADED_FILE_TEXT_LIMIT_CHARS)
    brief = build_brief(deck_title, description, uploaded_file_text)
    if llm_registry and llm_registry.get("brief") is not None:
        try:
            brief = normalize_brief(
                invoke_json_with_retries(
                    llm_registry["brief"],
                    system=load_node_prompt("brief"),
                    user=brief_prompt(deck_title, description, uploaded_file_text),
                    attempts=2,
                ),
                fallback=brief,
            )
        except ValueError as exc:
            writer(events.stage_progress("brief", f"Brief model output was unusable; used deterministic extraction: {exc}", {"status": "warning"}))
    else:
        writer(events.stage_progress("brief", "使用了确定性素材提取"))
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
        "brief": brief,
    }


def build_brief(deck_title: str, description: str, uploaded_file_text: str) -> dict[str, Any]:
    facts = []
    source_text = "\n".join(part for part in (description, uploaded_file_text) if part)
    for index, sentence in enumerate(_extract_fact_sentences(source_text)[:18], start=1):
        facts.append({"id": f"fact#{index}", "statement": sentence, "source_file": ""})
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
                "facts": [{"id": "fact#1", "statement": "...", "value": "", "unit": "", "source_file": ""}],
                "audience": "string",
                "goal": "string",
                "language": "zh|en",
                "suggested_pages": 8,
            },
        },
        uploaded_file_text=uploaded_file_text,
    )


def normalize_brief(raw: dict[str, Any], *, fallback: dict[str, Any]) -> dict[str, Any]:
    facts = []
    for index, item in enumerate(raw.get("facts") or [], start=1):
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement") or "").strip()
        if not statement:
            continue
        fact_id = str(item.get("id") or f"fact#{index}").strip()
        facts.append(
            {
                "id": fact_id if fact_id.startswith("fact#") else f"fact#{index}",
                "statement": statement,
                "value": str(item.get("value") or "").strip(),
                "unit": str(item.get("unit") or "").strip(),
                "source_file": str(item.get("source_file") or "").strip(),
            }
        )
    if not facts:
        facts = list(fallback.get("facts") or [])
    return {
        "facts": facts[:24],
        "audience": str(raw.get("audience") or fallback.get("audience") or "general audience").strip(),
        "goal": str(raw.get("goal") or fallback.get("goal") or "communicate clearly").strip(),
        "occasion": str(raw.get("occasion") or fallback.get("occasion") or "").strip(),
        "language": str(raw.get("language") or fallback.get("language") or "zh").strip() or "zh",
        "suggested_pages": clamp_slide_count(raw.get("suggested_pages") or fallback.get("suggested_pages") or 8),
    }


def _extract_fact_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", cleaned)
    facts: list[str] = []
    for part in parts:
        item = part.strip(" -#\t")
        if len(item) < 8:
            continue
        if any(char.isdigit() for char in item) or len(item) <= 120:
            facts.append(item[:180])
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
