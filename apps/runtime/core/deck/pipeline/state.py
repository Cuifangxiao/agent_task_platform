"""Typed state for the uninterrupted PPT graph."""

from __future__ import annotations

from typing import Any, TypedDict


class DeckGraphState(TypedDict, total=False):
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
    brief: dict[str, Any]
    outline_markdown: str
    plan: dict[str, Any]
    style_options: list[dict[str, Any]]
    theme: dict[str, Any]
    image_strategy: str
    art_direction: dict[str, Any]
    design_spec: dict[str, Any]
    execution_lock: dict[str, Any]
    page_blueprints: list[dict[str, Any]]
    asset_manifest: dict[str, Any]
    template_manifest: dict[str, Any]
    design_contract: dict[str, Any]
    slides: list[dict[str, Any]]
    raw_slides: list[dict[str, Any]]
    final_slides: list[dict[str, Any]]
    deck_spec: dict[str, Any]
    artifacts: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]
    artifact_path: str
    generation_manifest: dict[str, Any]


__all__ = ["DeckGraphState"]
