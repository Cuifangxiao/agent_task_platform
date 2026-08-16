"""Build the immutable renderer design contract from the global outline palette."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.deck.agents.processor.layout import deck_plan_markdown
from core.deck.design_system.catalog import get_recipe
from core.deck.domain.schemas import (
    DeckOutlineSpec,
    DeckPlan,
    DesignSpec,
    GlobalStyleSpec,
    PROMPT_VERSION,
    PaletteSpec,
    RENDERER_VERSION,
    ShapeStyleSpec,
    SpacingSpec,
    TypeToken,
    TypographySpec,
)
from core.deck.workspace.paths import DeckProjectPaths


# Fixed renderer defaults for the whole deck.  Stage one owns every color via
# GlobalStyleSpec; typography, spacing, and shape geometry are stable renderer
# contracts that do not vary with topic, audience, or any template catalog.
DEFAULT_TYPOGRAPHY = TypographySpec(
    chinese_font="Noto Sans SC",
    latin_font="Inter",
    cover_title=TypeToken(size_px=76, weight=800, line_height=1.05, max_lines=2),
    page_title=TypeToken(size_px=48, weight=700, line_height=1.14, max_lines=2),
    body=TypeToken(size_px=23, weight=400, line_height=1.5, max_lines=8),
    caption=TypeToken(size_px=16, weight=500, line_height=1.4, max_lines=3),
)
DEFAULT_SPACING = SpacingSpec(canvas_margin=80, base_unit=8, column_gap=40)
DEFAULT_SHAPE_STYLE = ShapeStyleSpec(radius=8, line_width=2, shadow="none")
DEFAULT_ICON_STYLE = "Lucide outline, 2px stroke, currentColor"
DEFAULT_BACKGROUND_STYLE = "clean neutral field"
DEFAULT_MOTIF = "subtle geometric accents and editorial rules"
DEFAULT_DENSITY_POLICY = (
    "alternate dense evidence pages with breathing statement pages; avoid equal card grids"
)

_NEUTRAL_PALETTE = PaletteSpec(
    background="#FFFFFF",
    surface="#F5F5F5",
    text="#111111",
    muted="#555555",
    accent="#0057B8",
    secondary="#6B4EFF",
    divider="#C7C7C7",
)


def _palette_from_global(colors: GlobalStyleSpec) -> PaletteSpec:
    return PaletteSpec(
        background=colors.background_color,
        surface=colors.surface_color,
        text=colors.text_primary,
        muted=colors.text_muted,
        accent=colors.primary_color,
        secondary=colors.secondary_color,
        divider=colors.border_color,
    )


def default_design_spec(
    global_style: GlobalStyleSpec | None = None,
    *,
    background_style: str = DEFAULT_BACKGROUND_STYLE,
) -> DesignSpec:
    """Return the fixed renderer design contract with one color source."""

    return DesignSpec(
        palette=_palette_from_global(global_style) if global_style else _NEUTRAL_PALETTE,
        global_style_spec=global_style,
        typography=DEFAULT_TYPOGRAPHY,
        spacing=DEFAULT_SPACING,
        shape_style=DEFAULT_SHAPE_STYLE,
        icon_style=DEFAULT_ICON_STYLE,
        background_style=background_style or DEFAULT_BACKGROUND_STYLE,
        motif=DEFAULT_MOTIF,
        density_policy=DEFAULT_DENSITY_POLICY,
    )


def create_design_spec(outline: DeckOutlineSpec) -> DesignSpec:
    """Derive the renderer design contract from the outline palette.

    Stage one's ``GlobalStyleSpec`` remains the only palette source; every
    other design token comes from the fixed renderer defaults above.
    """

    return default_design_spec(
        outline.metadata.global_style_spec,
        background_style=outline.metadata.design_style,
    )


def persist_design_artifacts(
    project: DeckProjectPaths,
    *,
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
    _atomic_write_json(project.design_spec_json, design_payload)
    _atomic_write_json(project.spec_lock_json, execution_lock)
    _atomic_write_json(
        project.root / "page-blueprints.json",
        {"schema": "html-page-blueprints.v2", "pages": blueprints},
    )
    _atomic_write_text(project.outline_markdown, deck_plan_markdown(plan))
    _atomic_write_text(project.design_spec_markdown, _design_markdown(design, spec_hash))
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


def _design_markdown(design: DesignSpec, spec_hash: str) -> str:
    return "\n".join(
        [
            "# HTML Deck Design Specification",
            "",
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


__all__ = ["create_design_spec", "default_design_spec", "persist_design_artifacts"]
