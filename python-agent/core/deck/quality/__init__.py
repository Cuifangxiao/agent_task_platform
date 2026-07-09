"""Deterministic and model-backed quality checks for deck output."""

from core.deck.quality.issues import (
    ValidationIssue,
    dedupe_issue_dicts,
    dedupe_issues,
    issues_to_dicts,
)
from core.deck.quality.rules import (
    DOM_VALIDATION_JS,
    validate_deck_spec,
    validate_dom_result,
    validate_html_static,
    validate_slide_spec,
    validate_theme_contrast,
)

__all__ = [
    "ValidationIssue",
    "dedupe_issue_dicts",
    "dedupe_issues",
    "issues_to_dicts",
    "DOM_VALIDATION_JS",
    "validate_deck_spec",
    "validate_dom_result",
    "validate_html_static",
    "validate_slide_spec",
    "validate_theme_contrast",
]
