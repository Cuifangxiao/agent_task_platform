"""Global theme selection and immutable DesignSpec nodes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.pipeline.events as events
from core.deck.design_system.catalog import (
    ThemeDefinition,
    get_recipe,
    list_html_themes,
    select_html_theme,
)
from core.deck.runtime.llm import (
    ModelInvocationError,
    StructuredOutputError,
    invoke_structured_with_retries,
    load_node_prompt,
    normalize_llm_registry,
)
from core.deck.workspace.paths import DeckProjectPaths
from core.deck.runtime.metrics import record_fallback
from core.deck.domain.schemas import DeckBrief, DeckPlan, DesignSpec, PROMPT_VERSION, RENDERER_VERSION
from core.deck.pipeline.state import DeckGraphState


class DesignNodes:
    def __init__(self, llms: Any, output_dir: Path) -> None:
        self.llm_registry = normalize_llm_registry(llms)
        self.output_dir = Path(output_dir)

    def select_template(self, state: DeckGraphState) -> dict[str, Any]:
        return select_template(state)

    def create_design_spec(self, state: DeckGraphState) -> dict[str, Any]:
        return create_design_spec(state, self.output_dir, self.llm_registry)


def select_template(state: DeckGraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("select_template", "Selecting one complete HTML theme"))
    brief = DeckBrief.model_validate(state.get("brief") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    context = " ".join(
        [
            brief.topic,
            brief.audience,
            brief.goal,
            brief.tone,
            *brief.style_preferences,
            *brief.brand_preferences,
            plan.core_message,
        ]
    )
    theme = select_html_theme(str(state.get("requested_template_id") or "auto"), context)
    writer(events.template_selected(theme.name, theme.theme_id))
    return {
        "selected_template_id": theme.theme_id,
        "status": "designing",
    }


def create_design_spec(
    state: DeckGraphState,
    output_dir: Path,
    llm_registry: dict[str, Any],
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("create_design_spec", "Locking the global HTML design system"))
    brief = DeckBrief.model_validate(state.get("brief") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    selected = next(
        theme
        for theme in list_html_themes()
        if theme.theme_id == str(state.get("selected_template_id") or "")
    )
    baseline = selected.to_design_spec()
    design = baseline
    style_llm = llm_registry.get("style")
    if style_llm is not None:
        try:
            proposed = invoke_structured_with_retries(
                style_llm,
                DesignSpec,
                system=load_node_prompt("design_director"),
                user=json.dumps(
                    {
                        "brief": brief.model_dump(mode="json", exclude={"source_text"}),
                        "deck_plan": plan.model_dump(mode="json"),
                        "selected_theme": selected.model_dump(mode="json"),
                        "rules": {
                            "keep_theme_id": selected.theme_id,
                            "approved_chinese_font": baseline.typography.chinese_font,
                            "approved_latin_font": baseline.typography.latin_font,
                            "immutable_catalog_tokens": [
                                "palette",
                                "typography",
                                "spacing",
                                "shape_style",
                                "icon_style",
                            ],
                            "no_remote_resources": True,
                        },
                    },
                    ensure_ascii=False,
                ),
                attempts=2,
            )
            design = _lock_approved_identity(proposed, baseline)
            if not _has_readable_contrast(design):
                raise StructuredOutputError("DesignSpec palette does not meet minimum text contrast")
        except StructuredOutputError as exc:
            record_fallback()
            writer(
                events.stage_progress(
                    "create_design_spec",
                    f"DesignSpec was outside the safe token contract; used the selected theme: {exc}",
                    {"status": "warning"},
                )
            )
            design = baseline
        except ModelInvocationError:
            raise
    else:
        record_fallback()

    design_payload = design.model_dump(mode="json")
    canonical = json.dumps(design_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    spec_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    blueprints = _page_blueprints(plan, state.get("layout_preferences") or {})
    template_manifest = {
        "schema": "html-template-manifest.v1",
        "theme_id": selected.theme_id,
        "theme_name": selected.name,
        "theme_catalog": "design_system/themes.json",
        "recipe_catalog": "design_system/recipes.json",
        "source": "native HTML theme catalog",
        "license": "Project-authored themes; Lucide icons are separately licensed.",
        "recipe_families": sorted({page["layout_family"] for page in blueprints}),
    }
    execution_lock = {
        "schema": "html-execution-lock.v1",
        "spec_hash": spec_hash,
        "prompt_version": PROMPT_VERSION,
        "renderer_version": RENDERER_VERSION,
        "canvas": {"width": design.canvas_width, "height": design.canvas_height, "aspect_ratio": "16:9"},
        "immutable_tokens": design_payload,
        "pages": blueprints,
    }
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"])).ensure()
    _atomic_write_json(project.design_spec_json, design_payload)
    _atomic_write_json(project.spec_lock_json, execution_lock)
    _atomic_write_json(project.root / "page-blueprints.json", {"schema": "html-page-blueprints.v1", "pages": blueprints})
    _atomic_write_json(project.root / "template-manifest.json", template_manifest)
    _atomic_write_text(project.design_spec_markdown, _design_markdown(selected, design, spec_hash))
    _atomic_write_text(project.spec_lock_markdown, _lock_markdown(execution_lock))

    writer(
        events.stage_progress(
            "create_design_spec",
            "Global DesignSpec and page recipe roster locked",
            {"status": "done", "page_count": len(blueprints), "template_id": selected.theme_id},
        )
    )
    return {"design_spec": design_payload}


def _lock_approved_identity(proposed: DesignSpec, baseline: DesignSpec) -> DesignSpec:
    """Keep every executable recipe token inside its browser-tested catalog domain.

    The descriptive art-direction fields may still be refined by the style
    model, but fallback geometry is calibrated to the selected theme's palette,
    typography, spacing, shape, and Lucide icon contract.  Accepting arbitrary
    schema-valid values here (for example 120px body text) can make both the
    generated page and the terminal program fallback impossible to render.
    """

    return proposed.model_copy(
        update={
            "theme_id": baseline.theme_id,
            "palette": baseline.palette,
            "typography": baseline.typography,
            "spacing": baseline.spacing,
            "shape_style": baseline.shape_style,
            "icon_style": baseline.icon_style,
        }
    )


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


def _has_readable_contrast(design: DesignSpec) -> bool:
    palette = design.palette
    background = _rgb(palette.background)
    surface = _rgb(palette.surface)
    text = _rgb(palette.text)
    muted = _rgb(palette.muted)
    return all(
        (
            _contrast(background, text) >= 4.5,
            _contrast(background, muted) >= 3.0,
            _contrast(surface, text) >= 4.5,
            _contrast(surface, muted) >= 3.0,
            _contrast(background, _rgb(palette.accent)) >= 3.0,
            _contrast(background, _rgb(palette.secondary)) >= 3.0,
            _contrast(surface, _rgb(palette.accent)) >= 3.0,
            _contrast(surface, _rgb(palette.secondary)) >= 3.0,
        )
    )


def _rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))  # type: ignore[return-value]


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


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
            f"- Theme: `{theme.theme_id}` — {theme.name}",
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


__all__ = ["DesignNodes", "create_design_spec", "select_template"]
