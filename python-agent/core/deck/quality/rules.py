"""Deterministic deck validation rules."""

from __future__ import annotations

import re
from typing import Any

from core.deck.quality.issues import ValidationIssue
from core.deck.rendering.charts import chart_is_valid
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, COMPONENT_LIMITS, DeckSpec, SlideSpec, has_placeholder_text, slide_text_values

EXTERNAL_RESOURCE_RE = re.compile(
    r"""(?:\b(?:src|href)\s*=\s*["']\s*https?://|url\(\s*["']?\s*https?://|<script\b)""",
    flags=re.IGNORECASE,
)
DOM_VALIDATION_JS = """
() => {
  const issues = [];
  const canvas = { width: 1280, height: 720 };
  const elements = Array.from(document.querySelectorAll('[data-text]'));
  const rects = elements.map((el, index) => {
    const rect = el.getBoundingClientRect();
    const text = (el.innerText || el.textContent || '').trim();
    if (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2) {
      issues.push({ kind: 'overflow', text, detail: 'Text content overflows its box.' });
    }
    if (rect.left < -2 || rect.top < -2 || rect.right > canvas.width + 2 || rect.bottom > canvas.height + 2) {
      issues.push({ kind: 'out_of_bounds', text, detail: 'Text element extends outside the slide canvas.' });
    }
    return { index, text, left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
  }).filter((rect) => rect.width > 1 && rect.height > 1);

  for (let i = 0; i < rects.length; i += 1) {
    for (let j = i + 1; j < rects.length; j += 1) {
      const a = rects[i];
      const b = rects[j];
      const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
      const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
      const area = x * y;
      const smaller = Math.max(1, Math.min(a.width * a.height, b.width * b.height));
      if (area / smaller > 0.04) {
        issues.push({ kind: 'overlap', text: `${a.text} / ${b.text}`, detail: 'Editable text elements overlap.' });
      }
    }
  }
  return issues;
}
"""


def validate_deck_spec(deck: DeckSpec) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_titles: dict[str, int] = {}
    for slide in deck.slides:
        issues.extend(validate_slide_spec(slide))
        normalized_title = slide.title.strip().casefold()
        if normalized_title:
            if normalized_title in seen_titles:
                issues.append(
                    ValidationIssue(
                        page=slide.index,
                        severity="nice_to_have",
                        kind="duplicate_title",
                        description=f"Slide title repeats slide {seen_titles[normalized_title]}.",
                        suggestion="Make this title state the distinct point of the page.",
                    )
                )
            else:
                seen_titles[normalized_title] = slide.index
    return issues


def validate_slide_spec(slide: SlideSpec) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not slide.title.strip():
        issues.append(_issue(slide, "missing_title", "Slide is missing a title.", "Write one viewpoint title."))
    if any(has_placeholder_text(value) for value in slide_text_values(slide)):
        issues.append(_issue(slide, "placeholder_text", "Slide contains placeholder text.", "Rewrite from source content."))
    if slide.component == "chart-takeaway" and not chart_is_valid(slide.chart):
        issues.append(
            _nice_issue(
                slide,
                "chart_fallback",
                "Chart data is missing or invalid, so the page falls back to a text layout.",
                "Use chart schema {type, series[{label,value}], unit} with source-backed values.",
            )
        )
    issues.extend(_density_issues(slide))
    return issues


def validate_html_static(html: str, *, page: int) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not re.search(r'<[^>]+\bclass=["\'][^"\']*\bslide\b', html):
        issues.append(
            ValidationIssue(
                page=page,
                severity="must_fix",
                kind="missing_slide_root",
                description="HTML does not contain a .slide root.",
                suggestion="Render the component inside a fixed 1280x720 .slide element.",
            )
        )
    if "data-text" not in html:
        issues.append(
            ValidationIssue(
                page=page,
                severity="must_fix",
                kind="missing_text_slots",
                description="No editable text slots were marked with data-text.",
                suggestion="Mark title, bullets, labels, and takeaway text with data-text.",
            )
        )
    if EXTERNAL_RESOURCE_RE.search(html):
        issues.append(
            ValidationIssue(
                page=page,
                severity="must_fix",
                kind="unsafe_html",
                description="HTML contains external links or scripts outside the allowed design system.",
                suggestion="Use only inline CSS, local assets, and approved chart runtime.",
            )
        )
    if any(has_placeholder_text(text) for text in re.findall(r">([^<>]+)<", html)):
        issues.append(
            ValidationIssue(
                page=page,
                severity="must_fix",
                kind="placeholder_text",
                description="Rendered HTML contains placeholder text.",
                suggestion="Replace placeholder copy with source-backed content.",
            )
        )
    return issues


def validate_dom_result(raw_issues: Any, *, page: int) -> list[ValidationIssue]:
    if not isinstance(raw_issues, list):
        return []
    issues: list[ValidationIssue] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "dom").strip()
        detail = str(item.get("detail") or "Rendered slide has a DOM layout issue.").strip()
        text = str(item.get("text") or "").strip()
        suffix = f" Text: {text[:60]}" if text else ""
        issues.append(
            ValidationIssue(
                page=page,
                severity="must_fix",
                kind=f"dom_{kind}",
                description=detail + suffix,
                suggestion="Reduce text density, adjust layout, or switch to a lower-density component.",
            )
        )
    return issues


def validate_theme_contrast(deck: DeckSpec) -> list[ValidationIssue]:
    palette = deck.theme.palette
    bg = _hex_to_rgb(palette.get("bg", ""))
    ink = _hex_to_rgb(palette.get("ink", ""))
    muted = _hex_to_rgb(palette.get("muted", ""))
    issues: list[ValidationIssue] = []
    if bg and ink and _contrast_ratio(bg, ink) < 4.5:
        issues.append(
            ValidationIssue(
                page=0,
                severity="must_fix",
                kind="contrast",
                description="Theme ink color does not meet WCAG AA contrast on the background.",
                suggestion="Use a darker ink or lighter background token.",
            )
        )
    if bg and muted and _contrast_ratio(bg, muted) < 3.0:
        issues.append(
            ValidationIssue(
                page=0,
                severity="nice_to_have",
                kind="contrast",
                description="Theme muted color is low contrast on the background.",
                suggestion="Increase muted text contrast.",
            )
        )
    return issues


def _density_issues(slide: SlideSpec) -> list[ValidationIssue]:
    limits = COMPONENT_LIMITS.get(slide.component, {})
    content = slide.content
    issues: list[ValidationIssue] = []

    def check_text(name: str, value: Any, limit_key: str) -> None:
        limit = limits.get(limit_key)
        if limit and len(str(value or "")) > limit:
            issues.append(_issue(slide, "density", f"{name} exceeds {limit} characters.", "Shorten the copy."))

    check_text("Title", slide.title, "title_chars")
    check_text("Subtitle", content.get("subtitle"), "subtitle_chars")
    check_text("Body", content.get("body"), "body_chars")
    check_text("Takeaway", content.get("takeaway"), "takeaway_chars")
    check_text("Quote", content.get("quote"), "quote_chars")

    for key in ("bullets", "items", "steps"):
        if isinstance(content.get(key), list):
            count_limit = limits.get("bullets") or limits.get("items") or limits.get("steps")
            if count_limit and len(content[key]) > count_limit:
                issues.append(_issue(slide, "density", f"{key} has too many items.", "Keep only the strongest items."))
            char_limit = limits.get("bullet_chars") or limits.get("item_chars")
            if char_limit:
                for item in content[key]:
                    if len(str(item)) > char_limit:
                        issues.append(_issue(slide, "density", f"{key} item exceeds {char_limit} characters.", "Shorten the item."))
                        break

    if isinstance(content.get("cards"), list):
        if len(content["cards"]) > limits.get("items", 4):
            issues.append(_issue(slide, "density", "Too many KPI cards.", "Use 2-4 cards."))
        for card in content["cards"]:
            if not isinstance(card, dict):
                continue
            check_text("KPI value", card.get("value"), "value_chars")
            check_text("KPI label", card.get("label"), "label_chars")

    if isinstance(content.get("rows"), list) and len(content["rows"]) > limits.get("rows", 5):
        issues.append(_issue(slide, "density", "Table has too many rows.", "Keep the table compact."))

    return issues


def _issue(slide: SlideSpec, kind: str, description: str, suggestion: str) -> ValidationIssue:
    return ValidationIssue(
        page=slide.index,
        severity="must_fix",
        kind=kind,
        description=description,
        suggestion=suggestion,
    )


def _nice_issue(slide: SlideSpec, kind: str, description: str, suggestion: str) -> ValidationIssue:
    return ValidationIssue(
        page=slide.index,
        severity="nice_to_have",
        kind=kind,
        description=description,
        suggestion=suggestion,
    )


def _hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value) for value in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    l1 = _relative_luminance(a)
    l2 = _relative_luminance(b)
    light, dark = max(l1, l2), min(l1, l2)
    return (light + 0.05) / (dark + 0.05)
