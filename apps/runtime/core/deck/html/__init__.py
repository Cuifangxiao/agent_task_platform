"""Safe HTML authoring primitives used by the HTML-first deck workflow."""

from core.deck.html.assembler import HTMLAssembler, HTMLAssemblyResult
from core.deck.html.fallback import build_fallback_html_spec
from core.deck.html.icons import IconRegistry, icon_registry
from core.deck.html.sanitizer import SanitizationDiagnostic, sanitize_css, sanitize_html_fragment

__all__ = [
    "HTMLAssembler",
    "HTMLAssemblyResult",
    "IconRegistry",
    "SanitizationDiagnostic",
    "build_fallback_html_spec",
    "icon_registry",
    "sanitize_css",
    "sanitize_html_fragment",
]
