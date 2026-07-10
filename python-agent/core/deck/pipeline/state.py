"""Typed state shared by deck pipeline nodes."""

from __future__ import annotations

from typing import Any, TypedDict


class DeckGraphState(TypedDict, total=False):
    request_id: str
    deck_title: str
    user_description: str
    uploaded_file_name: str
    uploaded_file_text: str
    source_chunks: list[dict[str, Any]]
    brief: dict[str, Any]
    outline_markdown: str
    outline_action: str
    plan: dict[str, Any]
    style_options: list[dict[str, Any]]
    theme: dict[str, Any]
    slides: list[dict[str, Any]]
    raw_slides: list[dict[str, Any]]
    final_slides: list[dict[str, Any]]
    deck_spec: dict[str, Any]
    artifacts: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]
    repair_round: int
    vlm_reviewed: bool
    coherence_reviewed: bool
    artifact_path: str
    quality_status: str
    generation_mode: str
