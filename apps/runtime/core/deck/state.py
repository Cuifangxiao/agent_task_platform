"""Serializable state contract for the HTML-first LangGraph workflow.

Only request data, schema-validated dictionaries, diagnostics, and lightweight
artifact metadata enter the graph state. Browser objects, LLM clients, file
handles, and image bytes remain request-scoped dependencies outside the state.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class DeckRunState(TypedDict, total=False):
    schema_version: str
    prompt_version: str
    renderer_version: str
    task_id: str
    job_id: str
    deck_id: str
    request_id: str
    project_dir: str
    status: Literal[
        "created",
        "planning",
        "designing",
        "generating",
        "exporting",
        "completed",
        "failed",
    ]
    last_error: dict[str, Any] | None


class DeckInputState(TypedDict, total=False):
    deck_title: str
    user_description: str
    uploaded_file_name: str
    uploaded_file_text: str
    source_chunks: list[dict[str, Any]]
    # Retained for input compatibility; the HTML v2 workflow intentionally
    # does not expose uploaded visual assets to any generation node.
    source_images: list[dict[str, Any]]
    expected_slide_count: int
    language: str
    aspect_ratio: str
    requested_template_id: str
    generation_mode: str


class DeckPlanningState(TypedDict, total=False):
    brief: dict[str, Any]
    deck_plan: dict[str, Any]
    # Compatibility/audit projections. They are derived from deck_plan and are
    # never canonical model boundaries in v2.
    plan: dict[str, Any]
    outline_markdown: str


class DeckDesignState(TypedDict, total=False):
    selected_template_id: str
    design_spec: dict[str, Any]
    page_blueprints: list[dict[str, Any]]
    template_manifest: dict[str, Any]
    execution_lock: dict[str, Any]
    # Legacy compatibility aliases; both point at the canonical design_spec.
    design_contract: dict[str, Any]
    theme: dict[str, Any]
    art_direction: dict[str, Any]


class DeckCurrentSlideState(TypedDict, total=False):
    current_slide_index: int
    current_slide_id: str
    current_slide_plan: dict[str, Any]
    current_slide_content_spec: dict[str, Any]
    current_html_slide_spec: dict[str, Any]
    current_sanitized_body_html: str
    current_sanitized_page_css: str
    current_assembled_html: str
    current_diagnostics: list[dict[str, Any]]
    current_repair_attempts: int
    current_fallback_used: bool
    current_render_decision: Literal["valid", "repair", "fallback"]
    current_preview_path: str
    current_preview_hash: str
    current_renderer_metadata: dict[str, Any]
    current_raw_debug_path: str


class DeckGenerationState(TypedDict, total=False):
    # Canonical idempotent output map. Nodes replace values by slide_id; they
    # never use an append reducer that could duplicate a replayed page.
    completed_slides: dict[str, dict[str, Any]]
    layout_history: list[dict[str, str]]
    validation_issues: list[dict[str, Any]]
    # Compatibility projections consumed by the existing service and reports.
    slides: list[dict[str, Any]]
    raw_slides: list[dict[str, Any]]
    final_slides: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    deck_spec: dict[str, Any]


class DeckOutputState(TypedDict, total=False):
    artifact_path: str
    pptx_artifact: dict[str, Any]
    export_degraded: bool
    generation_manifest: dict[str, Any]


class DeckGraphState(
    DeckRunState,
    DeckInputState,
    DeckPlanningState,
    DeckDesignState,
    DeckCurrentSlideState,
    DeckGenerationState,
    DeckOutputState,
    total=False,
):
    """Complete state shared by the flat, explicit deck graph."""


__all__ = [
    "DeckCurrentSlideState",
    "DeckDesignState",
    "DeckGenerationState",
    "DeckGraphState",
    "DeckInputState",
    "DeckOutputState",
    "DeckPlanningState",
    "DeckRunState",
]
