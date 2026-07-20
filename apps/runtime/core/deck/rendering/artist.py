"""Model-backed, one-page-at-a-time SVG art direction with a local fallback."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from core.deck.design import get_template_pack, template_svg_source, visualization_svg_source
from core.deck.llm import invoke_text_with_retries, load_node_prompt
from core.deck.quality.issues import ValidationIssue, dedupe_issues, issues_to_dicts
from core.deck.quality.svg import validate_svg_page
from core.deck.rendering.svg import SVGSlideRenderer
from core.deck.runtime_metrics import record_fallback
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, DeckSpec, SlideSpec, ThemeTokens


logger = logging.getLogger(__name__)


@dataclass
class SVGArtwork:
    """A validated canonical SVG and the provenance of how it was produced."""

    svg: str
    render_mode: str
    issues: list[ValidationIssue] = field(default_factory=list)
    rejected_issues: list[dict[str, Any]] = field(default_factory=list)
    template_id: str = ""
    layout_id: str = ""
    page_rhythm: str = ""
    visual_signature: dict[str, Any] = field(default_factory=dict)


def render_svg_artwork(
    *,
    llm: Any | None,
    state: dict[str, Any],
    deck: DeckSpec,
    slide: SlideSpec,
    outline: dict[str, Any] | None = None,
    previous_visual_signatures: list[dict[str, Any]] | None = None,
) -> SVGArtwork:
    """Author one complete SVG once, validate it, then fall back safely.

    Only an explicitly configured Designer model should be passed as ``llm``.
    The deterministic component renderer is retained solely as the last-resort
    path when no Artist is configured or the model cannot satisfy the SVG gate.
    """

    blueprint = resolve_page_blueprint(state, slide, outline or {})
    template_id = resolve_template_id(state, blueprint)
    layout_id = str(blueprint.get("layout_id") or blueprint.get("layout") or "").strip()
    rhythm = str(blueprint.get("page_rhythm") or blueprint.get("rhythm") or "dense").strip()
    signatures = list(previous_visual_signatures or [])[-2:]
    rejected: list[dict[str, Any]] = []
    request_id = str(state.get("request_id") or "")
    started_at = time.perf_counter()
    logger.info(
        "event=ppt_svg_artist_start request_id=%s slide_index=%s template_id=%s layout_id=%s has_model=%s",
        request_id,
        slide.index,
        template_id,
        layout_id,
        llm is not None,
    )

    if llm is not None:
        base_prompt = build_svg_artist_prompt(
            state=state,
            deck=deck,
            slide=slide,
            outline=outline or {},
            blueprint=blueprint,
            previous_visual_signatures=signatures,
        )
        candidate = ""
        try:
            candidate = invoke_text_with_retries(
                llm,
                system=load_node_prompt("svg_executor"),
                user=base_prompt,
                attempts=1,
            )
        except ValueError as exc:
            rejected.append(_generation_issue(slide.index, str(exc)).to_dict())
            logger.warning(
                "event=ppt_svg_generation_error request_id=%s slide_index=%s error_type=%s error=%s",
                request_id,
                slide.index,
                type(exc).__name__,
                " ".join(str(exc).split())[:500],
            )

        if candidate:
            normalized, normalization_issue = _normalize_or_issue(
                candidate,
                slide=slide,
                theme=deck.theme,
                blueprint=blueprint,
                template_id=template_id,
            )
            if normalization_issue is None:
                candidate_issues = validate_artist_svg(
                    normalized,
                    slide=slide,
                    theme=deck.theme,
                    blueprint=blueprint,
                    previous_visual_signatures=signatures,
                )
                if not candidate_issues:
                    signature = visual_signature(
                        normalized,
                        template_id=template_id,
                        layout_id=layout_id,
                        page_rhythm=rhythm,
                        component=slide.component,
                    )
                    logger.info(
                        "event=ppt_svg_artist_end request_id=%s slide_index=%s duration_ms=%s render_mode=llm_svg "
                        "rejected_issue_count=%s svg_chars=%s",
                        request_id,
                        slide.index,
                        round((time.perf_counter() - started_at) * 1000),
                        len(rejected),
                        len(normalized),
                    )
                    return SVGArtwork(
                        svg=normalized,
                        render_mode="llm_svg",
                        rejected_issues=rejected,
                        template_id=template_id,
                        layout_id=layout_id,
                        page_rhythm=rhythm,
                        visual_signature=signature,
                    )
                rejected.extend(issues_to_dicts(candidate_issues))
                logger.info(
                    "event=ppt_svg_gate_rejected request_id=%s slide_index=%s issue_count=%s issue_kinds=%s",
                    request_id,
                    slide.index,
                    len(candidate_issues),
                    ",".join(sorted({item.kind for item in candidate_issues})),
                )
            else:
                rejected.append(normalization_issue.to_dict())
                logger.info(
                    "event=ppt_svg_gate_rejected request_id=%s slide_index=%s issue_count=1 issue_kinds=%s",
                    request_id,
                    slide.index,
                    normalization_issue.kind,
                )

    fallback = _fallback_artwork(
        deck=deck,
        slide=slide,
        blueprint=blueprint,
        template_id=template_id,
        rejected=rejected,
        reason="Designer model was not explicitly configured." if llm is None else "Designer SVG did not pass the deterministic safety gate.",
    )
    logger.warning(
        "event=ppt_svg_artist_end request_id=%s slide_index=%s duration_ms=%s render_mode=%s "
        "rejected_issue_count=%s svg_chars=%s",
        request_id,
        slide.index,
        round((time.perf_counter() - started_at) * 1000),
        fallback.render_mode,
        len(rejected),
        len(fallback.svg),
    )
    return fallback


def build_svg_artist_prompt(
    *,
    state: dict[str, Any],
    deck: DeckSpec,
    slide: SlideSpec,
    outline: dict[str, Any],
    blueprint: dict[str, Any],
    previous_visual_signatures: list[dict[str, Any]],
) -> str:
    """Build the page prompt, including the actual template SVG source."""

    template_id = resolve_template_id(state, blueprint)
    pack = get_template_pack(template_id)
    layout_id = str(blueprint.get("layout_id") or blueprint.get("layout") or "").strip()
    structure = _structure_only_svg(
        _blueprint_template_svg(blueprint) or template_svg_source(pack.template_id, layout_id)
    )
    visualization = blueprint.get("visualization")
    visualization_id = str(visualization.get("type") or "") if isinstance(visualization, dict) else ""
    visualization_intent = (
        json.dumps(visualization, ensure_ascii=False)
        if isinstance(visualization, dict)
        else str(visualization or outline.get("visualization") or "")
    )
    inherently_visual = slide.component in {
        "chart-takeaway", "data-table", "kpi-cards", "comparison", "quadrant", "timeline", "steps", "agenda"
    }
    explicit_none = visualization_id == "none" or (
        not isinstance(visualization, dict) and visualization_intent.strip().casefold() in {"", "none"}
    )
    visualization_requested = inherently_visual or (not explicit_none and bool(visualization_intent.strip()))
    visualization_reference = (
        visualization_svg_source(
            component=slide.component,
            intent=visualization_intent,
            visualization_id=visualization_id,
            template_id=template_id,
            theme=deck.theme,
        )
        if visualization_requested
        else ""
    )
    execution_lock = _execution_lock(state, deck.theme)
    design_spec = state.get("design_spec") or state.get("design_contract") or {}
    art_direction = design_spec.get("art_direction") if isinstance(design_spec, dict) else {}
    page_lock = _page_execution_lock(execution_lock, slide.index)
    asset_manifest = state.get("asset_manifest") or []
    context = {
        "task": "Author exactly one complete, editable, PowerPoint-safe SVG page.",
        "page": slide.index,
        "deck": {
            "title": deck.title,
            "narrative": deck.narrative,
            "audience": deck.audience,
            "goal": deck.goal,
            "language": deck.language,
        },
        "execution_lock": page_lock,
        "design_summary": {
            "communication_mode": art_direction.get("communication_mode") if isinstance(art_direction, dict) else "",
            "visual_style": art_direction.get("visual_style") if isinstance(art_direction, dict) else "",
            "design_narrative": art_direction.get("design_narrative") if isinstance(art_direction, dict) else {},
        },
        "selected_template": {
            "template_id": pack.template_id,
            "template_mode": pack.template_mode,
            "summary": pack.summary,
            "layout_principles": pack.layout_principles,
        },
        "page_blueprint": blueprint,
        "semantic_slide_spec": slide.to_dict(),
        "outline_context": outline,
        "approved_assets": _approved_assets(asset_manifest, allowed_ids=blueprint.get("asset_ids")),
        "previous_two_visual_signatures": list(previous_visual_signatures)[-2:],
        "already_used_compositions": [
            {
                "page": item.get("page"),
                "layout_id": item.get("layout_id"),
                "page_rhythm": item.get("page_rhythm"),
                "group_ids": item.get("group_ids"),
                "geometry_fingerprint": item.get("geometry_fingerprint"),
            }
            for item in list(previous_visual_signatures)[-2:]
            if isinstance(item, dict)
        ],
        "runtime_constraints": {
            "canvas": [CANVAS_WIDTH, CANVAS_HEIGHT],
            "template_svg_is_structure_not_copy": True,
            "avoid_reusing_previous_composition": True,
            "images_are_allowed_only_when_listed_in_approved_assets": True,
            "visualization_reference_is_grammar_not_data": True,
        },
    }
    parts = [
            "PAGE EXECUTION CONTEXT (JSON):",
            json.dumps(context, ensure_ascii=False, indent=2),
            "",
            "ACTUAL TEMPLATE SVG STRUCTURE SOURCE:",
            structure,
            "",
            "Inherit the named zones and structural logic above, then replace guide geometry with a finished page. Do not return the guide itself.",
    ]
    if visualization_reference:
        parts.extend(
            [
                "",
                "VISUALIZATION GRAMMAR SVG REFERENCE:",
                visualization_reference,
                "",
                "Use this only as a visual grammar. Replace every sample label and number with the semantic SlideSpec; never copy reference data.",
            ]
        )
    return "\n".join(parts)


def resolve_page_blueprint(state: dict[str, Any], slide: SlideSpec, outline: dict[str, Any]) -> dict[str, Any]:
    """Resolve the per-page blueprint across the current design package shapes."""

    candidates: list[Any] = [
        state.get("page_blueprints"),
        (state.get("design_spec") or {}).get("pages") if isinstance(state.get("design_spec"), dict) else None,
        (state.get("execution_lock") or {}).get("pages") if isinstance(state.get("execution_lock"), dict) else None,
        (state.get("design_contract") or {}).get("pages") if isinstance(state.get("design_contract"), dict) else None,
    ]
    resolved: dict[str, Any] = {}
    for collection in candidates:
        page = _page_from_collection(collection, slide.index)
        if page:
            resolved.update(page)
            break
    for key in ("layout_id", "layout", "page_rhythm", "rhythm", "composition", "reading_order", "visualization", "emphasis", "template_svg", "template_svg_source"):
        if key not in resolved and outline.get(key) not in (None, ""):
            resolved[key] = outline.get(key)
    resolved.setdefault("index", slide.index)
    resolved.setdefault("title", slide.title)
    resolved.setdefault("component", slide.component)
    resolved.setdefault("page_rhythm", "breathing" if slide.component in {"cover", "section", "quote", "end"} else "dense")
    resolved.setdefault("composition", "Use the selected template zones to express the page's key message with one dominant focal point.")
    resolved.setdefault("reading_order", ["title", "primary evidence", "implication", "footer"])
    template_id = resolve_template_id(state, resolved)
    resolved.setdefault("template_id", template_id)
    if not str(resolved.get("layout_id") or resolved.get("layout") or "").strip():
        pack = get_template_pack(template_id)
        rhythm = str(resolved.get("page_rhythm") or "dense")
        candidates = [item for item in pack.layouts if slide.component in item.get("components", []) and rhythm in item.get("rhythms", [])]
        candidates = candidates or [item for item in pack.layouts if slide.component in item.get("components", [])]
        candidates = candidates or [item for item in pack.layouts if rhythm in item.get("rhythms", [])]
        candidates = candidates or list(pack.layouts)
        if candidates:
            selected = candidates[(slide.index - 1) % len(candidates)]
            resolved["layout_id"] = str(selected.get("layout_id") or "")
            resolved["composition"] = str(selected.get("composition") or resolved["composition"])
            if isinstance(selected.get("geometry"), dict):
                resolved.setdefault("geometry", dict(selected["geometry"]))
    return resolved


def resolve_template_id(state: dict[str, Any], blueprint: dict[str, Any]) -> str:
    """Read the chosen template id without depending on one state schema."""

    direct_values = [
        blueprint.get("template_id"),
        state.get("template_id"),
        (state.get("art_direction") or {}).get("template_id") if isinstance(state.get("art_direction"), dict) else None,
    ]
    for container_key in ("design_spec", "execution_lock", "design_contract", "template_manifest"):
        container = state.get(container_key)
        if not isinstance(container, dict):
            continue
        direct_values.append(container.get("template_id"))
        template = container.get("template")
        if isinstance(template, dict):
            direct_values.extend([template.get("template_id"), template.get("id")])
    selected = next((str(value).strip() for value in direct_values if str(value or "").strip()), "editorial_freeform")
    return get_template_pack(selected).template_id


def validate_artist_svg(
    svg: str,
    *,
    slide: SlideSpec,
    theme: ThemeTokens,
    blueprint: dict[str, Any] | None = None,
    previous_visual_signatures: list[dict[str, Any]] | None = None,
) -> list[ValidationIssue]:
    """Apply the native SVG gate plus content and art-direction invariants."""

    issues = validate_svg_page(svg, page=slide.index, theme=theme, expected_spec_hash=slide.spec_hash)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return issues
    groups = [child for child in root if _local_name(child.tag) == "g"]
    if not 3 <= len(groups) <= 8:
        issues.append(
            ValidationIssue(
                page=slide.index,
                severity="must_fix",
                kind="svg_semantic_grouping",
                description=f"The page has {len(groups)} top-level semantic groups; expected 3-8.",
                suggestion="Separate background/header/footer and content into a small set of named groups.",
            )
        )
    issues.extend(_content_preservation_issues(root, slide))
    issues.extend(_blueprint_geometry_issues(root, slide.index, blueprint or {}))
    signature = visual_signature(svg, component=slide.component)
    previous = [item for item in list(previous_visual_signatures or [])[-2:] if isinstance(item, dict)]
    if previous and signature.get("geometry_fingerprint") and signature.get("geometry_fingerprint") == previous[-1].get("geometry_fingerprint"):
        issues.append(
            ValidationIssue(
                page=slide.index,
                severity="must_fix",
                kind="svg_repeated_composition",
                description="The page repeats the immediately preceding geometry almost exactly.",
                suggestion="Keep the design system but change the focal geometry and reading path for this page.",
            )
        )
    return dedupe_issues(issues)


def visual_signature(
    svg: str,
    *,
    template_id: str = "",
    layout_id: str = "",
    page_rhythm: str = "",
    component: str = "",
) -> dict[str, Any]:
    """Create compact page context for the next two SVG Artist calls."""

    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return {}
    counts: dict[str, int] = {}
    geometry: list[str] = []
    for element in root.iter():
        tag = _local_name(element.tag)
        counts[tag] = counts.get(tag, 0) + 1
        if tag in {"rect", "circle", "ellipse", "line", "polygon", "polyline", "text"} and len(geometry) < 24:
            geometry.append(_geometry_token(element, tag))
    return {
        "page": _int_value(root.get("data-page")),
        "template_id": template_id or str(root.get("data-template-id") or ""),
        "layout_id": layout_id or str(root.get("data-layout-id") or ""),
        "page_rhythm": page_rhythm or str(root.get("data-page-rhythm") or ""),
        "component": component or str(root.get("data-component") or ""),
        "group_ids": [str(child.get("id") or "") for child in root if _local_name(child.tag) == "g"],
        "element_counts": counts,
        "geometry_fingerprint": "|".join(geometry),
    }


def _fallback_artwork(
    *,
    deck: DeckSpec,
    slide: SlideSpec,
    blueprint: dict[str, Any],
    template_id: str,
    rejected: list[dict[str, Any]],
    reason: str,
) -> SVGArtwork:
    record_fallback()
    layout_id = str(blueprint.get("layout_id") or blueprint.get("layout") or "").strip()
    rhythm = str(blueprint.get("page_rhythm") or blueprint.get("rhythm") or "dense").strip()
    fallback = SVGSlideRenderer().render(deck, slide)
    normalized, issue = _normalize_or_issue(fallback, slide=slide, theme=deck.theme, blueprint=blueprint, template_id=template_id)
    fallback_issues = validate_svg_page(normalized, page=slide.index, theme=deck.theme, expected_spec_hash=slide.spec_hash) if issue is None else [issue]
    fallback_issues.append(
        ValidationIssue(
            page=slide.index,
            severity="must_fix",
            kind="svg_artist_fallback",
            description=reason,
            suggestion="Configure a capable Designer model or inspect the rejected SVG quality issues.",
        )
    )
    return SVGArtwork(
        svg=normalized,
        render_mode="deterministic_fallback",
        issues=dedupe_issues(fallback_issues),
        rejected_issues=rejected,
        template_id=template_id,
        layout_id=layout_id,
        page_rhythm=rhythm,
        visual_signature=visual_signature(
            normalized,
            template_id=template_id,
            layout_id=layout_id,
            page_rhythm=rhythm,
            component=slide.component,
        ),
    )


def _normalize_or_issue(
    raw: str,
    *,
    slide: SlideSpec,
    theme: ThemeTokens,
    blueprint: dict[str, Any],
    template_id: str,
) -> tuple[str, ValidationIssue | None]:
    try:
        return normalize_svg_document(raw, slide=slide, theme=theme, blueprint=blueprint, template_id=template_id), None
    except (ET.ParseError, ValueError) as exc:
        return "", ValidationIssue(
            page=slide.index,
            severity="must_fix",
            kind="svg_invalid_xml",
            description=f"SVG Artist output could not be normalized: {exc}.",
            suggestion="Return exactly one complete SVG document.",
        )


def normalize_svg_document(
    raw: str,
    *,
    slide: SlideSpec,
    theme: ThemeTokens,
    blueprint: dict[str, Any],
    template_id: str,
) -> str:
    """Canonicalize root metadata and editable text slots without redesigning."""

    source = _extract_svg(raw)
    root = ET.fromstring(source)
    if _local_name(root.tag) != "svg":
        raise ValueError("root element is not svg")
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    if not str(root.tag).startswith("{"):
        root.tag = f"{{{namespace}}}svg"
    root.set("width", str(CANVAS_WIDTH))
    root.set("height", str(CANVAS_HEIGHT))
    root.set("viewBox", f"0 0 {CANVAS_WIDTH} {CANVAS_HEIGHT}")
    root.set("data-page", str(slide.index))
    root.set("data-component", slide.component)
    root.set("data-layout-variant", slide.layout_variant)
    root.set("data-spec-hash", slide.spec_hash)
    root.set("data-template-id", template_id)
    root.set("data-layout-id", str(blueprint.get("layout_id") or blueprint.get("layout") or ""))
    root.set("data-page-rhythm", str(blueprint.get("page_rhythm") or blueprint.get("rhythm") or "dense"))
    # Reference SVGs may use guide-only attributes that are not round-trippable.
    # A generated page must never masquerade as a reference to bypass final-page checks.
    root.attrib.pop("data-reference-kind", None)

    used_ids: set[str] = set()
    counters: dict[str, int] = {}
    for element in root.iter():
        if element is root:
            continue
        tag = _local_name(element.tag)
        proposed = _slug(str(element.get("id") or ""))
        if not proposed or proposed == "zone":
            counters[tag] = counters.get(tag, 0) + 1
            proposed = f"{tag}-{counters[tag]}"
        unique = proposed
        suffix = 2
        while unique in used_ids:
            unique = f"{proposed}-{suffix}"
            suffix += 1
        element.set("id", unique)
        used_ids.add(unique)
        if tag == "text":
            _normalize_text_element(element, theme)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _normalize_text_element(element: ET.Element, theme: ThemeTokens) -> None:
    lines = ["".join(child.itertext()).strip() for child in element if _local_name(child.tag) == "tspan"]
    visible = "\n".join(value for value in lines if value) or "".join(element.itertext()).strip()
    element.set("data-text", "true")
    element.set("data-editable", "true")
    element.set("data-value", str(element.get("data-value") or visible))
    size = _positive_float(element.get("data-font-size") or element.get("font-size"), float(theme.type_scale.get("body") or 18))
    display = str(theme.fonts.get("display") or "Noto Sans SC").split(",", 1)[0].strip(" '\"")
    body = str(theme.fonts.get("body") or "Noto Sans SC").split(",", 1)[0].strip(" '\"")
    current_family = str(element.get("data-font-family") or element.get("font-family") or "").split(",", 1)[0].strip(" '\"")
    family = current_family if current_family.casefold() in {display.casefold(), body.casefold()} else (display if size >= float(theme.type_scale.get("h2") or 30) else body)
    element.set("font-size", f"{size:g}")
    element.set("data-font-size", f"{size:g}")
    element.set("font-family", family)
    element.set("data-font-family", family)
    element.set("fill", str(element.get("fill") or theme.palette.get("ink") or "#111827"))
    anchor = str(element.get("text-anchor") or "start").strip().lower()
    align = str(element.get("data-align") or {"middle": "center", "end": "right"}.get(anchor, "left"))
    element.set("data-align", align)
    element.set("data-valign", str(element.get("data-valign") or "top"))
    if not str(element.get("data-box") or "").strip():
        x, y = _text_origin(element)
        element.set("data-box", _derived_text_box(visible, x=x, y=y, size=size, align=align))


def _content_preservation_issues(root: ET.Element, slide: SlideSpec) -> list[ValidationIssue]:
    visible = " ".join("".join(element.itertext()) for element in root.iter() if _local_name(element.tag) == "text")
    canonical_visible = _canonical_text(visible)
    required = [slide.title]

    def walk(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            required.append(value.strip())
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            required.append(str(value))
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(slide.content)
    if slide.chart:
        for item in slide.chart.get("series") or []:
            if isinstance(item, dict) and str(item.get("label") or "").strip():
                required.append(str(item.get("label")).strip())
    missing = []
    for value in required:
        canonical = _canonical_text(value)
        if canonical and canonical not in canonical_visible and value not in missing:
            missing.append(value)
    issues: list[ValidationIssue] = []
    if missing:
        issues.append(
            ValidationIssue(
                page=slide.index,
                severity="must_fix",
                kind="svg_content_omission",
                description="SVG omits SlideSpec text: " + "; ".join(missing[:5]),
                suggestion="Restore every title, label, and content fact from the semantic SlideSpec.",
            )
        )

    source_text = json.dumps({"title": slide.title, "content": slide.content, "chart": slide.chart}, ensure_ascii=False)
    allowed_numbers = set(re.findall(r"\d[\d,.]*(?:%|x)?", source_text, flags=re.IGNORECASE))
    allowed_numbers.update({str(slide.index), f"{slide.index:02d}"})
    visible_numbers = set(re.findall(r"\d[\d,.]*(?:%|x)?", visible, flags=re.IGNORECASE))
    invented = sorted(visible_numbers - allowed_numbers)
    if invented:
        issues.append(
            ValidationIssue(
                page=slide.index,
                severity="must_fix",
                kind="svg_fabricated_number",
                description="SVG introduces numeric text not present in SlideSpec: " + ", ".join(invented[:5]),
                suggestion="Remove invented numbers and use only source-backed values from SlideSpec.",
            )
        )
    return issues


def _blueprint_geometry_issues(root: ET.Element, page: int, blueprint: dict[str, Any]) -> list[ValidationIssue]:
    """Require the authored page to use most of the locked layout zones."""

    geometry = blueprint.get("geometry") if isinstance(blueprint.get("geometry"), dict) else {}
    zones = {
        str(name): box
        for name, raw in geometry.items()
        if (box := _numeric_box(raw)) is not None
    }
    if not zones:
        return []
    authored = [
        box
        for element in root.iter()
        if _local_name(element.tag) not in {"svg", "g", "tspan"}
        and (box := _element_box(element)) is not None
        and not (box[2] >= CANVAS_WIDTH * 0.9 and box[3] >= CANVAS_HEIGHT * 0.9)
    ]
    covered = [name for name, zone in zones.items() if any(_boxes_touch(zone, box) for box in authored)]
    required = max(1, (len(zones) + 1) // 2)
    if len(covered) >= required:
        return []
    missing = [name for name in zones if name not in covered]
    return [
        ValidationIssue(
            page=page,
            severity="must_fix",
            kind="svg_blueprint_geometry",
            description=f"The page uses only {len(covered)}/{len(zones)} locked layout zones; missing: {', '.join(missing[:4])}.",
            suggestion="Recompose the page around the named geometry in the active page blueprint.",
        )
    ]


def _execution_lock(state: dict[str, Any], theme: ThemeTokens) -> dict[str, Any]:
    project_dir = str(state.get("project_dir") or "").strip()
    if project_dir:
        path = Path(project_dir) / "spec-lock.json"
        try:
            persisted = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            persisted = None
        if isinstance(persisted, dict):
            return persisted
    source = state.get("execution_lock") or state.get("design_contract") or {}
    if isinstance(source, dict) and source:
        return source
    return {
        "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
        "theme": theme.to_dict(),
        "svg_profile": "native editable shapes",
        "min_body_font_px": max(16, int(theme.type_scale.get("body") or 18)),
    }


def _page_execution_lock(lock: dict[str, Any], page: int) -> dict[str, Any]:
    """Keep immutable deck tokens plus exactly one page lock in the page prompt."""

    result = {key: value for key, value in lock.items() if key not in {"page_locks", "pages"}}
    page_lock = _page_from_collection(lock.get("page_locks") or lock.get("pages"), page)
    result["page_lock"] = page_lock or {"index": page}
    return result


def _approved_assets(value: Any, *, allowed_ids: Any = None) -> list[dict[str, Any]]:
    collection = value.get("assets") if isinstance(value, dict) else value
    raw_ids = [allowed_ids] if isinstance(allowed_ids, str) else allowed_ids or []
    approved_ids = {str(item) for item in raw_ids if str(item)}
    return [
        dict(item)
        for item in collection or []
        if isinstance(item, dict)
        and (not approved_ids or str(item.get("asset_id") or "") in approved_ids)
        and str(item.get("href") or item.get("path") or "").startswith("../images/")
    ] if approved_ids else []


def _blueprint_template_svg(blueprint: dict[str, Any]) -> str:
    for key in ("template_svg_source", "template_svg", "svg_structure"):
        value = str(blueprint.get(key) or "").strip()
        if "<svg" in value and "</svg>" in value:
            return _extract_svg(value)
    return ""


def _structure_only_svg(svg: str) -> str:
    """Remove guide copy so the model sees geometry without placeholder text."""

    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    for parent in root.iter():
        for child in list(parent):
            if _local_name(child.tag) == "text":
                parent.remove(child)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _numeric_box(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, width, height = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    return (x, y, width, height) if width >= 0 and height >= 0 else None


def _element_box(element: ET.Element) -> tuple[float, float, float, float] | None:
    tag = _local_name(element.tag)
    if tag in {"rect", "image"}:
        return _numeric_box([element.get("x"), element.get("y"), element.get("width"), element.get("height")])
    if tag == "text":
        return _numeric_box(str(element.get("data-box") or "").split(","))
    try:
        if tag == "circle":
            cx, cy, radius = (float(element.get(name) or 0) for name in ("cx", "cy", "r"))
            return cx - radius, cy - radius, radius * 2, radius * 2
        if tag == "ellipse":
            cx, cy, rx, ry = (float(element.get(name) or 0) for name in ("cx", "cy", "rx", "ry"))
            return cx - rx, cy - ry, rx * 2, ry * 2
        if tag == "line":
            x1, y1, x2, y2 = (float(element.get(name) or 0) for name in ("x1", "y1", "x2", "y2"))
            return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
        if tag in {"polygon", "polyline"}:
            numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", str(element.get("points") or ""))]
            xs, ys = numbers[0::2], numbers[1::2]
            if xs and ys:
                return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    except (TypeError, ValueError):
        return None
    return None


def _boxes_touch(zone: tuple[float, float, float, float], box: tuple[float, float, float, float]) -> bool:
    zx, zy, zw, zh = zone
    bx, by, bw, bh = box
    pad = 18.0
    return not (
        bx + bw < zx - pad
        or zx + zw < bx - pad
        or by + bh < zy - pad
        or zy + zh < by - pad
    )


def _page_from_collection(collection: Any, page_index: int) -> dict[str, Any]:
    if isinstance(collection, dict):
        direct = collection.get(str(page_index)) or collection.get(page_index)
        if isinstance(direct, dict):
            return dict(direct)
        collection = collection.get("pages")
    if isinstance(collection, list):
        for offset, item in enumerate(collection, start=1):
            if isinstance(item, dict) and int(item.get("index") or item.get("page") or offset) == page_index:
                return dict(item)
    return {}


def _extract_svg(raw: str) -> str:
    cleaned = re.sub(r"^```(?:xml|svg)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.IGNORECASE)
    start = cleaned.lower().find("<svg")
    end = cleaned.lower().rfind("</svg>")
    if start < 0 or end < start:
        raise ValueError("response does not contain one complete SVG document")
    return cleaned[start : end + len("</svg>")]


def _text_origin(element: ET.Element) -> tuple[float, float]:
    first_tspan = next((child for child in element if _local_name(child.tag) == "tspan"), None)
    x = _positive_float(element.get("x") or (first_tspan.get("x") if first_tspan is not None else None), 56.0, allow_zero=True)
    y = _positive_float(element.get("y") or (first_tspan.get("y") if first_tspan is not None else None), 56.0, allow_zero=True)
    return x, y


def _derived_text_box(value: str, *, x: float, y: float, size: float, align: str) -> str:
    lines = value.splitlines() or [value]
    max_units = max((_text_units(line) for line in lines), default=1.0)
    width = min(1168.0, max(size * 2, max_units * size * 0.62))
    height = min(636.0, max(size * 1.35, len(lines) * size * 1.35))
    box_x = x - width if align == "right" else x - width / 2 if align == "center" else x
    box_y = y - size
    box_x = min(max(0.0, box_x), CANVAS_WIDTH - width)
    box_y = min(max(0.0, box_y), CANVAS_HEIGHT - height)
    return f"{box_x:g},{box_y:g},{width:g},{height:g}"


def _text_units(value: str) -> float:
    return sum(1.0 if "\u3400" <= char <= "\u9fff" else 0.62 for char in value)


def _geometry_token(element: ET.Element, tag: str) -> str:
    names = {
        "rect": ("x", "y", "width", "height"),
        "circle": ("cx", "cy", "r"),
        "ellipse": ("cx", "cy", "rx", "ry"),
        "line": ("x1", "y1", "x2", "y2"),
        "text": ("x", "y", "font-size"),
    }.get(tag, ("points",))
    values: list[str] = []
    for name in names:
        raw = str(element.get(name) or "")
        if name == "points":
            numbers = re.findall(r"-?\d+(?:\.\d+)?", raw)[:8]
            values.extend(str(round(float(value) / 24) * 24) for value in numbers)
        else:
            try:
                values.append(str(round(float(raw) / 24) * 24))
            except ValueError:
                values.append("")
    return f"{tag}:{','.join(values)}"


def _generation_issue(page: int, description: str) -> ValidationIssue:
    return ValidationIssue(
        page=page,
        severity="must_fix",
        kind="svg_artist_generation",
        description=description,
        suggestion="Use the deterministic local SVG fallback for this page.",
    )


def _canonical_text(value: Any) -> str:
    return re.sub(r"[^\w]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _positive_float(value: Any, fallback: float, *, allow_zero: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if parsed > 0 or allow_zero and parsed >= 0:
        return parsed
    return fallback


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-_")
    return normalized or "zone"


__all__ = [
    "SVGArtwork",
    "build_svg_artist_prompt",
    "normalize_svg_document",
    "render_svg_artwork",
    "resolve_page_blueprint",
    "resolve_template_id",
    "validate_artist_svg",
    "visual_signature",
]
