"""Validated HTML theme and layout-recipe catalog.

Themes define the global visual language.  Recipes define page composition.
Keeping them separate gives every theme a complete page set without copying
ten near-identical HTML templates four times.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field

from core.deck.schemas import (
    DesignSpec,
    LayoutFamily,
    PaletteSpec,
    ShapeStyleSpec,
    SpacingSpec,
    StrictModel,
    TypographySpec,
)


HTML_DESIGN_SYSTEM_DIR = Path(__file__).resolve().parent.parent / "design_system" / "html"
THEMES_PATH = HTML_DESIGN_SYSTEM_DIR / "themes.json"
RECIPES_PATH = HTML_DESIGN_SYSTEM_DIR / "recipes.json"


class ThemeDefinition(StrictModel):
    theme_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=400)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    palette: PaletteSpec
    typography: TypographySpec
    spacing: SpacingSpec
    shape_style: ShapeStyleSpec
    icon_style: str
    background_style: str
    motif: str
    density_policy: str

    def to_design_spec(self) -> DesignSpec:
        return DesignSpec.model_validate(
            self.model_dump(exclude={"name", "summary", "keywords"})
        )


class RecipeCapacity(StrictModel):
    title_lines: int = Field(ge=1, le=4)
    body_chars: int = Field(ge=0, le=1200)
    items: int = Field(ge=0, le=12)


class RecipeDefinition(StrictModel):
    family: LayoutFamily
    roles: list[str] = Field(min_length=1, max_length=12)
    purpose: str = Field(min_length=1, max_length=400)
    slots: list[str] = Field(min_length=1, max_length=16)
    capacity: RecipeCapacity
    structure: str = Field(min_length=1, max_length=500)
    variants: list[str] = Field(min_length=1, max_length=12)
    visual_anchors: list[str] = Field(default_factory=list, max_length=10)

    def prompt_manifest(self) -> dict[str, Any]:
        return {
            "family": self.family.value,
            "purpose": self.purpose,
            "slots": self.slots,
            "capacity": self.capacity.model_dump(mode="json"),
            "structure": self.structure,
            "variants": self.variants,
            "visual_anchors": self.visual_anchors,
        }


class ThemeCatalog(StrictModel):
    catalog_schema: str = Field(alias="schema")
    default_theme_id: str
    aliases: dict[str, str]
    themes: list[ThemeDefinition]


class RecipeCatalog(StrictModel):
    catalog_schema: str = Field(alias="schema")
    recipes: list[RecipeDefinition]


@lru_cache(maxsize=1)
def theme_catalog() -> ThemeCatalog:
    return ThemeCatalog.model_validate(json.loads(THEMES_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def recipe_catalog() -> RecipeCatalog:
    catalog = RecipeCatalog.model_validate(json.loads(RECIPES_PATH.read_text(encoding="utf-8")))
    families = [item.family for item in catalog.recipes]
    expected = set(LayoutFamily)
    if set(families) != expected or len(families) != len(expected):
        raise ValueError("recipe catalog must define every layout family exactly once")
    return catalog


def list_html_themes() -> tuple[ThemeDefinition, ...]:
    return tuple(theme_catalog().themes)


def resolve_theme_id(requested_theme_id: str) -> str:
    catalog = theme_catalog()
    requested = str(requested_theme_id or "").strip().casefold()
    if not requested or requested == "auto":
        return catalog.default_theme_id
    aliases = {key.casefold(): value for key, value in catalog.aliases.items()}
    resolved = aliases.get(requested, requested)
    known = {theme.theme_id for theme in catalog.themes}
    return resolved if resolved in known else catalog.default_theme_id


def get_html_theme(theme_id: str) -> ThemeDefinition:
    resolved = resolve_theme_id(theme_id)
    by_id = {theme.theme_id: theme for theme in theme_catalog().themes}
    return by_id[resolved]


def select_html_theme(requested_theme_id: str, context: str) -> ThemeDefinition:
    requested = str(requested_theme_id or "").strip()
    if requested and requested.casefold() != "auto":
        return get_html_theme(requested)
    haystack = str(context or "").casefold()
    catalog = theme_catalog()
    ranked = sorted(
        catalog.themes,
        key=lambda theme: (
            -sum(1 for keyword in theme.keywords if keyword.casefold() in haystack),
            theme.theme_id != catalog.default_theme_id,
            theme.theme_id,
        ),
    )
    return ranked[0]


def get_recipe(family: LayoutFamily | str) -> RecipeDefinition:
    normalized = family if isinstance(family, LayoutFamily) else LayoutFamily(str(family))
    for recipe in recipe_catalog().recipes:
        if recipe.family is normalized:
            return recipe
    raise KeyError(normalized.value)


def recipe_for_role(role: str, *, index: int = 0, total: int = 0) -> RecipeDefinition:
    normalized = str(role or "").strip().casefold()
    if index == 1 or normalized == "cover":
        return get_recipe(LayoutFamily.COVER_MINIMAL)
    if total and index == total or normalized == "closing":
        return get_recipe(LayoutFamily.CLOSING_RESOLUTION)
    matches = [recipe for recipe in recipe_catalog().recipes if normalized in recipe.roles]
    return matches[0] if matches else get_recipe(LayoutFamily.EDITORIAL_SPLIT)


def recipe_prompt_manifest(families: list[str] | None = None) -> list[dict[str, Any]]:
    selected = set(families or [])
    return [
        recipe.prompt_manifest()
        for recipe in recipe_catalog().recipes
        if not selected or recipe.family.value in selected
    ]


__all__ = [
    "HTML_DESIGN_SYSTEM_DIR",
    "RecipeDefinition",
    "ThemeDefinition",
    "get_html_theme",
    "get_recipe",
    "list_html_themes",
    "recipe_catalog",
    "recipe_for_role",
    "recipe_prompt_manifest",
    "resolve_theme_id",
    "select_html_theme",
    "theme_catalog",
]
