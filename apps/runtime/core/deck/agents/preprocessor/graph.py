"""One-shot global outline planner for the three-stage PPT generation flow."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

import core.deck.pipeline.events as events
from core.deck.agents.processor.design import create_design_spec, persist_design_artifacts
from core.deck.agents.processor.layout import allocate_layout_preferences, deck_plan_from_outline
from core.deck.domain.schemas import DeckOutlineSpec, LayoutFamily, SCHEMA_VERSION, SlideRole
from core.deck.domain.text import truncate
from core.deck.pipeline.instrumentation import instrument_node
from core.deck.pipeline.state import DeckGraphState
from core.deck.runtime.llm import (
    invoke_structured_with_retries,
    llm_for_node,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.workspace.paths import DeckProjectPaths


SOURCE_TEXT_LIMIT = 60_000
PREPROCESSOR_NODE_SEQUENCE = ("generate_deck_outline",)
_PAGE_COUNT_SUFFIX_PATTERN = re.compile(
    r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*|\s*)(?:页|頁|pages?|slides?|"
    r"[张張]\s*(?:幻灯片|投影片))(?![a-z])",
    flags=re.IGNORECASE,
)
_PAGE_COUNT_PREFIX_PATTERN = re.compile(
    r"(?:page|slide)\s*count\s*[:=]?\s*(\d{1,4})(?!\d)",
    flags=re.IGNORECASE,
)
_CHINESE_PAGE_COUNT_PATTERN = re.compile(
    r"([零〇一二两兩三四五六七八九十]{1,3})\s*(?:页|頁|[张張]\s*(?:幻灯片|投影片))"
)


class Preprocessor:
    """Generate one complete deck outline before page branches are created."""

    def __init__(self, llms: Any, output_dir: Path | None = None) -> None:
        self.llm_registry = normalize_llm_registry(llms)
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.graph = self.compile_graph()

    def generate_deck_outline(self, state: DeckGraphState) -> dict[str, Any]:
        return generate_deck_outline(
            state,
            self.llm_registry,
            output_dir=self.output_dir,
        )

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        builder.add_node(
            "generate_deck_outline",
            instrument_node(
                "generate_deck_outline",
                self.generate_deck_outline,
                self.llm_registry,
                model_key="outline",
            ),
        )
        builder.add_edge(START, "generate_deck_outline")
        builder.add_edge("generate_deck_outline", END)
        return builder.compile()

    def run(self, state: DeckGraphState) -> dict[str, Any]:
        return self.graph.invoke(state)


def generate_deck_outline(
    state: DeckGraphState,
    llm_registry: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Call the outline model exactly once on the successful path."""

    emit_event = get_stream_writer()
    emit_event(events.stage_started("generate_deck_outline", "Planning the complete deck outline"))
    incoming_schema = str(state.get("schema_version") or "").strip()
    if incoming_schema != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported deck state schema {incoming_schema!r}; restart the task with {SCHEMA_VERSION}"
        )
    user_request = str(state.get("user_description") or "").strip()
    if not user_request:
        raise ValueError("user_description is required")

    uploaded_text = str(state.get("uploaded_file_text") or "").strip()
    source_text = truncate(
        "\n\n".join(part for part in (user_request, uploaded_text) if part),
        SOURCE_TEXT_LIMIT,
    )
    requested_page_count = _requested_page_count(user_request)
    prompt_payload = {
        "user_request": user_request,
        "source_markdown": source_text,
        "available_assets": _available_assets(state, source_text),
        "requested_page_count": requested_page_count,
        "hard_constraints": {
            "minimum_page_count": 1,
            "maximum_page_count": 30,
            "canvas": "1600x900 (16:9)",
            "supported_layout_types": [item.value for item in LayoutFamily],
            "first_slide_role": SlideRole.COVER.value,
            "last_slide_role_when_multiple": SlideRole.CLOSING.value,
            "adjacent_layouts_must_differ": True,
            "global_style_is_the_only_palette_source": True,
            "external_resources": "forbidden",
        },
    }
    outline = invoke_structured_with_retries(
        llm_for_node(llm_registry, "outline"),
        DeckOutlineSpec,
        system=load_node_prompt("deck_outline_planner"),
        user=json.dumps(prompt_payload, ensure_ascii=False),
        attempts=3,
        output_validator=lambda value: _validate_outline_output(
            DeckOutlineSpec.model_validate(value),
            requested_page_count=requested_page_count,
        ),
    )
    outline = DeckOutlineSpec.model_validate(outline)
    plan = deck_plan_from_outline(outline)
    layout_preferences = allocate_layout_preferences(plan)
    theme, design = create_design_spec(outline)

    project = _project_paths(output_dir, state)
    if project is not None:
        _atomic_write_json(project.deck_outline_json, outline.model_dump(mode="json"))
        _atomic_write_json(
            project.global_style_json,
            outline.metadata.global_style_spec.model_dump(mode="json"),
        )
        _atomic_write_json(project.deck_plan_json, plan.model_dump(mode="json"))
        persist_design_artifacts(
            project,
            theme=theme,
            design=design,
            plan=plan,
            layout_preferences=layout_preferences,
        )

    emit_event(events.outline_ready(_outline_markdown(outline)))
    emit_event(events.template_selected(theme.name, theme.theme_id))
    emit_event(
        events.stage_progress(
            "generate_deck_outline",
            "Deck outline, global palette, and renderer lock ready",
            {
                "status": "done",
                "page_count": len(outline.slides),
                "total": len(outline.slides),
                "current": len(outline.slides),
                "unit": "page",
            },
        )
    )
    result: dict[str, Any] = {
        "deck_title": outline.title,
        "deck_outline": outline.model_dump(mode="json"),
        "global_style_spec": outline.metadata.global_style_spec.model_dump(mode="json"),
        "source_text": source_text,
        "deck_plan": plan.model_dump(mode="json"),
        "layout_preferences": layout_preferences,
        "selected_template_id": theme.theme_id,
        "design_spec": design.model_dump(mode="json"),
        "status": "designing",
    }
    if requested_page_count is not None:
        result["requested_page_count"] = requested_page_count
    return result


def _validate_outline_output(
    outline: DeckOutlineSpec,
    *,
    requested_page_count: int | None,
) -> DeckOutlineSpec:
    if requested_page_count is not None and len(outline.slides) != requested_page_count:
        raise ValueError(
            f"outline contains {len(outline.slides)} slides; requested {requested_page_count}"
        )
    if outline.slides[0].type is not SlideRole.COVER:
        raise ValueError("the first outline slide must have type=cover")
    if outline.slides[0].layout_suggestion is not LayoutFamily.COVER_MINIMAL:
        raise ValueError("the cover must use layout_suggestion=cover_minimal")
    if len(outline.slides) > 1:
        if outline.slides[-1].type is not SlideRole.CLOSING:
            raise ValueError("the final outline slide must have type=closing")
        if outline.slides[-1].layout_suggestion is not LayoutFamily.CLOSING_RESOLUTION:
            raise ValueError("the final slide must use layout_suggestion=closing_resolution")
    for previous, current in zip(outline.slides, outline.slides[1:]):
        if previous.layout_suggestion is current.layout_suggestion:
            raise ValueError(
                f"adjacent slides {previous.id} and {current.id} must use different layouts"
            )
    return outline


def _requested_page_count(user_request: str) -> int | None:
    numeric_match = _PAGE_COUNT_SUFFIX_PATTERN.search(user_request)
    if numeric_match is None:
        numeric_match = _PAGE_COUNT_PREFIX_PATTERN.search(user_request)
    if numeric_match is not None:
        page_count = int(numeric_match.group(1))
    else:
        chinese_match = _CHINESE_PAGE_COUNT_PATTERN.search(user_request)
        if chinese_match is None:
            return None
        page_count = _chinese_integer(chinese_match.group(1))
    if not 1 <= page_count <= 30:
        raise ValueError("requested page count must be between 1 and 30")
    return page_count


def _chinese_integer(value: str) -> int:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "兩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if "十" not in value:
        return digits.get(value, -1)
    tens_text, ones_text = value.split("十", 1)
    tens = digits.get(tens_text, 1) if tens_text else 1
    ones = digits.get(ones_text, 0) if ones_text else 0
    return tens * 10 + ones


def _available_assets(state: DeckGraphState, source_text: str) -> dict[str, Any]:
    chunks = [item for item in state.get("source_chunks") or [] if isinstance(item, dict)]
    files: list[str] = []
    for item in chunks:
        name = str(item.get("source_file") or "").strip()
        if name and name not in files:
            files.append(name)
    asset_lines = [
        line.strip()
        for line in source_text.splitlines()
        if re.search(
            r"(?:asset|artifact|image|chart|table|dashboard|kpi|图|表|看板|指标)",
            line,
            flags=re.IGNORECASE,
        )
    ][:120]
    source_slices = [
        {
            "source_file": item.get("source_file", ""),
            "page": item.get("page", 0),
            "section": item.get("section", ""),
        }
        for item in chunks[:200]
    ]
    local_assets = [
        {
            key: item.get(key)
            for key in (
                "asset_id",
                "kind",
                "original_name",
                "mime_type",
                "relative_path",
                "size_bytes",
                "sha256",
                "trusted_local_only",
                "visual_analysis_available",
                "html_embedding_allowed",
            )
        }
        for item in state.get("local_assets") or []
        if isinstance(item, dict)
    ][:120]
    return {
        "source_files": files,
        "source_slices": source_slices,
        "local_assets": local_assets,
        "declared_asset_lines": asset_lines,
        "rules": [
            "Prefer charts for trends and comparisons.",
            "Prefer tables for exact evidence.",
            "Prefer KPI blocks for key numbers.",
            "Prefer dashboards for overview pages.",
        ],
    }


def _outline_markdown(outline: DeckOutlineSpec) -> str:
    lines = [f"# {outline.title}"]
    if outline.subtitle:
        lines.extend(["", f"> {outline.subtitle}"])
    lines.append("")
    for index, slide in enumerate(outline.slides, start=1):
        lines.append(f"## {index}. {slide.title}")
        lines.extend(f"- {point}" for point in slide.key_points)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _project_paths(output_dir: Path | None, state: DeckGraphState) -> DeckProjectPaths | None:
    request_id = str(state.get("request_id") or "").strip()
    if output_dir is None or not request_id:
        return None
    return DeckProjectPaths.for_request(output_dir, request_id).ensure()


def _atomic_write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "PREPROCESSOR_NODE_SEQUENCE",
    "Preprocessor",
    "generate_deck_outline",
]
