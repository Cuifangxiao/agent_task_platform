"""PPTX rendering for deck specs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


PAGE_WIDTH = Inches(13.333)
PAGE_HEIGHT = Inches(7.5)

FONT_FAMILY = "Microsoft YaHei"
COLOR_BG = RGBColor(248, 250, 252)
COLOR_INK = RGBColor(24, 35, 48)
COLOR_MUTED = RGBColor(91, 104, 120)
COLOR_ACCENT = RGBColor(27, 94, 140)
COLOR_ACCENT_2 = RGBColor(223, 88, 69)
COLOR_PANEL = RGBColor(255, 255, 255)
COLOR_PANEL_ALT = RGBColor(235, 243, 249)
COLOR_LINE = RGBColor(209, 219, 229)


class DeckRenderer:
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def render(self, request_id: str, plan: dict[str, Any], slides: list[dict[str, Any]]) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)

        prs = Presentation()
        prs.slide_width = PAGE_WIDTH
        prs.slide_height = PAGE_HEIGHT

        for slide in slides:
            self._add_slide(prs, plan, slide)

        path = self._output_dir / f"{request_id}.pptx"
        prs.save(path)
        return path.resolve()

    def _add_slide(self, prs: Presentation, plan: dict[str, Any], slide_spec: dict[str, Any]) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        layout = _normalize_layout(slide_spec.get("layout"))

        self._add_background(slide)

        if int(slide_spec.get("index") or 1) == 1 or layout == "title":
            self._add_title_slide(slide, plan, slide_spec)
        elif layout == "section":
            self._add_section_slide(slide, slide_spec)
        elif layout == "comparison":
            self._add_comparison_slide(slide, slide_spec)
        elif layout == "summary":
            self._add_summary_slide(slide, slide_spec)
        else:
            self._add_content_slide(slide, slide_spec)

        self._add_footer(slide, plan, slide_spec)
        self._add_speaker_notes(slide, slide_spec)

    @staticmethod
    def _add_background(slide) -> None:
        bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, PAGE_WIDTH, PAGE_HEIGHT)
        bg.fill.solid()
        bg.fill.fore_color.rgb = COLOR_BG
        bg.line.fill.background()

        top = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, PAGE_WIDTH, Inches(0.08))
        top.fill.solid()
        top.fill.fore_color.rgb = COLOR_ACCENT
        top.line.fill.background()

        tab = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(0.08), Inches(1.2), Inches(0.08))
        tab.fill.solid()
        tab.fill.fore_color.rgb = COLOR_ACCENT_2
        tab.line.fill.background()

    @staticmethod
    def _add_footer(slide, plan: dict[str, Any], slide_spec: dict[str, Any]) -> None:
        deck_title = _truncate_text(str(plan.get("title") or ""), 64)
        line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.65), Inches(6.72), Inches(12.0), Inches(0.01))
        line.fill.solid()
        line.fill.fore_color.rgb = COLOR_LINE
        line.line.fill.background()

        _add_text(
            slide,
            deck_title,
            Inches(0.68),
            Inches(6.85),
            Inches(8.4),
            Inches(0.25),
            size=8.5,
            color=COLOR_MUTED,
        )
        _add_text(
            slide,
            str(slide_spec.get("index") or ""),
            Inches(12.1),
            Inches(6.85),
            Inches(0.55),
            Inches(0.25),
            size=8.5,
            color=COLOR_MUTED,
            align=PP_ALIGN.RIGHT,
        )

    @staticmethod
    def _add_title_slide(slide, plan: dict[str, Any], slide_spec: dict[str, Any]) -> None:
        title = _truncate_text(str(plan.get("title") or slide_spec.get("title") or "Untitled deck"), 96)
        key_message = _truncate_text(str(slide_spec.get("key_message") or plan.get("narrative") or ""), 180)

        accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(9.35), Inches(0.9), Inches(2.95), Inches(4.85))
        accent.fill.solid()
        accent.fill.fore_color.rgb = COLOR_PANEL_ALT
        accent.line.color.rgb = COLOR_LINE

        for idx, color in enumerate((COLOR_ACCENT, COLOR_ACCENT_2, RGBColor(63, 123, 106))):
            stripe = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(9.75 + idx * 0.55),
                Inches(1.25 + idx * 0.55),
                Inches(0.34),
                Inches(3.5 - idx * 0.42),
            )
            stripe.fill.solid()
            stripe.fill.fore_color.rgb = color
            stripe.line.fill.background()

        _add_text(
            slide,
            title,
            Inches(0.85),
            Inches(1.42),
            Inches(8.15),
            Inches(1.7),
            size=_font_size_for_length(title, 38, 27),
            bold=True,
            color=COLOR_INK,
        )
        _add_text(
            slide,
            key_message,
            Inches(0.9),
            Inches(3.3),
            Inches(7.7),
            Inches(0.95),
            size=_font_size_for_length(key_message, 18, 13),
            color=COLOR_MUTED,
        )

    @staticmethod
    def _add_section_slide(slide, slide_spec: dict[str, Any]) -> None:
        title = _truncate_text(str(slide_spec.get("title") or "Section"), 88)
        key_message = _truncate_text(str(slide_spec.get("key_message") or ""), 180)

        band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.65), Inches(1.05), Inches(12.0), Inches(4.85))
        band.fill.solid()
        band.fill.fore_color.rgb = COLOR_ACCENT
        band.line.fill.background()

        marker = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.65), Inches(1.05), Inches(0.16), Inches(4.85))
        marker.fill.solid()
        marker.fill.fore_color.rgb = COLOR_ACCENT_2
        marker.line.fill.background()

        _add_text(
            slide,
            title,
            Inches(1.15),
            Inches(2.18),
            Inches(10.55),
            Inches(1.1),
            size=_font_size_for_length(title, 34, 24),
            bold=True,
            color=RGBColor(255, 255, 255),
        )
        _add_text(
            slide,
            key_message,
            Inches(1.18),
            Inches(3.45),
            Inches(9.8),
            Inches(0.7),
            size=_font_size_for_length(key_message, 17, 12),
            color=RGBColor(230, 240, 248),
        )

    @staticmethod
    def _add_content_slide(slide, slide_spec: dict[str, Any]) -> None:
        title, key_message, bullets, visual = _slide_text_parts(slide_spec)
        _add_slide_header(slide, title, key_message)

        _add_bullet_list(
            slide,
            bullets,
            Inches(0.9),
            Inches(1.95),
            Inches(7.5),
            Inches(4.25),
            font_size=_bullet_font_size(bullets, 17),
        )

        panel = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(8.9), Inches(1.65), Inches(3.35), Inches(4.55))
        panel.fill.solid()
        panel.fill.fore_color.rgb = COLOR_PANEL
        panel.line.color.rgb = COLOR_LINE

        cap = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(8.9), Inches(1.65), Inches(3.35), Inches(0.12))
        cap.fill.solid()
        cap.fill.fore_color.rgb = COLOR_ACCENT_2
        cap.line.fill.background()

        _add_text(
            slide,
            _truncate_text(visual or "Suggested visual", 150),
            Inches(9.25),
            Inches(2.15),
            Inches(2.65),
            Inches(2.55),
            size=_font_size_for_length(visual, 13, 10),
            color=COLOR_MUTED,
        )

    @staticmethod
    def _add_comparison_slide(slide, slide_spec: dict[str, Any]) -> None:
        title, key_message, bullets, _ = _slide_text_parts(slide_spec)
        _add_slide_header(slide, title, key_message)

        left_items, right_items = _split_comparison_items(bullets)
        _add_panel(slide, Inches(0.85), Inches(1.9), Inches(5.75), Inches(4.35), "A", COLOR_ACCENT)
        _add_panel(slide, Inches(6.85), Inches(1.9), Inches(5.75), Inches(4.35), "B", COLOR_ACCENT_2)
        _add_bullet_list(slide, left_items, Inches(1.2), Inches(2.55), Inches(5.05), Inches(3.35), font_size=14.5)
        _add_bullet_list(slide, right_items, Inches(7.2), Inches(2.55), Inches(5.05), Inches(3.35), font_size=14.5)

    @staticmethod
    def _add_summary_slide(slide, slide_spec: dict[str, Any]) -> None:
        title, key_message, bullets, visual = _slide_text_parts(slide_spec)
        _add_slide_header(slide, title, key_message)

        items = bullets[:4] or [key_message]
        card_width = 11.3 / max(1, min(len(items), 4))
        for index, item in enumerate(items[:4]):
            x = Inches(0.85 + index * card_width)
            box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, Inches(2.0), Inches(card_width - 0.22), Inches(2.65))
            box.fill.solid()
            box.fill.fore_color.rgb = COLOR_PANEL
            box.line.color.rgb = COLOR_LINE

            _add_text(
                slide,
                f"{index + 1}",
                x + Inches(0.25),
                Inches(2.25),
                Inches(0.45),
                Inches(0.4),
                size=18,
                bold=True,
                color=COLOR_ACCENT,
            )
            _add_text(
                slide,
                _truncate_text(item, 120),
                x + Inches(0.25),
                Inches(2.85),
                Inches(card_width - 0.75),
                Inches(1.25),
                size=_font_size_for_length(item, 14, 10),
                color=COLOR_INK,
            )

        if visual:
            _add_text(
                slide,
                _truncate_text(visual, 150),
                Inches(1.0),
                Inches(5.15),
                Inches(10.8),
                Inches(0.55),
                size=12,
                color=COLOR_MUTED,
                align=PP_ALIGN.CENTER,
            )

    @staticmethod
    def _add_speaker_notes(slide, slide_spec: dict[str, Any]) -> None:
        notes = str(slide_spec.get("speaker_notes") or "").strip()
        if not notes:
            key_message = str(slide_spec.get("key_message") or "").strip()
            bullets = _normalize_string_list(slide_spec.get("bullets"))
            notes = "\n".join([key_message, *bullets]).strip()
        if not notes:
            return
        try:
            slide.notes_slide.notes_text_frame.text = notes
        except (AttributeError, ValueError):
            return


def _add_slide_header(slide, title: str, key_message: str) -> None:
    _add_text(
        slide,
        title,
        Inches(0.72),
        Inches(0.42),
        Inches(11.8),
        Inches(0.58),
        size=_font_size_for_length(title, 24, 17),
        bold=True,
        color=COLOR_INK,
    )
    if key_message:
        _add_text(
            slide,
            key_message,
            Inches(0.75),
            Inches(1.12),
            Inches(10.9),
            Inches(0.5),
            size=_font_size_for_length(key_message, 13, 10),
            color=COLOR_ACCENT,
        )


def _add_text(
    slide,
    text: str,
    left,
    top,
    width,
    height,
    *,
    size: float,
    color: RGBColor,
    bold: bool = False,
    align: PP_ALIGN = PP_ALIGN.LEFT,
) -> None:
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Inches(0.02)
    frame.margin_right = Inches(0.02)
    frame.margin_top = Inches(0.02)
    frame.margin_bottom = Inches(0.02)
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    run.font.name = FONT_FAMILY
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _add_bullet_list(slide, bullets: list[str], left, top, width, height, *, font_size: float) -> None:
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Inches(0.06)
    frame.margin_right = Inches(0.04)
    frame.margin_top = Inches(0.02)
    frame.margin_bottom = Inches(0.02)
    for index, bullet in enumerate(_fit_bullets(bullets)):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.text = _truncate_text(bullet, 125)
        paragraph.level = 0
        paragraph.font.name = FONT_FAMILY
        paragraph.font.size = Pt(font_size)
        paragraph.font.color.rgb = COLOR_INK
        paragraph.space_after = Pt(max(3, 9 - len(bullets)))


def _add_panel(slide, left, top, width, height, label: str, color: RGBColor) -> None:
    panel = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    panel.fill.solid()
    panel.fill.fore_color.rgb = COLOR_PANEL
    panel.line.color.rgb = COLOR_LINE
    marker = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, Inches(0.14))
    marker.fill.solid()
    marker.fill.fore_color.rgb = color
    marker.line.fill.background()
    _add_text(slide, label, left + Inches(0.28), top + Inches(0.32), Inches(0.55), Inches(0.32), size=15, bold=True, color=color)


def _slide_text_parts(slide_spec: dict[str, Any]) -> tuple[str, str, list[str], str]:
    title = _truncate_text(str(slide_spec.get("title") or "Untitled slide"), 92)
    key_message = _truncate_text(str(slide_spec.get("key_message") or ""), 160)
    bullets = _normalize_string_list(slide_spec.get("bullets"))
    visual = str(slide_spec.get("visual_suggestion") or "").strip()
    return title, key_message, bullets, visual


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def _fit_bullets(items: list[str]) -> list[str]:
    max_items = 5
    if len(items) > 5:
        max_items = 4
    return [_truncate_text(item, 125) for item in items[:max_items]]


def _split_comparison_items(items: list[str]) -> tuple[list[str], list[str]]:
    if not items:
        return ["Key difference"], ["Implication"]
    left: list[str] = []
    right: list[str] = []
    for item in items:
        if "|" in item:
            a, b = item.split("|", 1)
            left.append(a.strip())
            right.append(b.strip())
        elif len(left) <= len(right):
            left.append(item)
        else:
            right.append(item)
    return left or items[:1], right or items[1:2] or ["Contrast point"]


def _normalize_layout(value: Any) -> str:
    layout = str(value or "").strip().lower()
    return layout if layout in {"title", "section", "content", "comparison", "summary"} else "content"


def _font_size_for_length(text: str, base: float, minimum: float) -> float:
    length = len(text or "")
    if length > 140:
        return max(minimum, base - 8)
    if length > 95:
        return max(minimum, base - 5)
    if length > 60:
        return max(minimum, base - 3)
    return base


def _bullet_font_size(bullets: list[str], base: float) -> float:
    total = sum(len(item) for item in bullets)
    if len(bullets) >= 5 or total > 420:
        return max(11.5, base - 4)
    if total > 280:
        return max(12.5, base - 2.5)
    return base


def _truncate_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "..."
