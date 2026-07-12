"""Static quality checks for the restricted SVG deck intermediate format."""

from __future__ import annotations

import math
import re
from typing import Any
from xml.etree import ElementTree as ET

from core.deck.assets import is_safe_image_href
from core.deck.quality.issues import ValidationIssue
from core.deck.rendering.svg import SVG_ALLOWED_TAGS
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, ThemeTokens


FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "clip-path",
        "filter",
        "mask",
        "style",
        "transform",
        "{http://www.w3.org/1999/xlink}href",
    }
)
FINAL_ONLY_FORBIDDEN_ATTRIBUTES = frozenset(
    {"opacity", "fill-opacity", "stroke-opacity", "stroke-dasharray", "letter-spacing"}
)
PAINT_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def validate_svg_page(svg: str, *, page: int, theme: ThemeTokens, expected_spec_hash: str = "") -> list[ValidationIssue]:
    """Validate syntax, canvas, safe element subset, typography, and text boxes."""

    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return [_issue(page, "invalid_xml", f"SVG XML is invalid: {exc}.", "Regenerate the page SVG.")]

    issues: list[ValidationIssue] = []
    if _local_name(root.tag) != "svg":
        issues.append(_issue(page, "missing_root", "SVG root element is missing.", "Use one <svg> root element."))
        return issues

    issues.extend(_validate_canvas(root, page))
    if expected_spec_hash and root.get("data-spec-hash") != expected_spec_hash:
        issues.append(_issue(page, "spec_drift", "SVG was not generated from the active design-contract revision.", "Regenerate the page from the frozen spec hash."))
    allowed_fonts = {
        _font_name(theme.fonts.get("display")),
        _font_name(theme.fonts.get("body")),
    }
    is_reference = bool(str(root.get("data-reference-kind") or "").strip())
    ids: set[str] = set()
    editable_text = 0
    shape_count = 0
    for element in root.iter():
        tag = _local_name(element.tag)
        if tag not in SVG_ALLOWED_TAGS:
            issues.append(_issue(page, "unsupported_element", f"Unsupported SVG element <{tag}>.", "Use only the restricted native-shape subset."))
        elif tag not in {"svg", "g", "tspan"}:
            shape_count += 1
        element_id = str(element.get("id") or "").strip()
        if element_id:
            if element_id in ids:
                issues.append(_issue(page, "duplicate_id", f"SVG id '{element_id}' is duplicated.", "Use stable, unique semantic ids."))
            ids.add(element_id)
        for raw_name in element.attrib:
            attr_name = _local_name(raw_name).lower()
            if attr_name == "href":
                if tag != "image" or raw_name != "href":
                    issues.append(_issue(page, "forbidden_attribute", f"<{tag}> uses forbidden attribute '{attr_name}'.", "Only plain href on an approved image element is allowed."))
                continue
            if (
                raw_name in FORBIDDEN_ATTRIBUTES
                or attr_name.startswith("on")
                or (not is_reference and attr_name in FINAL_ONLY_FORBIDDEN_ATTRIBUTES)
            ):
                issues.append(_issue(page, "forbidden_attribute", f"<{tag}> uses forbidden attribute '{_local_name(raw_name)}'.", "Remove external, scripted, and transform-based SVG features."))
        issues.extend(_validate_paint(element, page, tag))
        if tag == "text":
            if element.get("data-text") == "true":
                editable_text += 1
            issues.extend(_validate_text(element, page, allowed_fonts))
        elif tag == "image":
            issues.extend(_validate_image(element, page))

    if editable_text == 0:
        issues.append(_issue(page, "missing_text_slots", "SVG contains no editable text slots.", "Mark user-visible text with data-text=true."))
    if shape_count < 4:
        issues.append(_issue(page, "too_sparse", "SVG contains too few visual elements for a complete slide.", "Render the background, hierarchy, content, and footer shapes."))
    if shape_count > 220:
        issues.append(_issue(page, "too_complex", f"SVG contains {shape_count} visual elements.", "Simplify the page before native PPTX conversion."))
    return _dedupe(issues)


def _validate_canvas(root: ET.Element, page: int) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    viewbox = re.split(r"[\s,]+", str(root.get("viewBox") or "").strip())
    expected = [0.0, 0.0, float(CANVAS_WIDTH), float(CANVAS_HEIGHT)]
    try:
        parsed = [float(item) for item in viewbox]
    except ValueError:
        parsed = []
    if len(parsed) != 4 or any(abs(a - b) > 0.01 for a, b in zip(parsed, expected, strict=False)):
        issues.append(_issue(page, "viewbox", f"SVG viewBox must be 0 0 {CANVAS_WIDTH} {CANVAS_HEIGHT}.", "Regenerate using the locked 16:9 canvas."))
    for name, expected_value in (("width", CANVAS_WIDTH), ("height", CANVAS_HEIGHT)):
        try:
            value = float(str(root.get(name) or ""))
        except ValueError:
            value = 0
        if abs(value - expected_value) > 0.01:
            issues.append(_issue(page, "canvas_size", f"SVG {name} must be {expected_value}px.", "Use the locked canvas dimensions."))
    return issues


def _validate_paint(element: ET.Element, page: int, tag: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for name in ("fill", "stroke"):
        value = str(element.get(name) or "").strip()
        if not value or value == "none":
            continue
        if not PAINT_RE.fullmatch(value):
            issues.append(_issue(page, "paint_syntax", f"<{tag}> {name} must be a six-digit hex color, got '{value}'.", "Resolve colors before writing SVG."))
    return issues


def _validate_text(element: ET.Element, page: int, allowed_fonts: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    family = _font_name(element.get("data-font-family") or element.get("font-family"))
    if family and family not in allowed_fonts:
        issues.append(_issue(page, "font_drift", f"Text uses unlocked font '{family}'.", "Use the selected display or body font."))
    try:
        font_size = float(element.get("data-font-size") or element.get("font-size") or 0)
    except ValueError:
        font_size = 0
    if element.get("data-text") == "true" and font_size < 12:
        issues.append(_issue(page, "font_size", f"Editable text is only {font_size:g}px.", "Increase font size or split the content."))
    raw_box = str(element.get("data-box") or "")
    if element.get("data-text") == "true" and not str(element.get("data-value") or "").strip():
        issues.append(_issue(page, "empty_text", "Editable SVG text has no source value.", "Remove the empty slot or provide visible text."))
    if not raw_box:
        return issues
    try:
        x, y, width, height = [float(item) for item in raw_box.split(",")]
    except (TypeError, ValueError):
        issues.append(_issue(page, "text_box", f"Invalid text data-box '{raw_box}'.", "Write x,y,width,height numeric geometry."))
        return issues
    if not all(math.isfinite(value) for value in (x, y, width, height)) or width <= 0 or height <= 0:
        issues.append(_issue(page, "text_box", f"Invalid text box geometry '{raw_box}'.", "Use finite positive text-box dimensions."))
    elif x < -0.01 or y < -0.01 or x + width > CANVAS_WIDTH + 0.01 or y + height > CANVAS_HEIGHT + 0.01:
        issues.append(_issue(page, "text_out_of_bounds", f"Text box '{raw_box}' extends outside the canvas.", "Reflow the text inside the slide safe area."))
    return issues


def _validate_image(element: ET.Element, page: int) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    href = str(element.get("href") or "").strip()
    if not is_safe_image_href(href):
        issues.append(
            _issue(
                page,
                "image_href",
                f"Image href '{href}' is outside the approved request image namespace.",
                "Use ../images/<opaque-name>.png|jpg|jpeg|webp from the approved asset manifest.",
            )
        )
    for name in ("x", "y", "width", "height"):
        raw = element.get(name)
        try:
            value = float(str(raw or ""))
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or (name in {"width", "height"} and value <= 0):
            issues.append(_issue(page, "image_box", f"Image has invalid {name}={raw!r}.", "Use finite x/y and positive width/height values."))
    preserve = str(element.get("preserveAspectRatio") or "xMidYMid meet").strip()
    if preserve not in {"xMidYMid meet", "xMidYMid slice"}:
        issues.append(_issue(page, "image_fit", f"Unsupported preserveAspectRatio '{preserve}'.", "Use xMidYMid meet or xMidYMid slice."))
    return issues


def _font_name(value: Any) -> str:
    return str(value or "").split(",", 1)[0].strip().strip("'\"").casefold()


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _issue(page: int, kind: str, description: str, suggestion: str) -> ValidationIssue:
    return ValidationIssue(page=page, severity="must_fix", kind=f"svg_{kind}", description=description, suggestion=suggestion)


def _dedupe(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[int, str, str]] = set()
    result: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.page, issue.kind, issue.description)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


__all__ = ["validate_svg_page"]
