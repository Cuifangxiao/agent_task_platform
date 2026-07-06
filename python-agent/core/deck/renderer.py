"""PPTX rendering for deck specs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


PAGE_WIDTH = Inches(13.333)
PAGE_HEIGHT = Inches(7.5)


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
        layout = str(slide_spec.get("layout") or "").lower()

        self._add_background(slide)
        self._add_slide_number(slide, slide_spec)

        if int(slide_spec.get("index") or 1) == 1 or layout == "title":
            self._add_title_slide(slide, plan, slide_spec)
            return

        if layout == "section":
            self._add_section_slide(slide, slide_spec)
            return

        self._add_content_slide(slide, slide_spec)

    @staticmethod
    def _add_background(slide) -> None:
        shape = slide.shapes.add_shape(1, 0, 0, PAGE_WIDTH, PAGE_HEIGHT)
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(248, 249, 251)
        shape.line.fill.background()

        accent = slide.shapes.add_shape(1, 0, 0, Inches(0.12), PAGE_HEIGHT)
        accent.fill.solid()
        accent.fill.fore_color.rgb = RGBColor(30, 96, 145)
        accent.line.fill.background()

    @staticmethod
    def _add_slide_number(slide, slide_spec: dict[str, Any]) -> None:
        box = slide.shapes.add_textbox(Inches(12.25), Inches(6.95), Inches(0.7), Inches(0.25))
        frame = box.text_frame
        frame.clear()
        paragraph = frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.RIGHT
        run = paragraph.add_run()
        run.text = str(slide_spec.get("index") or "")
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(120, 130, 140)

    @staticmethod
    def _add_title_slide(slide, plan: dict[str, Any], slide_spec: dict[str, Any]) -> None:
        title = str(plan.get("title") or slide_spec.get("title") or "Untitled deck")
        key_message = str(slide_spec.get("key_message") or plan.get("narrative") or "")

        title_box = slide.shapes.add_textbox(Inches(0.85), Inches(1.6), Inches(10.9), Inches(1.45))
        title_frame = title_box.text_frame
        title_frame.clear()
        title_frame.word_wrap = True
        paragraph = title_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = title
        run.font.size = Pt(38)
        run.font.bold = True
        run.font.color.rgb = RGBColor(24, 35, 48)

        subtitle_box = slide.shapes.add_textbox(Inches(0.9), Inches(3.25), Inches(9.8), Inches(0.85))
        subtitle_frame = subtitle_box.text_frame
        subtitle_frame.clear()
        subtitle_frame.word_wrap = True
        paragraph = subtitle_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = key_message
        run.font.size = Pt(18)
        run.font.color.rgb = RGBColor(76, 88, 102)

    @staticmethod
    def _add_section_slide(slide, slide_spec: dict[str, Any]) -> None:
        title = str(slide_spec.get("title") or "Section")
        key_message = str(slide_spec.get("key_message") or "")

        title_box = slide.shapes.add_textbox(Inches(0.85), Inches(2.25), Inches(10.6), Inches(1.1))
        title_frame = title_box.text_frame
        title_frame.clear()
        paragraph = title_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = title
        run.font.size = Pt(34)
        run.font.bold = True
        run.font.color.rgb = RGBColor(24, 35, 48)

        msg_box = slide.shapes.add_textbox(Inches(0.9), Inches(3.45), Inches(9.8), Inches(0.7))
        msg_frame = msg_box.text_frame
        msg_frame.clear()
        paragraph = msg_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = key_message
        run.font.size = Pt(17)
        run.font.color.rgb = RGBColor(76, 88, 102)

    @staticmethod
    def _add_content_slide(slide, slide_spec: dict[str, Any]) -> None:
        title = str(slide_spec.get("title") or "Untitled slide")
        key_message = str(slide_spec.get("key_message") or "")
        bullets = _normalize_string_list(slide_spec.get("bullets"))
        visual = str(slide_spec.get("visual_suggestion") or "")

        title_box = slide.shapes.add_textbox(Inches(0.72), Inches(0.45), Inches(11.45), Inches(0.6))
        title_frame = title_box.text_frame
        title_frame.clear()
        title_frame.word_wrap = True
        paragraph = title_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = title
        run.font.size = Pt(24)
        run.font.bold = True
        run.font.color.rgb = RGBColor(24, 35, 48)

        msg_box = slide.shapes.add_textbox(Inches(0.75), Inches(1.15), Inches(8.25), Inches(0.55))
        msg_frame = msg_box.text_frame
        msg_frame.clear()
        msg_frame.word_wrap = True
        paragraph = msg_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = key_message
        run.font.size = Pt(13)
        run.font.color.rgb = RGBColor(30, 96, 145)

        bullet_box = slide.shapes.add_textbox(Inches(0.95), Inches(2.05), Inches(7.6), Inches(3.8))
        bullet_frame = bullet_box.text_frame
        bullet_frame.clear()
        bullet_frame.word_wrap = True
        for index, bullet in enumerate(bullets[:5]):
            paragraph = bullet_frame.paragraphs[0] if index == 0 else bullet_frame.add_paragraph()
            paragraph.text = bullet
            paragraph.level = 0
            paragraph.font.size = Pt(17)
            paragraph.font.color.rgb = RGBColor(40, 50, 62)
            paragraph.space_after = Pt(8)

        visual_shape = slide.shapes.add_shape(1, Inches(9.15), Inches(1.55), Inches(3.25), Inches(4.45))
        visual_shape.fill.solid()
        visual_shape.fill.fore_color.rgb = RGBColor(232, 239, 246)
        visual_shape.line.color.rgb = RGBColor(189, 204, 219)

        visual_box = slide.shapes.add_textbox(Inches(9.45), Inches(1.95), Inches(2.65), Inches(3.45))
        visual_frame = visual_box.text_frame
        visual_frame.clear()
        visual_frame.word_wrap = True
        paragraph = visual_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = visual or "Suggested visual"
        run.font.size = Pt(13)
        run.font.color.rgb = RGBColor(58, 74, 91)


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            items.append(text)
    return items

