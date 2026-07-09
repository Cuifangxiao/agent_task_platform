"""Export rendered HTML slide artifacts to PPTX."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from core.deck.errors import ExportError
from core.deck.export.text_layer import MIN_TEXTBOX_SIZE_PX, TEXTBOX_HEIGHT_SLACK_PX, TEXTBOX_WIDTH_SLACK
from core.deck.rendering.browser import BrowserSession
from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, DeckSpec, SlideSpec

logger = logging.getLogger(__name__)

PAGE_WIDTH = Inches(13.333)
PAGE_HEIGHT = Inches(7.5)
PX_TO_INCH = 1 / 96
PPT_FONT = "Noto Sans SC"
@dataclass
class ExportResult:
    path: Path
    warnings: list[str]


class DeckExporter:
    def __init__(self, output_root: Path, browser_session: BrowserSession | None = None) -> None:
        self._output_root = output_root
        self._browser_session = browser_session

    def export(self, request_id: str, deck: DeckSpec) -> ExportResult:
        request_dir = self._output_root / request_id
        request_dir.mkdir(parents=True, exist_ok=True)
        spec_path = request_dir / "spec.json"
        spec_path.write_text(json.dumps(deck.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        prs = Presentation()
        prs.slide_width = PAGE_WIDTH
        prs.slide_height = PAGE_HEIGHT

        warnings: list[str] = []
        for slide_spec in deck.slides:
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            background_path, text_layer, warning = self._decorative_background_and_text(request_dir, slide_spec)
            if warning:
                warnings.append(warning)
            _add_full_slide_image(slide, background_path)
            for item in text_layer:
                _add_text_box(slide, item)
            _add_speaker_notes(slide, slide_spec)

        path = request_dir / "deck.pptx"
        prs.save(path)
        _validate_pptx(path, expected_slides=len(deck.slides))
        return ExportResult(path=path.resolve(), warnings=warnings)

    def _decorative_background_and_text(
        self,
        request_dir: Path,
        slide: SlideSpec,
    ) -> tuple[Path, list[dict[str, Any]], str]:
        html_path = request_dir / "slides" / f"{slide.index}.html"
        preview_path = request_dir / "slides" / f"{slide.index}.png"
        if html_path.exists():
            try:
                bg_path, text_layer = self._capture_hidden_text_background(html_path, request_dir, slide.index)
                return bg_path, text_layer, ""
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.info("mixed export capture failed for slide %s: %s", slide.index, exc)

        if not preview_path.exists():
            preview_path = _blank_slide_image(request_dir, slide.index)
        return (
            preview_path,
            [],
            f"Slide {slide.index} exported as a full-slide image because text extraction was unavailable.",
        )

    def _capture_hidden_text_background(self, html_path: Path, request_dir: Path, index: int) -> tuple[Path, list[dict[str, Any]]]:
        html = html_path.read_text(encoding="utf-8")
        bg_path = request_dir / "slides" / f"{index}_bg.png"
        if self._browser_session is not None:
            return bg_path, self._browser_session.extract_hidden_text_background(html, bg_path)
        session = BrowserSession()
        try:
            return bg_path, session.extract_hidden_text_background(html, bg_path)
        finally:
            session.close()


def extract_text_layer(html_path: Path) -> list[dict[str, Any]]:
    """Extract editable text geometry from HTML when Playwright is available."""

    html = html_path.read_text(encoding="utf-8")
    session = BrowserSession()
    try:
        return session.extract_text_layer(html)
    finally:
        session.close()


def _add_full_slide_image(slide: Any, path: Path) -> None:
    slide.shapes.add_picture(str(path), 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _validate_pptx(path: Path, *, expected_slides: int) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise ExportError("PPTX export produced an empty file.", stage="export")
    try:
        actual = len(Presentation(str(path)).slides)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise ExportError(f"PPTX export validation failed: {exc}", stage="export") from exc
    if actual != expected_slides:
        raise ExportError(f"PPTX slide count mismatch: expected {expected_slides}, got {actual}.", stage="export")


def _add_text_box(slide: Any, item: dict[str, Any]) -> None:
    text = str(item.get("text") or "").strip()
    if not text:
        return
    left = Inches(float(item.get("x") or 0) * PX_TO_INCH)
    top = Inches(float(item.get("y") or 0) * PX_TO_INCH)
    width = Inches(max(MIN_TEXTBOX_SIZE_PX, float(item.get("width") or 1) * TEXTBOX_WIDTH_SLACK) * PX_TO_INCH)
    height = Inches(max(MIN_TEXTBOX_SIZE_PX, float(item.get("height") or 1) + TEXTBOX_HEIGHT_SLACK_PX) * PX_TO_INCH)
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    paragraph = frame.paragraphs[0]
    paragraph.alignment = _alignment(str(item.get("textAlign") or "left"))
    font_size_px = float(item.get("fontSize") or 18)
    line_height_px = float(item.get("lineHeight") or font_size_px * 1.45)
    if font_size_px > 0:
        paragraph.line_spacing = line_height_px / font_size_px
    run = paragraph.add_run()
    run.text = text
    run.font.name = PPT_FONT
    run.font.size = Pt(round(font_size_px * 0.75 * 2) / 2)
    run.font.bold = _font_weight_is_bold(item.get("fontWeight"))
    rgb = _css_rgb_to_rgbcolor(str(item.get("color") or "rgb(20, 33, 61)"))
    if rgb:
        run.font.color.rgb = rgb
    _set_east_asian_font(run, PPT_FONT)


def _add_speaker_notes(slide: Any, slide_spec: SlideSpec) -> None:
    notes = slide_spec.notes.strip()
    if not notes:
        return
    try:
        slide.notes_slide.notes_text_frame.text = notes
    except (AttributeError, ValueError):
        return


def _blank_slide_image(request_dir: Path, index: int) -> Path:
    path = request_dir / "slides" / f"{index}_blank.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (CANVAS_WIDTH * 2, CANVAS_HEIGHT * 2), (247, 243, 234))
    image.save(path)
    return path


def _alignment(value: str) -> PP_ALIGN:
    normalized = value.strip().lower()
    if normalized == "center":
        return PP_ALIGN.CENTER
    if normalized == "right":
        return PP_ALIGN.RIGHT
    return PP_ALIGN.LEFT


def _font_weight_is_bold(value: Any) -> bool:
    try:
        return int(value) >= 600
    except (TypeError, ValueError):
        return str(value or "").lower() in {"bold", "bolder"}


def _css_rgb_to_rgbcolor(value: str) -> RGBColor | None:
    text = value.strip()
    if text.startswith("#") and len(text) == 7:
        try:
            return RGBColor(int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
        except ValueError:
            return None
    if text.startswith("rgb"):
        raw = text[text.find("(") + 1 : text.find(")")].split(",")
        try:
            values = [int(float(item.strip())) for item in raw[:3]]
        except ValueError:
            return None
        if len(values) == 3:
            return RGBColor(values[0], values[1], values[2])
    return None


def _set_east_asian_font(run: Any, font_name: str) -> None:
    try:
        r_pr = run._r.get_or_add_rPr()  # pylint: disable=protected-access
        r_fonts = r_pr.rFonts
        if r_fonts is None:
            r_fonts = OxmlElement("a:rFonts")
            r_pr.append(r_fonts)
        r_fonts.set(qn("a:ea"), font_name)
    except Exception:  # pylint: disable=broad-exception-caught
        return
