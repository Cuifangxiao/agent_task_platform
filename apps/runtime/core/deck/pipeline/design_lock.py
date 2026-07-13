"""Compile the approved narrative and art direction into an executable design lock."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.design.catalog import TemplatePack, compatible_layouts, get_template_pack
from core.deck.design.visualizations import public_visualization_catalog
from core.deck.pipeline.llm import invoke_json_with_retries, load_node_prompt
from core.deck.pipeline.state import DeckGraphState
from core.deck.paths import DeckProjectPaths
from core.deck.runtime_metrics import record_fallback
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, ThemeTokens, default_theme


DESIGN_SPEC_SCHEMA = "ppt-design-spec"
SPEC_LOCK_SCHEMA = "ppt-spec-lock"
PAGE_BLUEPRINT_SCHEMA = "ppt-page-blueprints"
ASSET_MANIFEST_SCHEMA = "ppt-asset-manifest"
TEMPLATE_MANIFEST_SCHEMA = "ppt-template-manifest"

_RHYTHMS = {"anchor", "dense", "breathing"}
_VISUALIZATION_TYPES = {
    "none", "bar", "line", "donut", "timeline", "process", "matrix",
    "funnel", "architecture", "table", "kpi", "comparison", "custom",
}


def freeze_design_contract(
    state: DeckGraphState,
    output_dir: Path,
    llm_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the one authoritative design package used by all page executors."""

    writer = get_stream_writer()
    writer(events.stage_started("design_lock", "Compiling the approved art direction into page blueprints"))
    package = compile_design_package(state, llm_registry=llm_registry)
    project = DeckProjectPaths.for_request(output_dir, str(state["request_id"])).ensure()
    request_dir = project.root

    _write_json(project.design_spec_json, package["design_spec"])
    _write_json(project.spec_lock_json, package["execution_lock"])
    project.design_spec_markdown.write_text(
        _design_spec_markdown(package["design_spec"]), encoding="utf-8"
    )
    project.spec_lock_markdown.write_text(
        _spec_lock_markdown(package["execution_lock"]), encoding="utf-8"
    )
    _write_json(
        request_dir / "page-blueprints.json",
        {
            "schema": PAGE_BLUEPRINT_SCHEMA,
            "spec_hash": package["design_spec"]["spec_hash"],
            "pages": package["page_blueprints"],
        },
    )
    _write_json(request_dir / "asset-manifest.json", package["asset_manifest"])
    _write_json(request_dir / "template-manifest.json", package["template_manifest"])

    template_dir = project.templates
    for page in package["page_blueprints"]:
        svg = str(page.get("template_svg_source") or "").strip()
        relative = str(page.get("template_svg_file") or "").strip()
        if svg and relative:
            (request_dir / relative).write_text(svg, encoding="utf-8")

    template = package["template_manifest"]
    writer(
        events.stage_progress(
            "design_lock",
            "Design specification locked; every page now has a concrete template SVG and provenance",
            {
                "status": "done",
                "spec_hash": package["design_spec"]["spec_hash"],
                "page_count": len(package["page_blueprints"]),
                "template_id": template["template_id"],
                "template_mode": template["template_mode"],
                "template_applied": template["applied"],
                "path": "design-spec.json",
            },
        )
    )
    return {
        **package,
        "project_dir": str(project.root),
        # Existing slide/repair nodes read this key. It is the same authoritative
        # object, not a legacy parallel contract.
        "design_contract": package["design_spec"],
    }


def compile_design_package(
    state: DeckGraphState,
    *,
    llm_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the deterministic package, optionally refined by an explicit Art Director model."""

    art_direction = state.get("art_direction") if isinstance(state.get("art_direction"), dict) else {}
    template_id = str(state.get("template_id") or art_direction.get("template_id") or "editorial_freeform").strip()
    pack = get_template_pack(template_id)
    theme = ThemeTokens.from_dict(state.get("theme") or pack.theme.to_dict() or default_theme().to_dict())
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else {}
    outline_pages = [item for item in plan.get("slides") or [] if isinstance(item, dict)]
    if not outline_pages:
        raise ValueError("approved outline contains no executable page sections")
    asset_manifest = _build_asset_manifest(state.get("source_images") or [])
    selection_source = _template_selection_source(state, art_direction)
    theme_source = "approved_theme_tokens" if isinstance(state.get("theme"), dict) and state.get("theme") else "template_catalog"
    template_manifest = _build_template_manifest(pack, selection_source=selection_source)

    page_blueprints = _deterministic_blueprints(
        outline_pages,
        pack=pack,
        theme=theme,
        asset_manifest=asset_manifest,
        selection_source=selection_source,
        theme_source=theme_source,
    )
    design_narrative = _default_design_narrative(pack, art_direction, page_blueprints)

    # A default/fallback model is deliberately not used here. The Art Director
    # pass runs only when the deployment explicitly assigned a style model.
    style_llm = llm_registry.get("style") if isinstance(llm_registry, dict) else None
    if style_llm is not None and outline_pages:
        try:
            raw = invoke_json_with_retries(
                style_llm,
                system=load_node_prompt("art_director"),
                user=json.dumps(
                    {
                        "approved_outline": plan,
                        "brief": brief,
                        "approved_art_direction": art_direction,
                        "selected_template": pack.to_dict(),
                        "visualization_catalog": public_visualization_catalog(),
                        "available_assets": asset_manifest["assets"],
                        "deterministic_page_blueprints": page_blueprints,
                    },
                    ensure_ascii=False,
                ),
                attempts=2,
            )
            page_blueprints = _merge_art_director_pages(
                raw.get("pages"),
                page_blueprints,
                pack=pack,
                theme=theme,
                asset_manifest=asset_manifest,
                selection_source=selection_source,
                theme_source=theme_source,
            )
            design_narrative = _normalize_design_narrative(raw.get("design_narrative"), design_narrative)
        except ValueError:
            record_fallback()

    design_spec: dict[str, Any] = {
        "schema": DESIGN_SPEC_SCHEMA,
        "canvas": {"format": "ppt169", "width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
        "content": {
            "title": str(plan.get("title") or state.get("deck_title") or "").strip(),
            "narrative": str(plan.get("narrative") or "").strip(),
            "audience": str(brief.get("audience") or "").strip(),
            "goal": str(brief.get("goal") or "").strip(),
            "language": str(brief.get("language") or "zh").strip() or "zh",
        },
        "art_direction": {
            "communication_mode": str(art_direction.get("communication_mode") or pack.communication_mode),
            "visual_style": str(art_direction.get("visual_style") or pack.visual_style),
            "rationale": str(art_direction.get("rationale") or pack.summary),
            "confirmations": dict(art_direction.get("confirmations") or {}),
            "design_narrative": design_narrative,
        },
        "template": template_manifest,
        "visual_system": {
            "theme": theme.to_dict(),
            "typography": {
                "title_font": theme.fonts.get("display"),
                "body_font": theme.fonts.get("body"),
                "type_scale": dict(theme.type_scale),
                "minimum_body_font_px": 16,
            },
            "layout_principles": list(art_direction.get("layout_principles") or pack.layout_principles),
            "icon_strategy": dict(art_direction.get("icon_strategy") or pack.icon_strategy),
            "image_strategy": str(state.get("image_strategy") or art_direction.get("image_strategy") or pack.image_strategy),
            "svg_profile": "native-editable",
        },
        "quality_rules": {
            "single_primary_message": True,
            "vary_adjacent_compositions": True,
            "breathing_pages_forbid_equal_card_grids": True,
            "approved_assets_only": True,
            "require_template_provenance": True,
        },
        "asset_manifest": "asset-manifest.json",
        "page_blueprints": "page-blueprints.json",
        "pages": page_blueprints,
    }
    canonical = json.dumps(design_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    design_spec["spec_hash"] = hashlib.sha256(canonical).hexdigest()
    spec_hash = design_spec["spec_hash"]
    asset_manifest["spec_hash"] = spec_hash

    execution_lock = _build_execution_lock(design_spec, pack=pack, theme=theme)
    return {
        "design_spec": design_spec,
        "execution_lock": execution_lock,
        "page_blueprints": page_blueprints,
        "asset_manifest": asset_manifest,
        "template_manifest": template_manifest,
    }


def build_design_contract(
    state: DeckGraphState,
    llm_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the authoritative design spec for callers that do not persist it."""

    return compile_design_package(state, llm_registry=llm_registry)["design_spec"]


def page_contract(state: DeckGraphState, page_index: int) -> dict[str, Any]:
    contract = state.get("design_contract") if isinstance(state.get("design_contract"), dict) else {}
    pages = contract.get("pages") or state.get("page_blueprints") or []
    for item in pages:
        if isinstance(item, dict) and int(item.get("index") or 0) == page_index:
            return item
    return {}


def _deterministic_blueprints(
    outline_pages: list[dict[str, Any]],
    *,
    pack: TemplatePack,
    theme: ThemeTokens,
    asset_manifest: dict[str, Any],
    selection_source: str,
    theme_source: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    previous_layout = ""
    approved_asset_ids = [str(item["asset_id"]) for item in asset_manifest["assets"]]
    for offset, item in enumerate(outline_pages):
        index = int(item.get("index") or offset + 1)
        component = str(item.get("component") or "content-basic").strip()
        rhythm = _normalize_rhythm(item.get("page_rhythm"), component=component, index=index)
        candidates = compatible_layouts(pack, component=component, rhythm=rhythm)
        if not candidates:
            candidates = [
                {
                    "layout_id": f"{pack.template_id}-content",
                    "composition": "Title, primary evidence, and implication inside the safe canvas",
                    "geometry": {"content": [72, 176, 1136, 430]},
                }
            ]
        layout = min(
            candidates,
            key=lambda value: (
                int(str(value.get("layout_id") or "") == previous_layout),
                usage.get(str(value.get("layout_id") or ""), 0),
                str(value.get("layout_id") or ""),
            ),
        )
        layout_id = str(layout.get("layout_id") or f"{pack.template_id}-page").strip()
        usage[layout_id] = usage.get(layout_id, 0) + 1
        previous_layout = layout_id
        geometry = _normalize_geometry(layout.get("geometry"))
        visualization = _normalize_visualization(item.get("visualization"), component=component, evidence=item.get("evidence"))
        asset_ids = _deterministic_asset_ids(item, approved_asset_ids)
        provenance = _template_provenance(pack, layout_id, selection_source=selection_source, theme_source=theme_source)
        template_svg = render_template_svg(pack, layout_id=layout_id, geometry=geometry, theme=theme)
        result.append(
            {
                "index": index,
                "title": str(item.get("title") or "").strip(),
                "key_message": str(item.get("key_message") or "").strip(),
                "role": str(item.get("role") or "evidence").strip(),
                "component": component,
                "page_rhythm": rhythm,
                "layout_id": layout_id,
                "layout_variant": _layout_variant(component, rhythm, str(layout.get("composition") or "")),
                "template_id": pack.template_id,
                "template_mode": pack.template_mode,
                "composition": str(item.get("layout_intent") or layout.get("composition") or "").strip(),
                "geometry": geometry,
                "reading_order": _reading_order(component),
                "visualization": visualization,
                "chart_strategy": "native-svg" if visualization["type"] in {"bar", "line", "donut"} else "none",
                "evidence": str(item.get("evidence") or "").strip(),
                "asset_ids": asset_ids,
                "emphasis": str(item.get("key_message") or item.get("title") or "").strip(),
                "avoid": _page_avoid_rules(component, rhythm, previous=result[-1] if result else None),
                "template_svg_file": f"templates/page-{index:03d}.svg",
                "template_svg_source": template_svg,
                "template_provenance": provenance,
            }
        )
    return result


def _merge_art_director_pages(
    raw_pages: Any,
    base_pages: list[dict[str, Any]],
    *,
    pack: TemplatePack,
    theme: ThemeTokens,
    asset_manifest: dict[str, Any],
    selection_source: str,
    theme_source: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_pages, list):
        return base_pages
    by_index = {
        int(item.get("index") or 0): item
        for item in raw_pages
        if isinstance(item, dict) and _positive_int(item.get("index"))
    }
    valid_layouts = {str(item.get("layout_id") or ""): item for item in pack.layouts}
    approved_assets = {str(item.get("asset_id") or "") for item in asset_manifest["assets"]}
    merged: list[dict[str, Any]] = []
    previous_layout = ""
    for base in base_pages:
        raw = by_index.get(int(base["index"])) or {}
        compatible_ids = {
            str(item.get("layout_id") or "")
            for item in compatible_layouts(pack, component=str(base["component"]), rhythm=str(base["page_rhythm"]))
        }
        proposed_layout = str(raw.get("layout_id") or "").strip()
        layout_id = proposed_layout if proposed_layout in valid_layouts and proposed_layout in compatible_ids else str(base["layout_id"])
        if layout_id == previous_layout and len(compatible_ids) > 1:
            alternatives = sorted(compatible_ids - {layout_id})
            layout_id = alternatives[0]
        previous_layout = layout_id
        layout = valid_layouts.get(layout_id) or {}
        geometry = _normalize_geometry(layout.get("geometry") or base.get("geometry"))
        reading_order = [str(value).strip() for value in raw.get("reading_order") or [] if str(value).strip()][:6]
        asset_ids = [str(value) for value in raw.get("asset_ids") or [] if str(value) in approved_assets]
        avoid = [str(value).strip() for value in raw.get("avoid") or [] if str(value).strip()][:6]
        visualization = base["visualization"]
        if visualization.get("type") == "none" and isinstance(raw.get("visualization"), dict):
            visualization = _normalize_visualization(raw["visualization"], component=str(base["component"]), evidence=base.get("evidence"))
        composition = str(raw.get("composition") or layout.get("composition") or base.get("composition") or "").strip()
        provenance = _template_provenance(
            pack,
            layout_id,
            selection_source=selection_source,
            theme_source=theme_source,
            art_director_refined=True,
        )
        merged.append(
            {
                **base,
                "layout_id": layout_id,
                "layout_variant": _layout_variant(str(base["component"]), str(base["page_rhythm"]), composition),
                "composition": composition,
                "geometry": geometry,
                "reading_order": reading_order or base["reading_order"],
                "visualization": visualization,
                "chart_strategy": "native-svg" if visualization["type"] in {"bar", "line", "donut"} else "none",
                "asset_ids": asset_ids,
                "emphasis": str(raw.get("emphasis") or base["emphasis"]).strip(),
                "avoid": avoid or base["avoid"],
                "template_svg_source": render_template_svg(pack, layout_id=layout_id, geometry=geometry, theme=theme),
                "template_provenance": provenance,
            }
        )
    return merged


def render_template_svg(
    pack: TemplatePack,
    *,
    layout_id: str,
    geometry: dict[str, list[int]],
    theme: ThemeTokens,
) -> str:
    """Materialize the selected template and approved tokens as the Artist's SVG source."""

    del geometry  # The catalog renderer resolves and validates its own geometry.
    from core.deck.design.sources import render_template_reference_svg

    return render_template_reference_svg(pack.template_id, layout_id, theme=theme)


def _build_template_manifest(pack: TemplatePack, *, selection_source: str) -> dict[str, Any]:
    applied = pack.template_mode == "layout_pack"
    return {
        "schema": TEMPLATE_MANIFEST_SCHEMA,
        "template_id": pack.template_id,
        "template_name": pack.name,
        "template_mode": pack.template_mode,
        "applied": applied,
        "structural_pack_used": True,
        "selection_source": selection_source,
        "catalog_source": "design_system/template_packs.json",
        "source": dict(pack.source),
        "summary": pack.summary,
        "available_layout_ids": [str(item.get("layout_id") or "") for item in pack.layouts],
    }


def _build_asset_manifest(source_images: Any) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for offset, item in enumerate(source_images if isinstance(source_images, list) else []):
        if not isinstance(item, dict):
            continue
        asset_id = _safe_id(str(item.get("asset_id") or item.get("id") or f"image-{offset + 1:02d}"))
        if asset_id in used_ids:
            asset_id = f"{asset_id}-{offset + 1:02d}"
        used_ids.add(asset_id)
        filename = Path(str(item.get("filename") or item.get("name") or f"image-{offset + 1:02d}")).name
        assets.append(
            {
                "asset_id": asset_id,
                "type": "image",
                "filename": filename,
                "relative_path": f"images/{filename}",
                "href": str(item.get("svg_href") or f"../images/{filename}"),
                "mime_type": str(item.get("mime_type") or item.get("content_type") or "").strip(),
                "width": _positive_int(item.get("width")),
                "height": _positive_int(item.get("height")),
                "status": "approved",
                "source": "user_upload",
                "original_name": str(item.get("original_name") or "").strip(),
                "sha256": str(item.get("sha256") or "").strip(),
            }
        )
    return {
        "schema": ASSET_MANIFEST_SCHEMA,
        "policy": "Only assets with status=approved may be referenced by SVG pages.",
        "assets": assets,
    }


def _build_execution_lock(design_spec: dict[str, Any], *, pack: TemplatePack, theme: ThemeTokens) -> dict[str, Any]:
    return {
        "schema": SPEC_LOCK_SCHEMA,
        "spec_hash": design_spec["spec_hash"],
        "design_spec": "design-spec.json",
        "canvas": dict(design_spec["canvas"]),
        "template": {
            "template_id": pack.template_id,
            "template_mode": pack.template_mode,
            "catalog_source": "design_system/template_packs.json",
        },
        "immutable_visual_tokens": {
            "palette": dict(theme.palette),
            "fonts": dict(theme.fonts),
            "type_scale": dict(theme.type_scale),
            "spacing_unit": theme.spacing_unit,
            "radius": theme.radius,
            "motif": theme.motif,
            "minimum_body_font_px": max(16, int(theme.type_scale.get("body") or 18)),
        },
        "page_locks": [
            {
                "index": page["index"],
                "component": page["component"],
                "page_rhythm": page["page_rhythm"],
                "layout_id": page["layout_id"],
                "composition": page["composition"],
                "geometry": page["geometry"],
                "reading_order": page["reading_order"],
                "visualization": page["visualization"],
                "asset_ids": page["asset_ids"],
                "emphasis": page["emphasis"],
                "avoid": page["avoid"],
                "template_svg_file": page["template_svg_file"],
                "template_provenance": page["template_provenance"],
            }
            for page in design_spec["pages"]
        ],
        "executor_rules": {
            "must_use_page_blueprint": True,
            "must_preserve_template_geometry": True,
            "must_reference_approved_assets_only": True,
            "must_return_one_1280x720_svg": True,
            "must_not_repeat_adjacent_layouts_when_alternatives_exist": True,
        },
    }


def _default_design_narrative(
    pack: TemplatePack,
    art_direction: dict[str, Any],
    pages: list[dict[str, Any]],
) -> dict[str, str]:
    rhythms = " → ".join(str(page["page_rhythm"]) for page in pages)
    return {
        "style_objective": f"{art_direction.get('communication_mode') or pack.communication_mode} + {art_direction.get('visual_style') or pack.visual_style}",
        "visual_throughline": pack.theme.motif,
        "rhythm_plan": rhythms,
        "typography_rationale": "Use display type for conclusion-led titles and body type only for supporting evidence.",
        "template_usage": "Free-design structural guide" if pack.template_mode == "free_design" else "Apply the selected layout pack, then adapt visual weight to each page.",
        "image_policy": str(art_direction.get("image_strategy") or pack.image_strategy),
        "icon_policy": str(pack.icon_strategy.get("rule") or "Use one consistent icon language."),
    }


def _normalize_design_narrative(raw: Any, fallback: dict[str, str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return fallback
    return {key: str(raw.get(key) or value).strip() for key, value in fallback.items()}


def _template_selection_source(state: DeckGraphState, art_direction: dict[str, Any]) -> str:
    if art_direction:
        return "approved_art_direction"
    requested = str(state.get("template_id") or "").strip()
    return "user_request" if requested and requested != "auto" else "system_recommendation"


def _template_provenance(
    pack: TemplatePack,
    layout_id: str,
    *,
    selection_source: str,
    theme_source: str,
    art_director_refined: bool = False,
) -> dict[str, Any]:
    return {
        "template_id": pack.template_id,
        "template_mode": pack.template_mode,
        "layout_id": layout_id,
        "selection_source": selection_source,
        "catalog_source": "design_system/template_packs.json",
        "source": dict(pack.source),
        "geometry_source": f"template_packs[{pack.template_id}].layouts[{layout_id}].geometry",
        "theme_source": "approved art-direction tokens" if theme_source == "approved_theme_tokens" else f"template_packs[{pack.template_id}].theme",
        "svg_generator": "core.deck.design.sources.render_template_reference_svg",
        "art_director_refined": art_director_refined,
    }


def _normalize_geometry(raw: Any) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            if not isinstance(value, list) or len(value) != 4:
                continue
            try:
                x, y, width, height = (int(number) for number in value)
            except (TypeError, ValueError):
                continue
            x = max(0, min(CANVAS_WIDTH - 1, x))
            y = max(0, min(CANVAS_HEIGHT - 1, y))
            width = max(1, min(CANVAS_WIDTH - x, width))
            height = max(1, min(CANVAS_HEIGHT - y, height))
            result[str(name)] = [x, y, width, height]
    return result or {"content": [72, 176, 1136, 430]}


def _normalize_rhythm(value: Any, *, component: str, index: int) -> str:
    rhythm = str(value or "").strip().casefold()
    if rhythm in _RHYTHMS:
        return rhythm
    if component in {"cover", "section", "end"} or index == 1:
        return "anchor"
    if component in {"quote", "summary"}:
        return "breathing"
    return "dense"


def _normalize_visualization(value: Any, *, component: str, evidence: Any) -> dict[str, str]:
    if isinstance(value, dict):
        raw_type = str(value.get("type") or "none").strip().casefold()
        if raw_type == "pie":
            raw_type = "donut"
        visual_type = raw_type if raw_type in _VISUALIZATION_TYPES else "custom"
        reason = str(value.get("reason") or evidence or "").strip()
        return {"type": visual_type, "reason": reason}
    text = str(value or "").strip()
    lowered = text.casefold()
    visual_type = "none"
    mappings = (
        ("bar", ("bar", "柱", "条形")),
        ("line", ("line", "折线", "趋势")),
        ("donut", ("donut", "pie", "环形", "饼", "占比")),
        ("timeline", ("timeline", "时间线", "里程碑")),
        ("process", ("process", "流程", "步骤")),
        ("matrix", ("matrix", "矩阵", "象限")),
        ("funnel", ("funnel", "漏斗", "转化")),
        ("architecture", ("architecture", "架构", "系统图", "关系图")),
        ("table", ("table", "表格", "明细", "基准")),
        ("kpi", ("kpi", "指标", "大数字", "计分卡")),
        ("comparison", ("comparison", "对比", "比较", "前后")),
    )
    for candidate, tokens in mappings:
        if any(token in lowered for token in tokens):
            visual_type = candidate
            break
    if visual_type == "none" and component == "chart-takeaway":
        visual_type = "bar"
    elif visual_type == "none" and component in {"timeline", "steps"}:
        visual_type = "timeline" if component == "timeline" else "process"
    elif visual_type == "none" and component == "quadrant":
        visual_type = "matrix"
    return {"type": visual_type, "reason": text or str(evidence or "").strip()}


def _deterministic_asset_ids(item: dict[str, Any], approved_asset_ids: list[str]) -> list[str]:
    if not approved_asset_ids:
        return []
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("visualization", "layout_intent", "key_message")
    ).casefold()
    if any(token in haystack for token in ("image", "photo", "图片", "照片", "主视觉")):
        page_index = max(1, _positive_int(item.get("index")))
        return [approved_asset_ids[(page_index - 1) % len(approved_asset_ids)]]
    return []


def _layout_variant(component: str, rhythm: str, composition: str) -> str:
    lowered = composition.casefold()
    if component in {"comparison", "chart-takeaway"} or any(token in lowered for token in ("split", "column", "before", "after", "对比", "分栏")):
        return "split"
    if rhythm == "breathing":
        return "editorial"
    return "balanced"


def _reading_order(component: str) -> list[str]:
    if component == "cover":
        return ["eyebrow", "title", "subtitle", "meta"]
    if component in {"chart-takeaway", "data-table", "kpi-cards"}:
        return ["conclusion title", "primary evidence", "implication", "source"]
    if component in {"comparison", "quadrant"}:
        return ["conclusion title", "comparison baseline", "contrast", "recommendation"]
    if component in {"timeline", "steps", "agenda"}:
        return ["conclusion title", "sequence", "outputs"]
    return ["conclusion title", "primary message", "supporting evidence"]


def _page_avoid_rules(component: str, rhythm: str, *, previous: dict[str, Any] | None) -> list[str]:
    rules = ["tiny body copy", "decoration without meaning"]
    if rhythm == "breathing":
        rules.append("four equal cards")
    if component == "chart-takeaway":
        rules.append("decorative chart without data")
    if previous:
        rules.append(f"repeat the prior {previous.get('layout_id')} composition unchanged")
    return rules


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return normalized or "asset"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _design_spec_markdown(spec: dict[str, Any]) -> str:
    """Render the authoritative JSON as a reviewable eleven-section design brief."""

    content = spec.get("content") if isinstance(spec.get("content"), dict) else {}
    art = spec.get("art_direction") if isinstance(spec.get("art_direction"), dict) else {}
    template = spec.get("template") if isinstance(spec.get("template"), dict) else {}
    visual = spec.get("visual_system") if isinstance(spec.get("visual_system"), dict) else {}
    theme = visual.get("theme") if isinstance(visual.get("theme"), dict) else {}
    palette = theme.get("palette") if isinstance(theme.get("palette"), dict) else {}
    fonts = theme.get("fonts") if isinstance(theme.get("fonts"), dict) else {}
    type_scale = theme.get("type_scale") if isinstance(theme.get("type_scale"), dict) else {}
    pages = [item for item in spec.get("pages") or [] if isinstance(item, dict)]
    confirmations = art.get("confirmations") if isinstance(art.get("confirmations"), dict) else {}
    quality = spec.get("quality_rules") if isinstance(spec.get("quality_rules"), dict) else {}

    lines = [
        f"# {content.get('title') or 'Presentation'} — Design Specification",
        "",
        f"> Spec hash: `{spec.get('spec_hash') or ''}`",
        "",
        "## 1. Design overview",
        "",
        f"- Audience: {_md(content.get('audience'))}",
        f"- Goal: {_md(content.get('goal'))}",
        f"- Narrative: {_md(content.get('narrative'))}",
        f"- Design rationale: {_md(art.get('rationale'))}",
        "",
        "## 2. Canvas and delivery",
        "",
        f"- Canvas: {spec.get('canvas', {}).get('width', CANVAS_WIDTH)} × {spec.get('canvas', {}).get('height', CANVAS_HEIGHT)} / 16:9",
        f"- Language: {_md(content.get('language'))}",
        f"- Page count: {len(pages)}",
        "",
        "## 3. Communication mode and visual style",
        "",
        f"- Communication mode: {_md(art.get('communication_mode'))}",
        f"- Visual style: {_md(art.get('visual_style'))}",
        f"- Throughline: {_md((art.get('design_narrative') or {}).get('visual_throughline') if isinstance(art.get('design_narrative'), dict) else '')}",
        "",
        "## 4. Color system",
        "",
        *[f"- {key}: `{value}`" for key, value in palette.items()],
        "",
        "## 5. Typography",
        "",
        f"- Display font: {_md(fonts.get('display'))}",
        f"- Body font: {_md(fonts.get('body'))}",
        f"- Type scale: {_md(json.dumps(type_scale, ensure_ascii=False))}",
        f"- Minimum body size: {visual.get('typography', {}).get('minimum_body_font_px', 16)}px",
        "",
        "## 6. Layout system and page rhythm",
        "",
        *[f"- {_md(item)}" for item in visual.get("layout_principles") or []],
        "",
        "## 7. Icons and diagrams",
        "",
        f"- {_md(json.dumps(visual.get('icon_strategy') or {}, ensure_ascii=False))}",
        "",
        "## 8. Image strategy",
        "",
        f"- {_md(visual.get('image_strategy'))}",
        "",
        "## 9. Template application",
        "",
        f"- Template: `{template.get('template_id') or ''}` / {_md(template.get('template_name'))}",
        f"- Mode: `{template.get('template_mode') or ''}`",
        f"- Applied: `{bool(template.get('applied'))}`",
        f"- Selection source: `{template.get('selection_source') or ''}`",
        "",
        "## 10. Page blueprints",
        "",
        "| Page | Role | Rhythm | Layout | Visualization | Composition |",
        "|---:|---|---|---|---|---|",
        *[
            "| {index} | {role} | {rhythm} | `{layout}` | {visualization} | {composition} |".format(
                index=item.get("index") or "",
                role=_cell(item.get("role")),
                rhythm=_cell(item.get("page_rhythm")),
                layout=_cell(item.get("layout_id")),
                visualization=_cell(json.dumps(item.get("visualization") or {}, ensure_ascii=False)),
                composition=_cell(item.get("composition")),
            )
            for item in pages
        ],
        "",
        "## 11. Execution confirmations and quality gates",
        "",
        *[f"- {key}: {_md(value)}" for key, value in confirmations.items()],
        *[f"- {key}: `{value}`" for key, value in quality.items()],
        "",
    ]
    return "\n".join(lines)


def _spec_lock_markdown(lock: dict[str, Any]) -> str:
    pages = [item for item in lock.get("page_locks") or [] if isinstance(item, dict)]
    rules = lock.get("executor_rules") if isinstance(lock.get("executor_rules"), dict) else {}
    template = lock.get("template") if isinstance(lock.get("template"), dict) else {}
    lines = [
        "# PPT Execution Lock",
        "",
        f"> Spec hash: `{lock.get('spec_hash') or ''}`",
        "",
        f"- Template: `{template.get('template_id') or ''}` / `{template.get('template_mode') or ''}`",
        f"- Canvas: `{json.dumps(lock.get('canvas') or {}, ensure_ascii=False)}`",
        f"- Immutable tokens: `{json.dumps(lock.get('immutable_visual_tokens') or {}, ensure_ascii=False)}`",
        "",
        "## Executor rules",
        "",
        *[f"- {key}: `{value}`" for key, value in rules.items()],
        "",
        "## Locked pages",
        "",
        *[
            f"- Page {item.get('index')}: layout `{item.get('layout_id') or ''}`, rhythm `{item.get('page_rhythm') or ''}`, template `{item.get('template_svg_file') or ''}`"
            for item in pages
        ],
        "",
    ]
    return "\n".join(lines)


def _md(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _cell(value: Any) -> str:
    return _md(value).replace("|", "\\|")


__all__ = [
    "ASSET_MANIFEST_SCHEMA",
    "DESIGN_SPEC_SCHEMA",
    "PAGE_BLUEPRINT_SCHEMA",
    "SPEC_LOCK_SCHEMA",
    "TEMPLATE_MANIFEST_SCHEMA",
    "build_design_contract",
    "compile_design_package",
    "freeze_design_contract",
    "page_contract",
    "render_template_svg",
]
