"""Typed state contracts shared by deck pipeline nodes and agent graphs."""

from __future__ import annotations

from typing import Any, TypedDict


class DeckInputState(TypedDict, total=False):
    """Request-scoped inputs prepared before the graph starts."""

    task_id: str
    # Local rendering/export code uses request_id as its directory key. It is
    # always exactly equal to task_id; no second identifier is generated.
    request_id: str
    project_dir: str
    deck_title: str
    user_description: str
    uploaded_file_name: str
    uploaded_file_text: str
    source_chunks: list[dict[str, Any]]
    source_images: list[dict[str, Any]]
    expected_slide_count: int
    language: str
    aspect_ratio: str
    template_id: str
    generation_mode: str


class DeckPlanningState(TypedDict, total=False):
    """Brief and narrative planning outputs."""

    brief: dict[str, Any]
    outline_markdown: str
    plan: dict[str, Any]


class DeckDesignState(TypedDict, total=False):
    """Selected visual direction and its executable design contract."""

    theme: dict[str, Any]
    image_strategy: str
    art_direction: dict[str, Any]
    design_spec: dict[str, Any]
    execution_lock: dict[str, Any]
    page_blueprints: list[dict[str, Any]]
    asset_manifest: dict[str, Any]
    template_manifest: dict[str, Any]
    design_contract: dict[str, Any]


class DeckGenerationState(TypedDict, total=False):
    """Slide authoring, rendering, and validation results."""

    slides: list[dict[str, Any]]
    raw_slides: list[dict[str, Any]]
    final_slides: list[dict[str, Any]]
    deck_spec: dict[str, Any]
    artifacts: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]


class DeckOutputState(TypedDict, total=False):
    """Final delivery metadata produced by the export stage."""

    artifact_path: str
    export_degraded: bool
    generation_manifest: dict[str, Any]


class DeckGraphState(
    DeckInputState,
    DeckPlanningState,
    DeckDesignState,
    DeckGenerationState,
    DeckOutputState,
    total=False,
):
    """Complete state shared by the assembled deck graph."""


__all__ = [
    "DeckDesignState",
    "DeckGenerationState",
    "DeckGraphState",
    "DeckInputState",
    "DeckOutputState",
    "DeckPlanningState",
]
