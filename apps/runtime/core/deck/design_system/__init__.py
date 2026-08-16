"""Validated page-composition recipes for the HTML deck pipeline."""

from core.deck.design_system.catalog import (
    DESIGN_SYSTEM_DIR,
    RecipeDefinition,
    get_recipe,
    recipe_prompt_manifest,
)

__all__ = [
    "DESIGN_SYSTEM_DIR",
    "RecipeDefinition",
    "get_recipe",
    "recipe_prompt_manifest",
]
