"""Brief node: build a deterministic fact sheet from request context."""

from __future__ import annotations

import re
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.helpers import clamp_slide_count, emit_agent, truncate
from core.deck.pipeline.llm import compose_context_prompt, invoke_json_with_retries, load_node_prompt
from core.deck.runtime_metrics import record_fallback
from core.deck.pipeline.state import DeckGraphState

UPLOADED_FILE_TEXT_LIMIT_CHARS = 60_000


def prepare_brief(state: DeckGraphState, llm_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    writer = get_stream_writer()
    emit_agent(writer, "brief", "Researcher", "整理输入材料和事实表。")
    writer(events.stage_started("brief", "Preparing deck brief"))

    deck_title = str(state.get("deck_title") or "").strip() or "Untitled presentation"
    description = truncate(str(state.get("user_description") or "").strip(), 5000)
    uploaded_file_text = truncate(str(state.get("uploaded_file_text") or "").strip(), UPLOADED_FILE_TEXT_LIMIT_CHARS)
    source_chunks = list(state.get("source_chunks") or [])
    brief = build_brief(deck_title, description, uploaded_file_text, source_chunks=source_chunks)
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
            record_fallback()
            writer(events.stage_progress("brief", f"Brief model output was unusable; used deterministic extraction: {exc}", {"status": "warning"}))
    else:
        record_fallback()
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
    # The deterministic ledger is authoritative because every entry has verified
    # file/page/section provenance. Model output may improve audience/goal fields,
    # but must never replace or invent source facts.
    fallback_facts = [item for item in fallback.get("facts") or [] if isinstance(item, dict)]
    return {
        "facts": fallback_facts,
        "audience": str(raw.get("audience") or fallback.get("audience") or "general audience").strip(),
        "goal": str(raw.get("goal") or fallback.get("goal") or "communicate clearly").strip(),
        "occasion": str(raw.get("occasion") or fallback.get("occasion") or "").strip(),
        "language": str(raw.get("language") or fallback.get("language") or "zh").strip() or "zh",
        "suggested_pages": clamp_slide_count(raw.get("suggested_pages") or fallback.get("suggested_pages") or 8),
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
