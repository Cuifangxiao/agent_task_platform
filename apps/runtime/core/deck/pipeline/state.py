"""Serializable LangGraph state for the HTML-first pipeline.

Only request data, schema-validated dictionaries, diagnostics, and lightweight
artifact metadata enter the graph state. Browser objects, LLM clients, file
handles, and image bytes remain request-scoped dependencies outside the state.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, TypedDict


def merge_completed_slides(
    left: dict[str, dict[str, Any]] | None,
    right: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Merge independently generated slide metadata by stable slide id."""

    merged = dict(left or {})
    merged.update(right or {})
    return merged


def merge_layout_history(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep one deterministic composition record per independently generated slide."""

    by_slide_id: dict[str, dict[str, Any]] = {}
    for item in [*(left or []), *(right or [])]:
        if not isinstance(item, dict):
            continue
        slide_id = str(item.get("slide_id") or "").strip()
        if slide_id:
            by_slide_id[slide_id] = dict(item)
    return [by_slide_id[key] for key in sorted(by_slide_id)]


def merge_validation_issues(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Deduplicate diagnostics emitted concurrently and restore page order."""

    unique: dict[str, dict[str, Any]] = {}
    for item in [*(left or []), *(right or [])]:
        if not isinstance(item, dict):
            continue
        value = dict(item)
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        unique[identity] = value
    return sorted(
        unique.values(),
        key=lambda item: (
            int(item.get("index") or 0),
            str(item.get("slide_id") or ""),
            str(item.get("code") or ""),
            str(item.get("selector") or ""),
        ),
    )


class DeckRunState(TypedDict, total=False):
    schema_version: str
    prompt_version: str
    renderer_version: str
    task_id: str
    request_id: str
    project_dir: str
    status: Literal[
        "created",
        "planning",
        "designing",
        "generating",
        "exporting",
        "completed",
    ]


class DeckInputState(TypedDict, total=False):
    deck_title: str
    user_description: str
    uploaded_file_text: str
    source_chunks: list[dict[str, Any]]
    local_assets: list[dict[str, Any]]


class DeckPreprocessorState(TypedDict, total=False):
    """Canonical output of the one-shot deck-outline phase."""

    deck_outline: dict[str, Any]
    global_style_spec: dict[str, Any]
    source_text: str
    requested_page_count: int


class DeckProcessorPlanningState(TypedDict, total=False):
    """Deterministic renderer adapters derived from ``deck_outline``."""

    deck_plan: dict[str, Any]
    # Deterministic recipe variants are allocated with the plan so parallel
    # slide branches do not need another branch's model output for diversity.
    layout_preferences: dict[str, dict[str, Any]]


class DeckProcessorDesignState(TypedDict, total=False):
    selected_template_id: str
    design_spec: dict[str, Any]


class DeckProcessorSlideState(TypedDict, total=False):
    current_slide_index: int
    current_slide_outline: dict[str, Any]
    current_slide_plan: dict[str, Any]
    current_slide_design_plan: dict[str, Any]
    current_slide_content_spec: dict[str, Any]
    current_html_slide_spec: dict[str, Any]
    current_assembled_html: str
    current_diagnostics: list[dict[str, Any]]
    current_repair_attempts: int
    current_fallback_used: bool
    current_render_decision: Literal["valid", "repair", "fallback"]
    current_speculative_preview_path: str
    current_speculative_preview_hash: str
    current_preview_path: str
    current_preview_hash: str
    current_renderer_metadata: dict[str, Any]
    current_layout_preference: dict[str, Any]
    previous_compositions: list[dict[str, Any]]


class DeckProcessorResultState(TypedDict, total=False):
    # Canonical idempotent output map. Nodes replace values by slide_id; they
    # never use an append reducer that could duplicate a replayed page.
    completed_slides: Annotated[dict[str, dict[str, Any]], merge_completed_slides]
    layout_history: Annotated[list[dict[str, Any]], merge_layout_history]
    validation_issues: Annotated[list[dict[str, Any]], merge_validation_issues]


class SlideGenerationOutput(TypedDict, total=False):
    """Only these reducer-backed fields cross a parallel slide boundary."""

    completed_slides: dict[str, dict[str, Any]]
    layout_history: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]


class DeckPostprocessorState(TypedDict, total=False):
    artifact_path: str


class DeckGraphState(
    DeckRunState,
    DeckInputState,
    DeckPreprocessorState,
    DeckProcessorPlanningState,
    DeckProcessorDesignState,
    DeckProcessorSlideState,
    DeckProcessorResultState,
    DeckPostprocessorState,
    total=False,
):
    """Complete state shared by the root graph and isolated slide branches."""


__all__ = [
    "DeckGraphState",
    "DeckInputState",
    "DeckPostprocessorState",
    "DeckPreprocessorState",
    "DeckProcessorDesignState",
    "DeckProcessorPlanningState",
    "DeckProcessorResultState",
    "DeckProcessorSlideState",
    "DeckRunState",
    "SlideGenerationOutput",
    "merge_completed_slides",
    "merge_layout_history",
    "merge_validation_issues",
]
