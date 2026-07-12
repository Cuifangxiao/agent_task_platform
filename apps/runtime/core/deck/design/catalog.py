"""Load the active presentation template catalog used by Strategist and Artist."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.deck.spec import ThemeTokens, default_theme


CATALOG_PATH = Path(__file__).resolve().parent.parent / "design_system" / "template_packs.json"


@dataclass(frozen=True)
class TemplatePack:
    template_id: str
    name: str
    communication_mode: str
    template_mode: str
    summary: str
    visual_style: str
    theme: ThemeTokens
    layout_principles: list[str]
    layouts: list[dict[str, Any]]
    icon_strategy: dict[str, Any]
    image_strategy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "communication_mode": self.communication_mode,
            "template_mode": self.template_mode,
            "summary": self.summary,
            "visual_style": self.visual_style,
            "theme": self.theme.to_dict(),
            "layout_principles": list(self.layout_principles),
            "layouts": [dict(item) for item in self.layouts],
            "icon_strategy": dict(self.icon_strategy),
            "image_strategy": self.image_strategy,
        }


@lru_cache(maxsize=1)
def list_template_packs() -> tuple[TemplatePack, ...]:
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return (_fallback_pack(),)
    packs: list[TemplatePack] = []
    for item in raw.get("packs") or []:
        if not isinstance(item, dict):
            continue
        template_id = str(item.get("template_id") or "").strip()
        if not template_id:
            continue
        packs.append(
            TemplatePack(
                template_id=template_id,
                name=str(item.get("name") or template_id).strip(),
                communication_mode=str(item.get("communication_mode") or "general").strip(),
                template_mode=str(item.get("template_mode") or "free_design").strip(),
                summary=str(item.get("summary") or "").strip(),
                visual_style=str(item.get("visual_style") or "").strip(),
                theme=ThemeTokens.from_dict(item.get("theme") or {}),
                layout_principles=[str(value).strip() for value in item.get("layout_principles") or [] if str(value).strip()],
                layouts=[dict(value) for value in item.get("layouts") or [] if isinstance(value, dict)],
                icon_strategy=dict(item.get("icon_strategy") or {}),
                image_strategy=str(item.get("image_strategy") or "none").strip(),
            )
        )
    return tuple(packs or [_fallback_pack()])


def get_template_pack(template_id: str, *, fallback: str = "editorial_freeform") -> TemplatePack:
    by_id = {pack.template_id: pack for pack in list_template_packs()}
    return by_id.get(str(template_id or "").strip(), by_id.get(fallback, next(iter(by_id.values()))))


def public_template_catalog() -> list[dict[str, Any]]:
    return [
        {
            "template_id": pack.template_id,
            "name": pack.name,
            "communication_mode": pack.communication_mode,
            "template_mode": pack.template_mode,
            "summary": pack.summary,
            "visual_style": pack.visual_style,
        }
        for pack in list_template_packs()
    ]


def compatible_layouts(pack: TemplatePack, *, component: str, rhythm: str) -> list[dict[str, Any]]:
    exact = [layout for layout in pack.layouts if component in layout.get("components", []) and rhythm in layout.get("rhythms", [])]
    if exact:
        return exact
    component_matches = [layout for layout in pack.layouts if component in layout.get("components", [])]
    if component_matches:
        return component_matches
    rhythm_matches = [layout for layout in pack.layouts if rhythm in layout.get("rhythms", [])]
    return rhythm_matches or list(pack.layouts)


def template_svg_source(template_id: str, layout_id: str = "") -> str:
    """Return a complete restricted-SVG reference for one catalog layout."""

    # Keep this stable compatibility entry point while the renderer lives in a
    # dedicated module.  The local import avoids a catalog/sources import cycle.
    from core.deck.design.sources import render_template_reference_svg

    return render_template_reference_svg(template_id, layout_id)


def _fallback_pack() -> TemplatePack:
    return TemplatePack(
        template_id="editorial_freeform",
        name="Editorial Freeform",
        communication_mode="general",
        template_mode="free_design",
        summary="Editorial free design with deliberate rhythm changes.",
        visual_style="editorial minimalism",
        theme=default_theme(),
        layout_principles=["Conclusion-led titles", "Asymmetric composition", "Use whitespace as structure"],
        layouts=[
            {
                "layout_id": "editorial_split",
                "components": ["content-basic", "comparison", "chart-takeaway"],
                "rhythms": ["dense", "breathing"],
                "composition": "Asymmetric 7:3 evidence and implication split",
                "geometry": {"primary": [70, 180, 760, 430], "secondary": [875, 210, 330, 360]},
            }
        ],
        icon_strategy={"library": "geometric", "weight": "medium"},
        image_strategy="optional",
    )


__all__ = [
    "TemplatePack",
    "compatible_layouts",
    "get_template_pack",
    "list_template_packs",
    "public_template_catalog",
    "template_svg_source",
]
