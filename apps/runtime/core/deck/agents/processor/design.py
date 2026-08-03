"""Build the immutable renderer design contract from the global outline palette."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.deck.agents.processor.layout import deck_plan_markdown
from core.deck.design_system.catalog import ThemeDefinition, get_recipe, select_html_theme
from core.deck.domain.schemas import (
    DeckOutlineSpec,
    DeckPlan,
    DesignSpec,
    PROMPT_VERSION,
    PaletteSpec,
    RENDERER_VERSION,
)
from core.deck.workspace.paths import DeckProjectPaths


def create_design_spec(outline: DeckOutlineSpec) -> tuple[ThemeDefinition, DesignSpec]:
    """Reuse tested typography/geometry while sourcing every color from stage one."""

    theme = select_html_theme(
        "auto",
        " ".join(
            [
                outline.title,
                outline.subtitle,
                outline.metadata.design_style,
                outline.metadata.source_summary,
            ]
        ),
    )
    baseline = theme.to_design_spec()
    colors = outline.metadata.global_style_spec
    palette = PaletteSpec(
        background=colors.background_color,
        surface=colors.surface_color,
        text=colors.text_primary,
        muted=colors.text_muted,
        accent=colors.primary_color,
        secondary=colors.secondary_color,
        divider=colors.border_color,
    )
    return theme, baseline.model_copy(
        update={
            "palette": palette,
            "global_style_spec": colors,
            "background_style": outline.metadata.design_style,
        }
    )


def persist_design_artifacts(
    project: DeckProjectPaths,
    *,
    theme: ThemeDefinition,
    design: DesignSpec,
    plan: DeckPlan,
    layout_preferences: dict[str, dict[str, Any]],
) -> None:
    """Persist public design locks derived from the validated outline."""

    design_payload = design.model_dump(mode="json")
    canonical = json.dumps(design_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    spec_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    blueprints = _page_blueprints(plan, layout_preferences)
    execution_lock = {
        "schema": "html-execution-lock.v2",
        "spec_hash": spec_hash,
        "prompt_version": PROMPT_VERSION,
        "renderer_version": RENDERER_VERSION,
        "canvas": {
            "width": design.canvas_width,
            "height": design.canvas_height,
            "aspect_ratio": "16:9",
        },
        "global_style_spec": design.global_style_spec.model_dump(mode="json"),
        "immutable_tokens": design_payload,
        "pages": blueprints,
    }
    template_manifest = {
        "schema": "html-template-manifest.v1",
        "theme_id": theme.theme_id,
        "theme_name": theme.name,
        "theme_catalog": "design_system/themes.json",
        "recipe_catalog": "design_system/recipes.json",
        "source": "native HTML theme catalog",
        "license": "Project-authored themes; Lucide icons are separately licensed.",
        "recipe_families": sorted({page["layout_family"] for page in blueprints}),
    }
    _atomic_write_json(project.design_spec_json, design_payload)
    _atomic_write_json(project.spec_lock_json, execution_lock)
    _atomic_write_json(
        project.root / "page-blueprints.json",
        {"schema": "html-page-blueprints.v2", "pages": blueprints},
    )
    _atomic_write_json(project.root / "template-manifest.json", template_manifest)
    _atomic_write_text(project.outline_markdown, deck_plan_markdown(plan))
    _atomic_write_text(project.design_spec_markdown, _design_markdown(theme, design, spec_hash))
    _atomic_write_text(project.spec_lock_markdown, _lock_markdown(execution_lock))


def _page_blueprints(
    plan: DeckPlan,
    layout_preferences: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for slide in plan.slides:
        recipe = get_recipe(slide.layout_family)
        preference = layout_preferences.get(slide.slide_id) or {}
        result.append(
            {
                "slide_id": slide.slide_id,
                "index": slide.index,
                "role": slide.role.value,
                "assertion_title": slide.assertion_title,
                "layout_family": slide.layout_family.value,
                "rhythm": slide.rhythm.value,
                "layout_variant": str(preference.get("layout_variant") or ""),
                "recipe": recipe.prompt_manifest(),
            }
        )
    return result


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _design_markdown(theme: ThemeDefinition, design: DesignSpec, spec_hash: str) -> str:
    return "\n".join(
        [
            "# HTML Deck Design Specification",
            "",
            f"- Theme geometry: `{theme.theme_id}` — {theme.name}",
            "- Palette source: `deck-outline.json.metadata.global_style_spec`",
            f"- Canvas: {design.canvas_width} × {design.canvas_height}",
            f"- Fonts: {design.typography.chinese_font} / {design.typography.latin_font}",
            f"- Motif: {design.motif}",
            f"- Density: {design.density_policy}",
            f"- Spec hash: `{spec_hash}`",
            "",
            "The JSON file is the machine-readable source of truth.",
            "",
        ]
    )


def _lock_markdown(lock: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# HTML Execution Lock",
            "",
            f"- Spec hash: `{lock['spec_hash']}`",
            f"- Prompt version: `{lock['prompt_version']}`",
            f"- Renderer version: `{lock['renderer_version']}`",
            f"- Pages: {len(lock['pages'])}",
            "",
        ]
    )


__all__ = ["create_design_spec", "persist_design_artifacts"]
