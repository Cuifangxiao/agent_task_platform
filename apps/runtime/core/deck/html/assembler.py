"""Assemble sanitized model fragments into the controlled slide shell."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path

import html5lib

from core.deck.design.html_catalog import HTML_DESIGN_SYSTEM_DIR
from core.deck.html.icons import IconRegistry, icon_registry
from core.deck.html.sanitizer import SanitizationDiagnostic, sanitize_css, sanitize_html_fragment
from core.deck.schemas import DesignSpec


BASE_CSS_PATH = HTML_DESIGN_SYSTEM_DIR / "base.css"
CSP = (
    "default-src 'none'; "
    "script-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src 'none'; "
    "font-src 'none'; "
    "connect-src 'none'; "
    "media-src 'none'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "worker-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "navigate-to 'none'"
)


@dataclass(frozen=True)
class HTMLAssemblyResult:
    document: str
    body_html: str
    page_css: str
    diagnostics: tuple[SanitizationDiagnostic, ...]
    resolved_icon_keys: tuple[str, ...]
    missing_icon_keys: tuple[str, ...]
    base_css_hash: str
    icon_registry_version: str


class HTMLAssembler:
    def __init__(
        self,
        *,
        base_css_path: Path = BASE_CSS_PATH,
        icons: IconRegistry | None = None,
    ) -> None:
        self._base_css = base_css_path.read_text(encoding="utf-8")
        self._base_css_hash = hashlib.sha256(self._base_css.encode("utf-8")).hexdigest()
        self._icons = icons or icon_registry()

    @property
    def base_css_hash(self) -> str:
        return self._base_css_hash

    @property
    def icon_registry_version(self) -> str:
        return self._icons.source

    def assemble(
        self,
        *,
        body_html: str,
        page_css: str,
        design_spec: DesignSpec | dict,
        language: str = "zh-CN",
        declared_icon_keys: list[str] | None = None,
        expected_layout: str = "",
    ) -> HTMLAssemblyResult:
        design = design_spec if isinstance(design_spec, DesignSpec) else DesignSpec.model_validate(design_spec)
        body = sanitize_html_fragment(body_html)
        css = sanitize_css(page_css)
        locked_body, root_diagnostics = _lock_slide_root(body.html, expected_layout)
        injected, resolved, missing = self._icons.inject(locked_body)
        diagnostics = list(body.diagnostics) + list(css.diagnostics) + root_diagnostics

        declared = {str(key).strip() for key in declared_icon_keys or [] if str(key).strip()}
        undeclared = sorted(set(resolved) - declared) if declared else []
        missing_declared = sorted(declared - set(resolved))
        if missing:
            diagnostics.append(
                SanitizationDiagnostic(
                    code="ICON_KEYS_MISSING",
                    severity="error",
                    message="Unknown icon placeholders were removed.",
                    details={"keys": sorted(set(missing))},
                )
            )
        if undeclared:
            diagnostics.append(
                SanitizationDiagnostic(
                    code="ICON_KEYS_UNDECLARED",
                    message="The HTML used icon placeholders not listed in icon_keys.",
                    details={"keys": undeclared},
                )
            )
        if missing_declared:
            diagnostics.append(
                SanitizationDiagnostic(
                    code="ICON_KEYS_UNUSED",
                    message="Some declared icon keys were not present in body_html.",
                    details={"keys": missing_declared},
                )
            )

        safe_language = language if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", language or "") else "zh-CN"
        theme_css = design_spec_css(design)
        document = "\n".join(
            [
                "<!doctype html>",
                f'<html lang="{html.escape(safe_language, quote=True)}">',
                "<head>",
                '<meta charset="utf-8">',
                '<meta name="viewport" content="width=1600, initial-scale=1">',
                f'<meta http-equiv="Content-Security-Policy" content="{html.escape(CSP, quote=True)}">',
                "<style id=\"theme-style\">",
                theme_css,
                "</style>",
                "<style id=\"base-style\">",
                self._base_css,
                "</style>",
                "<style id=\"page-style\">",
                css.css,
                "</style>",
                "</head>",
                "<body>",
                injected,
                "</body>",
                "</html>",
            ]
        )
        return HTMLAssemblyResult(
            document=document,
            body_html=injected,
            page_css=css.css,
            diagnostics=tuple(diagnostics),
            resolved_icon_keys=tuple(resolved),
            missing_icon_keys=tuple(missing),
            base_css_hash=self._base_css_hash,
            icon_registry_version=self._icons.source,
        )


def design_spec_css(design: DesignSpec | dict) -> str:
    spec = design if isinstance(design, DesignSpec) else DesignSpec.model_validate(design)
    palette = spec.palette
    typography = spec.typography
    spacing = spec.spacing
    shape = spec.shape_style
    variables = {
        "--slide-bg": palette.background,
        "--surface": palette.surface,
        "--text-primary": palette.text,
        "--text-muted": palette.muted,
        "--accent": palette.accent,
        "--secondary": palette.secondary,
        "--divider": palette.divider,
        "--font-zh": _quoted_font(typography.chinese_font),
        "--font-latin": _quoted_font(typography.latin_font),
        "--font-cover-title-size": f"{typography.cover_title.size_px}px",
        "--font-cover-title-weight": str(typography.cover_title.weight),
        "--font-cover-title-line-height": str(typography.cover_title.line_height),
        "--font-page-title-size": f"{typography.page_title.size_px}px",
        "--font-page-title-weight": str(typography.page_title.weight),
        "--font-page-title-line-height": str(typography.page_title.line_height),
        "--font-body-size": f"{typography.body.size_px}px",
        "--font-body-weight": str(typography.body.weight),
        "--font-body-line-height": str(typography.body.line_height),
        "--font-caption-size": f"{typography.caption.size_px}px",
        "--font-caption-weight": str(typography.caption.weight),
        "--font-caption-line-height": str(typography.caption.line_height),
        "--canvas-margin": f"{spacing.canvas_margin}px",
        "--space-unit": f"{spacing.base_unit}px",
        "--column-gap": f"{spacing.column_gap}px",
        "--radius": f"{shape.radius}px",
        "--line-width": f"{shape.line_width}px",
    }
    return ":root{\n" + "\n".join(f"  {name}: {value};" for name, value in variables.items()) + "\n}"


def _quoted_font(value: str) -> str:
    return '"' + value.replace('"', "") + '"'


def _lock_slide_root(
    fragment: str,
    expected_layout: str,
) -> tuple[str, list[SanitizationDiagnostic]]:
    parser = html5lib.HTMLParser(namespaceHTMLElements=False)
    root = parser.parseFragment(fragment)
    elements = list(root)
    diagnostics: list[SanitizationDiagnostic] = []
    has_stray_text = bool((root.text or "").strip()) or any((element.tail or "").strip() for element in elements)
    valid = (
        len(elements) == 1
        and str(elements[0].tag).casefold() == "section"
        and "slide" in str(elements[0].attrib.get("class") or "").split()
        and not has_stray_text
    )
    if not valid:
        diagnostics.append(
            SanitizationDiagnostic(
                code="SLIDE_ROOT_CONTRACT",
                severity="error",
                message="body_html must contain exactly one top-level section.slide and no stray text.",
            )
        )
    elif expected_layout:
        elements[0].set("data-layout", expected_layout)
    serialized = html5lib.serialize(
        root,
        tree="etree",
        quote_attr_values="always",
        omit_optional_tags=False,
    )
    return serialized.strip(), diagnostics


__all__ = ["BASE_CSS_PATH", "CSP", "HTMLAssembler", "HTMLAssemblyResult", "design_spec_css"]
