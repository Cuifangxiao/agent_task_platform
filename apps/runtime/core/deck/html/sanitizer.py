"""Parser-backed allowlist sanitizer for untrusted model HTML and CSS."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import bleach
import html5lib
import tinycss2


MAX_HTML_CHARS = 40_000
MAX_CSS_CHARS = 40_000
MAX_DOM_NODES = 500
MAX_SVG_PATHS = 80
MAX_CSS_RULES = 100
MAX_CSS_DECLARATIONS = 360

ALLOWED_TAGS = frozenset(
    {
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "div",
        "span",
        "h1",
        "h2",
        "h3",
        "p",
        "ul",
        "ol",
        "li",
        "strong",
        "em",
        "small",
        "blockquote",
        "dl",
        "dt",
        "dd",
        "br",
        "svg",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
    }
)
SVG_TAGS = frozenset({"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon"})
GLOBAL_ATTRIBUTES = frozenset(
    {
        "class",
        "role",
        "aria-hidden",
        "aria-label",
        "data-box",
        "data-layout",
        "data-decorative",
        "data-allow-overlap",
        "data-title-lines",
        "data-icon",
        "data-slot",
    }
)
SVG_ATTRIBUTES = frozenset(
    {
        "viewbox",
        "x",
        "y",
        "x1",
        "x2",
        "y1",
        "y2",
        "width",
        "height",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "opacity",
        "transform",
        "preserveaspectratio",
    }
)

ALLOWED_CSS_PROPERTIES = frozenset(
    {
        "align-content",
        "align-items",
        "align-self",
        "aspect-ratio",
        "background",
        "background-color",
        "background-image",
        "background-position",
        "background-repeat",
        "background-size",
        "border",
        "border-bottom",
        "border-bottom-color",
        "border-bottom-left-radius",
        "border-bottom-right-radius",
        "border-bottom-style",
        "border-bottom-width",
        "border-color",
        "border-left",
        "border-left-color",
        "border-left-style",
        "border-left-width",
        "border-radius",
        "border-right",
        "border-right-color",
        "border-right-style",
        "border-right-width",
        "border-style",
        "border-top",
        "border-top-color",
        "border-top-left-radius",
        "border-top-right-radius",
        "border-top-style",
        "border-top-width",
        "border-width",
        "bottom",
        "box-shadow",
        "color",
        "column-gap",
        "content",
        "display",
        "flex",
        "flex-basis",
        "flex-direction",
        "flex-flow",
        "flex-grow",
        "flex-shrink",
        "flex-wrap",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "gap",
        "grid",
        "grid-area",
        "grid-auto-columns",
        "grid-auto-flow",
        "grid-auto-rows",
        "grid-column",
        "grid-column-end",
        "grid-column-start",
        "grid-row",
        "grid-row-end",
        "grid-row-start",
        "grid-template",
        "grid-template-areas",
        "grid-template-columns",
        "grid-template-rows",
        "height",
        "inset",
        "justify-content",
        "justify-items",
        "justify-self",
        "left",
        "letter-spacing",
        "line-height",
        "list-style",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "object-fit",
        "opacity",
        "order",
        "outline",
        "outline-color",
        "outline-offset",
        "outline-style",
        "outline-width",
        "overflow",
        "overflow-wrap",
        "overflow-x",
        "overflow-y",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "place-content",
        "place-items",
        "position",
        "right",
        "row-gap",
        "text-align",
        "text-decoration",
        "text-overflow",
        "text-transform",
        "top",
        "transform",
        "transform-origin",
        "white-space",
        "width",
        "word-break",
        "z-index",
    }
)

APPROVED_CSS_VARIABLES = frozenset(
    {
        "--slide-bg",
        "--surface",
        "--text-primary",
        "--text-muted",
        "--accent",
        "--secondary",
        "--divider",
        "--primary-color",
        "--secondary-color",
        "--accent-color",
        "--background-color",
        "--surface-color",
        "--text-secondary",
        "--border-color",
        "--success-color",
        "--font-zh",
        "--font-latin",
        "--font-cover-title-size",
        "--font-cover-title-weight",
        "--font-cover-title-line-height",
        "--font-page-title-size",
        "--font-page-title-weight",
        "--font-page-title-line-height",
        "--font-body-size",
        "--font-body-weight",
        "--font-body-line-height",
        "--font-caption-size",
        "--font-caption-weight",
        "--font-caption-line-height",
        "--canvas-margin",
        "--space-unit",
        "--column-gap",
        "--radius",
        "--line-width",
    }
)

DESIGN_COLOR_VARIABLES = frozenset(
    {
        "--slide-bg",
        "--surface",
        "--text-primary",
        "--text-muted",
        "--accent",
        "--secondary",
        "--divider",
        "--primary-color",
        "--secondary-color",
        "--accent-color",
        "--background-color",
        "--surface-color",
        "--text-secondary",
        "--border-color",
        "--success-color",
    }
)
COLOR_ONLY_PROPERTIES = frozenset(
    {
        "color",
        "background-color",
        "border-color",
        "border-top-color",
        "border-right-color",
        "border-bottom-color",
        "border-left-color",
        "outline-color",
    }
)
COLOR_BEARING_PROPERTIES = frozenset(
    {
        "background",
        "background-image",
        "border",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
        "box-shadow",
        "outline",
        "text-decoration",
    }
)


@dataclass(frozen=True)
class SanitizationDiagnostic:
    code: str
    message: str
    severity: str = "warning"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SanitizedFragment:
    html: str
    diagnostics: tuple[SanitizationDiagnostic, ...]


@dataclass(frozen=True)
class SanitizedCSS:
    css: str
    diagnostics: tuple[SanitizationDiagnostic, ...]


def sanitize_html_fragment(fragment: str) -> SanitizedFragment:
    raw = str(fragment or "")
    if len(raw) > MAX_HTML_CHARS:
        raise ValueError(f"body_html exceeds {MAX_HTML_CHARS} characters")

    diagnostics, node_count, path_count = _audit_html(raw)
    if node_count > MAX_DOM_NODES:
        diagnostics.append(
            SanitizationDiagnostic(
                code="HTML_NODE_BUDGET_EXCEEDED",
                severity="error",
                message=f"HTML contains {node_count} nodes; limit is {MAX_DOM_NODES}.",
                details={"node_count": node_count, "limit": MAX_DOM_NODES},
            )
        )
    if path_count > MAX_SVG_PATHS:
        diagnostics.append(
            SanitizationDiagnostic(
                code="SVG_PATH_BUDGET_EXCEEDED",
                severity="error",
                message=f"Inline SVG contains {path_count} paths; limit is {MAX_SVG_PATHS}.",
                details={"path_count": path_count, "limit": MAX_SVG_PATHS},
            )
        )

    cleaner = bleach.Cleaner(
        tags=ALLOWED_TAGS,
        attributes=_attribute_allowed,
        protocols=(),
        strip=True,
        strip_comments=True,
    )
    sanitized = cleaner.clean(raw).strip()
    if not sanitized:
        diagnostics.append(
            SanitizationDiagnostic(
                code="HTML_EMPTY_AFTER_SANITIZE",
                severity="error",
                message="The page body became empty after unsafe markup was removed.",
            )
        )
    return SanitizedFragment(sanitized, tuple(diagnostics))


def sanitize_css(css: str) -> SanitizedCSS:
    raw = str(css or "")
    if len(raw) > MAX_CSS_CHARS:
        raise ValueError(f"page_css exceeds {MAX_CSS_CHARS} characters")

    diagnostics: list[SanitizationDiagnostic] = []
    rules = tinycss2.parse_stylesheet(raw, skip_comments=True, skip_whitespace=True)
    safe_rules: list[str] = []
    declaration_count = 0
    qualified_count = 0
    shadow_count = 0

    for rule in rules:
        if rule.type == "error":
            diagnostics.append(
                SanitizationDiagnostic(
                    code="CSS_PARSE_ERROR",
                    severity="error",
                    message=str(getattr(rule, "message", "CSS parse error")),
                )
            )
            continue
        if rule.type != "qualified-rule":
            diagnostics.append(
                SanitizationDiagnostic(
                    code="CSS_AT_RULE_REMOVED",
                    message=f"Removed unsupported CSS {getattr(rule, 'at_keyword', 'at-rule')}.",
                )
            )
            continue
        qualified_count += 1
        if qualified_count > MAX_CSS_RULES:
            diagnostics.append(
                SanitizationDiagnostic(
                    code="CSS_RULE_BUDGET_EXCEEDED",
                    severity="error",
                    message=f"CSS rule count exceeds {MAX_CSS_RULES}.",
                )
            )
            break

        selector = tinycss2.serialize(rule.prelude).strip()
        if not _safe_selector(selector):
            diagnostics.append(
                SanitizationDiagnostic(
                    code="CSS_SELECTOR_REMOVED",
                    message="Removed a selector that escaped the .slide scope.",
                    details={"selector": selector[:240]},
                )
            )
            continue

        declarations = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)
        safe_declarations: list[str] = []
        for declaration in declarations:
            if declaration.type == "error":
                diagnostics.append(
                    SanitizationDiagnostic(
                        code="CSS_DECLARATION_PARSE_ERROR",
                        message=str(getattr(declaration, "message", "Invalid CSS declaration")),
                    )
                )
                continue
            if declaration.type != "declaration":
                continue
            declaration_count += 1
            if declaration_count > MAX_CSS_DECLARATIONS:
                diagnostics.append(
                    SanitizationDiagnostic(
                        code="CSS_DECLARATION_BUDGET_EXCEEDED",
                        severity="error",
                        message=f"CSS declaration count exceeds {MAX_CSS_DECLARATIONS}.",
                    )
                )
                break
            name = str(declaration.lower_name or "").strip()
            if not _allowed_property(name):
                diagnostics.append(
                    SanitizationDiagnostic(
                        code="CSS_PROPERTY_REMOVED",
                        message=f"Removed unsupported CSS property: {name or 'unknown'}.",
                        details={"property": name},
                    )
                )
                continue
            value = tinycss2.serialize(declaration.value).strip()
            reason = _unsafe_css_value(name, value, declaration.value)
            if reason:
                diagnostics.append(
                    SanitizationDiagnostic(
                        code="CSS_VALUE_REMOVED",
                        message=f"Removed unsafe value for {name}: {reason}.",
                        details={"property": name, "reason": reason},
                    )
                )
                continue
            if name == "box-shadow":
                shadow_count += max(1, value.count(",") + 1)
            important = " !important" if declaration.important else ""
            safe_declarations.append(f"{name}:{value}{important}")
        if safe_declarations:
            safe_rules.append(f"{selector}{{{';'.join(safe_declarations)}}}")

    if shadow_count > 24:
        diagnostics.append(
            SanitizationDiagnostic(
                code="CSS_SHADOW_BUDGET_EXCEEDED",
                severity="error",
                message="Page CSS uses too many shadows.",
                details={"shadow_count": shadow_count, "limit": 24},
            )
        )
    return SanitizedCSS("\n".join(safe_rules), tuple(diagnostics))


def _audit_html(fragment: str) -> tuple[list[SanitizationDiagnostic], int, int]:
    diagnostics: list[SanitizationDiagnostic] = []
    parser = html5lib.HTMLParser(namespaceHTMLElements=False)
    try:
        root = parser.parseFragment(fragment)
    except Exception as exc:  # html5lib is intentionally tolerant, but keep a hard boundary.
        return (
            [
                SanitizationDiagnostic(
                    code="HTML_PARSE_ERROR",
                    severity="error",
                    message=f"HTML parser rejected the fragment: {exc}",
                )
            ],
            0,
            0,
        )

    node_count = 0
    path_count = 0
    removed_tags: set[str] = set()
    removed_attributes: set[str] = set()
    for element in root.iter():
        tag = _local_name(getattr(element, "tag", ""))
        if not tag or tag == "document_fragment":
            continue
        node_count += 1
        if tag == "path":
            path_count += 1
        if tag not in ALLOWED_TAGS:
            removed_tags.add(tag)
        for raw_name, value in getattr(element, "attrib", {}).items():
            name = _local_name(raw_name)
            if not _attribute_allowed(tag, name, str(value)):
                removed_attributes.add(name)
    if removed_tags:
        diagnostics.append(
            SanitizationDiagnostic(
                code="HTML_TAGS_REMOVED",
                message="Removed unsupported HTML/SVG elements.",
                details={"tags": sorted(removed_tags)},
            )
        )
    if removed_attributes:
        diagnostics.append(
            SanitizationDiagnostic(
                code="HTML_ATTRIBUTES_REMOVED",
                message="Removed unsupported or unsafe attributes.",
                details={"attributes": sorted(removed_attributes)},
            )
        )
    return diagnostics, node_count, path_count


def _attribute_allowed(tag: str, name: str, value: str) -> bool:
    tag = _local_name(tag)
    name = _local_name(name).casefold()
    value = str(value or "").strip()
    if len(value) > 2_500:
        return False
    if name.casefold().startswith("on") or name in {"style", "href", "src", "srcset", "formaction"}:
        return False
    if name in GLOBAL_ATTRIBUTES:
        return _safe_global_attribute(name, value)
    if tag in SVG_TAGS and name in SVG_ATTRIBUTES:
        return _safe_svg_attribute(name, value)
    return False


def _safe_global_attribute(name: str, value: str) -> bool:
    if name == "class":
        return bool(re.fullmatch(r"[a-zA-Z0-9 _-]{0,500}", value))
    if name in {"data-decorative", "data-allow-overlap", "aria-hidden"}:
        return value.casefold() in {"true", "false"}
    if name == "data-title-lines":
        return value in {"1", "2", "3"}
    if name == "data-icon":
        return bool(re.fullmatch(r"[a-z0-9_-]{1,64}", value))
    if name in {"data-box", "data-layout", "data-slot", "role"}:
        return bool(re.fullmatch(r"[a-zA-Z0-9 _:-]{1,120}", value))
    if name == "aria-label":
        return len(value) <= 300
    return True


def _safe_svg_attribute(name: str, value: str) -> bool:
    lowered = value.casefold()
    if any(token in lowered for token in ("url(", "javascript:", "data:", "http:", "https:", "var(")):
        return False
    if name == "d":
        return (
            len(value) <= 2_000
            and bool(re.fullmatch(r"[0-9a-zA-Z+.,\-\s]+", value))
            and _svg_numeric_magnitude_allowed(value, maximum=10_000, max_numbers=500)
        )
    if name == "points":
        return (
            len(value) <= 1_000
            and bool(re.fullmatch(r"[0-9+.,\-\s]+", value))
            and _svg_numeric_magnitude_allowed(value, maximum=10_000, max_numbers=300)
        )
    if name == "transform":
        return (
            len(value) <= 300
            and bool(re.fullmatch(r"[a-zA-Z0-9+.,()\-\s]+", value))
            and _svg_numeric_magnitude_allowed(value, maximum=2_000, max_numbers=24)
        )
    if name in {"fill", "stroke"}:
        # Simple model SVG geometry inherits the locked theme through CSS.
        # Literal colors would create a per-page palette outside DesignSpec.
        return value in {"none", "currentColor"}
    return bool(re.fullmatch(r"[a-zA-Z0-9+.,%\-\s]+", value)) and _svg_numeric_magnitude_allowed(
        value,
        maximum=10_000,
        max_numbers=24,
    )


def _safe_selector(selector: str) -> bool:
    if not selector or len(selector) > 500:
        return False
    lowered = selector.casefold()
    if any(token in lowered for token in ("html", "body", ":root", ":has(", "@", "\\")):
        return False
    # Generated pseudo-elements are not represented by elementsFromPoint and
    # can paint an opaque full-slide veil while every real DOM node appears
    # valid. Recipes use ordinary elements/backgrounds instead.
    if "::" in lowered or re.search(
        r":(?:before|after|first-line|first-letter|marker|backdrop)\b",
        lowered,
    ):
        return False
    if not re.fullmatch(r"[a-zA-Z0-9\s>+~.,:#*_=\-\[\]()\"']+", selector):
        return False
    return all(part.strip().startswith(".slide") for part in selector.split(","))


def _allowed_property(name: str) -> bool:
    return name in ALLOWED_CSS_PROPERTIES or bool(re.fullmatch(r"--page-[a-z0-9_-]{1,64}", name))


def _unsafe_css_value(name: str, value: str, tokens: Iterable[Any]) -> str:
    if len(value) > 2_000:
        return "value is too long"
    lowered = value.casefold()
    # CSS is embedded in an HTML raw-text <style> element.  Even a quoted CSS
    # string containing ``</style>`` terminates that element in the HTML parser,
    # so reject every raw '<' before serialization into the controlled shell.
    if "<" in value:
        return "raw HTML delimiters are forbidden in CSS"
    if any(token in lowered for token in ("javascript:", "data:", "http:", "https:", "@import", "-moz-binding", "behavior:")):
        return "external or executable resource"
    if "gradient(" in lowered:
        return "CSS gradients are forbidden"
    if _contains_unsafe_css_token(tokens):
        return "url() or expression() is forbidden"
    theme_reason = _unsafe_theme_value(name, value, tokens)
    if theme_reason:
        return theme_reason
    if _css_token_count(tokens) > 160:
        return "CSS value exceeds the token complexity budget"
    if not _numeric_magnitude_allowed(value, maximum=10_000, max_numbers=80):
        return "numeric magnitude or count exceeds the layout budget"
    repeat_counts = [
        int(item)
        for item in re.findall(r"repeat\(\s*(\d+)", lowered)
        if item.isdigit()
    ]
    if any(count > 12 for count in repeat_counts):
        return "grid repeat count exceeds 12"
    for variable in re.findall(r"var\(\s*(--[a-z0-9_-]+)", lowered):
        if variable not in APPROVED_CSS_VARIABLES and not variable.startswith("--page-"):
            return f"unknown CSS variable {variable}"
    if name == "position" and lowered in {"fixed", "sticky"}:
        return f"position:{lowered} is forbidden"
    if name in {"overflow", "overflow-x", "overflow-y"} and lowered not in {"hidden", "visible", "clip"}:
        return "scroll containers are forbidden"
    if name == "z-index":
        try:
            if abs(int(float(value))) > 50:
                return "z-index exceeds the allowed range"
        except ValueError:
            return "z-index must be numeric"
    if name == "background-image" and lowered != "none":
        return "background images are forbidden"
    if name == "box-shadow":
        numbers = [abs(float(item)) for item in re.findall(r"-?\d+(?:\.\d+)?", value)]
        if any(number > 160 for number in numbers):
            return "shadow geometry exceeds the allowed range"
    if name == "transform":
        scales = [abs(float(item)) for item in re.findall(r"scale(?:x|y)?\(\s*(-?\d+(?:\.\d+)?)", lowered)]
        if any(scale > 4 for scale in scales):
            return "transform scale exceeds the allowed range"
        translations = [
            abs(float(item))
            for item in re.findall(
                r"translate(?:x|y)?\(\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?)",
                lowered,
            )
        ]
        if any(distance > 2_000 for distance in translations):
            return "transform translation exceeds the allowed range"
    if name == "content" and value not in {'""', "''", "none"}:
        return "generated text content is forbidden"
    return ""


def _unsafe_theme_value(name: str, value: str, tokens: Iterable[Any]) -> str:
    """Keep every page inside the locked DesignSpec palette and font system."""

    lowered = value.casefold().strip()
    if name == "font-family":
        font_var = r"var\(\s*--font-(?:zh|latin)\s*\)"
        if lowered == "inherit" or re.fullmatch(
            rf"{font_var}(?:\s*,\s*{font_var})*",
            lowered,
        ):
            return ""
        return "font-family must use only --font-zh/--font-latin"

    if name in COLOR_ONLY_PROPERTIES:
        color_var = (
            r"var\(\s*--(?:slide-bg|surface|text-primary|text-muted|accent|secondary|divider|"
            r"primary-color|secondary-color|accent-color|background-color|surface-color|"
            r"text-secondary|border-color|success-color)\s*\)"
        )
        color_token = rf"(?:{color_var}|currentcolor|transparent|inherit)"
        if re.fullmatch(rf"{color_token}(?:\s+{color_token}){{0,3}}", lowered):
            return ""
        return "color must come from the locked DesignSpec variables"

    if name not in COLOR_BEARING_PROPERTIES:
        return ""
    if re.search(r"var\([^)]*,", lowered):
        return "CSS variable fallbacks are forbidden in color-bearing properties"

    allowed_idents = {
        "none",
        "transparent",
        "currentcolor",
        "inherit",
        "solid",
        "dashed",
        "dotted",
        "double",
        "groove",
        "ridge",
        "inset",
        "outset",
        "hidden",
        "thin",
        "medium",
        "thick",
        "underline",
        "overline",
        "line-through",
        "to",
        "left",
        "right",
        "top",
        "bottom",
        "center",
        "at",
        "circle",
        "ellipse",
        "closest-side",
        "farthest-side",
        "closest-corner",
        "farthest-corner",
        "from",
    }
    allowed_functions = {
        "var",
        "calc",
        "min",
        "max",
        "clamp",
    }

    def inspect(items: Iterable[Any]) -> str:
        for token in items:
            token_type = str(getattr(token, "type", ""))
            if token_type == "hash":
                return "literal colors are forbidden"
            if token_type == "ident":
                ident = str(getattr(token, "value", "")).casefold()
                if ident not in allowed_idents:
                    return f"unapproved color or keyword {ident}"
            if token_type == "function":
                function_name = str(
                    getattr(token, "lower_name", getattr(token, "name", ""))
                ).casefold()
                arguments = tuple(getattr(token, "arguments", ()))
                if function_name not in allowed_functions:
                    return f"unapproved color function {function_name}"
                if function_name == "var":
                    variable = tinycss2.serialize(arguments).strip().casefold()
                    if variable not in APPROVED_CSS_VARIABLES:
                        return f"variable {variable or '<empty>'} is not a DesignSpec token"
                    continue
                nested = inspect(arguments)
                if nested:
                    return nested
        return ""

    return inspect(tokens)


def _contains_unsafe_css_token(tokens: Iterable[Any]) -> bool:
    for token in tokens:
        token_type = str(getattr(token, "type", ""))
        if token_type == "url":
            return True
        if token_type == "function":
            name = str(getattr(token, "lower_name", getattr(token, "name", ""))).casefold()
            if name in {"url", "expression", "image-set", "cross-fade"}:
                return True
            if _contains_unsafe_css_token(getattr(token, "arguments", ())):
                return True
    return False


def _css_token_count(tokens: Iterable[Any]) -> int:
    total = 0
    for token in tokens:
        total += 1
        total += _css_token_count(getattr(token, "arguments", ()))
        total += _css_token_count(getattr(token, "content", ()))
    return total


def _numeric_magnitude_allowed(value: str, *, maximum: float, max_numbers: int) -> bool:
    raw_numbers = re.findall(
        r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if len(raw_numbers) > max_numbers:
        return False
    try:
        return all(abs(float(number)) <= maximum for number in raw_numbers)
    except (OverflowError, ValueError):
        return False


def _svg_numeric_magnitude_allowed(value: str, *, maximum: float, max_numbers: int) -> bool:
    """Bound SVG coordinates, including numbers adjacent to path commands.

    The CSS helper intentionally ignores numbers embedded in identifiers and
    hexadecimal colors. SVG path data commonly writes coordinates as ``M10``;
    using the CSS token rule here would let those coordinates evade bounds.
    """

    raw_numbers = re.findall(
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if len(raw_numbers) > max_numbers:
        return False
    try:
        return all(abs(float(number)) <= maximum for number in raw_numbers)
    except (OverflowError, ValueError):
        return False


def _local_name(value: Any) -> str:
    text = str(value or "")
    return text.rsplit("}", 1)[-1].casefold()


__all__ = [
    "MAX_CSS_CHARS",
    "MAX_DOM_NODES",
    "MAX_HTML_CHARS",
    "SanitizationDiagnostic",
    "SanitizedCSS",
    "SanitizedFragment",
    "sanitize_css",
    "sanitize_html_fragment",
]
