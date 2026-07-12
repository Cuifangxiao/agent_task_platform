"""Presentation design catalogs and execution contracts."""

from core.deck.design.catalog import (
    TemplatePack,
    compatible_layouts,
    get_template_pack,
    list_template_packs,
    public_template_catalog,
    template_svg_source,
)
from core.deck.design.sources import render_template_reference_svg, resolve_template_layout
from core.deck.design.visualizations import (
    VisualizationSpec,
    list_visualizations,
    public_visualization_catalog,
    render_visualization_reference_svg,
    select_visualization,
    visualization_svg_source,
)

__all__ = [
    "TemplatePack",
    "VisualizationSpec",
    "compatible_layouts",
    "get_template_pack",
    "list_template_packs",
    "list_visualizations",
    "public_template_catalog",
    "public_visualization_catalog",
    "render_template_reference_svg",
    "render_visualization_reference_svg",
    "resolve_template_layout",
    "select_visualization",
    "template_svg_source",
    "visualization_svg_source",
]
