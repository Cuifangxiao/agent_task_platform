"""Export rendered HTML slide artifacts to PPTX."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from core.deck.errors import ExportError
from core.deck.export.svg_native import add_svg_as_native_shapes
from core.deck.export.text_layer import MIN_TEXTBOX_SIZE_PX, TEXTBOX_HEIGHT_SLACK_PX, TEXTBOX_WIDTH_SLACK
from core.deck.paths import DeckProjectPaths
from core.deck.runtime_metrics import record_fallback
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
    manifest: dict[str, Any]


class DeckExporter:
    def __init__(self, output_root: Path, browser_session: BrowserSession | None = None) -> None:
        self._output_root = output_root
        self._browser_session = browser_session

    def project_paths(self, request_id: str) -> DeckProjectPaths:
        return DeckProjectPaths.for_request(self._output_root, request_id).ensure()

    def export(self, request_id: str, deck: DeckSpec) -> ExportResult:
        project = self.project_paths(request_id)
        project.deck_spec.write_text(json.dumps(deck.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        prs = Presentation()
        prs.slide_width = PAGE_WIDTH
        prs.slide_height = PAGE_HEIGHT

        warnings: list[str] = []
        manifest_slides: list[dict[str, Any]] = []
        for slide_spec in deck.slides:
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            manifest_slide, slide_warnings = self._export_slide(project, slide, slide_spec)
            warnings.extend(slide_warnings)
            _add_speaker_notes(slide, slide_spec)
            manifest_slides.append(manifest_slide)

        path = project.deck_pptx
        prs.save(path)
        structure = _validate_pptx(path, expected_slides=len(deck.slides), expected_text_counts=[item["editable_text_boxes"] for item in manifest_slides])
        degraded_pages = [item["index"] for item in manifest_slides if item["degraded"]]
        manifest = {
            "slide_count": len(deck.slides),
            "degraded": bool(degraded_pages),
            "degraded_pages": degraded_pages,
            "fonts": sorted({font for item in manifest_slides for font in item["fonts"]}),
            "slides": manifest_slides,
            "structure": structure,
            "warnings": warnings,
        }
        project.export_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _backup_authoring_sources(project)
        return ExportResult(path=path.resolve(), warnings=warnings, manifest=manifest)

    def _export_slide(self, project: DeckProjectPaths, slide: Any, slide_spec: SlideSpec) -> tuple[dict[str, Any], list[str]]:
        svg_path = project.svg(slide_spec.index)
        if not svg_path.exists():
            svg_path = project.root / "slides" / f"{slide_spec.index}.svg"
        if svg_path.exists():
            try:
                stats = add_svg_as_native_shapes(slide, svg_path)
                return (
                    {
                        "index": slide_spec.index,
                        "editable_text_boxes": stats.editable_text_boxes,
                        "native_shape_count": stats.shape_count,
                        "export_mode": "native_svg",
                        "degraded": False,
                        "fonts": stats.fonts,
                        "preview": project.relative(project.preview_png(slide_spec.index)),
                        "svg": project.relative(svg_path),
                    },
                    [],
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                record_fallback()
                logger.warning("native SVG export failed for slide %s: %s", slide_spec.index, exc)
                warning = f"Slide {slide_spec.index} native SVG export failed; used the previously rendered preview: {exc}"
                # Do not navigate the browser to HTML containing an SVG that
                # failed native preflight. Its failure may be a forbidden local
                # resource reference; the request-scoped preview is the safe
                # degradation path.
                manifest = self._export_preview_only_slide(project, slide, slide_spec)
                return manifest, [warning]
        manifest, fallback_warnings = self._export_mixed_slide(project, slide, slide_spec)
        return manifest, fallback_warnings

    def _export_mixed_slide(self, project: DeckProjectPaths, slide: Any, slide_spec: SlideSpec) -> tuple[dict[str, Any], list[str]]:
        background_path, text_layer, warning = self._decorative_background_and_text(project, slide_spec)
        warnings = [warning] if warning else []
        unavailable_fonts = sorted(
            {
                str(item.get("fontFamily") or PPT_FONT)
                for item in text_layer
                if item.get("fontAvailable") is False
            }
        )
        if unavailable_fonts:
            warnings.append(f"Slide {slide_spec.index} uses unavailable fonts: {', '.join(unavailable_fonts)}.")
        _add_full_slide_image(slide, background_path)
        for item in text_layer:
            _add_text_box(slide, item)
        return (
            {
                "index": slide_spec.index,
                "editable_text_boxes": len(text_layer),
                "native_shape_count": 0,
                "export_mode": "mixed_raster",
                "degraded": True,
                "fonts": sorted({str(item.get("fontFamily") or PPT_FONT) for item in text_layer}),
                "preview": project.relative(project.preview_png(slide_spec.index)),
                "svg": "",
            },
            warnings,
        )

    def _export_preview_only_slide(self, project: DeckProjectPaths, slide: Any, slide_spec: SlideSpec) -> dict[str, Any]:
        preview_path = project.preview_png(slide_spec.index)
        if not preview_path.exists():
            preview_path = project.root / "slides" / f"{slide_spec.index}.png"
        if not preview_path.exists():
            raise ExportError(f"Slide {slide_spec.index} has no safe preview fallback.", stage="export")
        _add_full_slide_image(slide, preview_path)
        return {
            "index": slide_spec.index,
            "editable_text_boxes": 0,
            "native_shape_count": 0,
            "export_mode": "preview_raster",
            "degraded": True,
            "fonts": [],
            "preview": project.relative(preview_path),
            "svg": project.relative(project.svg(slide_spec.index)) if project.svg(slide_spec.index).exists() else f"slides/{slide_spec.index}.svg",
        }

    def _decorative_background_and_text(
        self,
        project: DeckProjectPaths,
        slide: SlideSpec,
    ) -> tuple[Path, list[dict[str, Any]], str]:
        html_path = project.preview_html(slide.index)
        preview_path = project.preview_png(slide.index)
        if not html_path.exists():
            html_path = project.root / "slides" / f"{slide.index}.html"
        if not preview_path.exists():
            preview_path = project.root / "slides" / f"{slide.index}.png"
        if html_path.exists():
            try:
                bg_path, text_layer = self._capture_hidden_text_background(html_path, project, slide.index)
                return bg_path, text_layer, ""
            except Exception as exc:  # pylint: disable=broad-exception-caught
                record_fallback()
                logger.info("mixed export capture failed for slide %s: %s", slide.index, exc)

        if not preview_path.exists():
            preview_path = _blank_slide_image(project, slide.index)
        return (
            preview_path,
            [],
            f"Slide {slide.index} exported as a full-slide image because text extraction was unavailable.",
        )

    def _capture_hidden_text_background(self, html_path: Path, project: DeckProjectPaths, index: int) -> tuple[Path, list[dict[str, Any]]]:
        bg_path = project.preview_background(index)
        if self._browser_session is not None:
            return bg_path, self._browser_session.extract_hidden_text_background(html_path, bg_path)
        session = BrowserSession()
        try:
            return bg_path, session.extract_hidden_text_background(html_path, bg_path)
        finally:
            session.close()


def extract_text_layer(html_path: Path) -> list[dict[str, Any]]:
    """Extract editable text geometry from HTML when Playwright is available."""

    session = BrowserSession()
    try:
        return session.extract_text_layer(html_path)
    finally:
        session.close()


def _add_full_slide_image(slide: Any, path: Path) -> None:
    slide.shapes.add_picture(str(path), 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _validate_pptx(path: Path, *, expected_slides: int, expected_text_counts: list[int]) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        raise ExportError("PPTX export produced an empty file.", stage="export")
    try:
        presentation = Presentation(str(path))
        actual = len(presentation.slides)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise ExportError(f"PPTX export validation failed: {exc}", stage="export") from exc
    if actual != expected_slides:
        raise ExportError(f"PPTX slide count mismatch: expected {expected_slides}, got {actual}.", stage="export")
    editable_counts: list[int] = []
    used_fonts: set[str] = set()
    for index, slide in enumerate(presentation.slides):
        editable = 0
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            if shape.left < 0 or shape.top < 0 or shape.width <= 0 or shape.height <= 0:
                raise ExportError(f"PPTX slide {index + 1} contains invalid editable text geometry.", stage="export")
            if shape.left + shape.width > presentation.slide_width * 1.03 or shape.top + shape.height > presentation.slide_height * 1.03:
                raise ExportError(f"PPTX slide {index + 1} contains an editable text box outside the canvas.", stage="export")
            if not shape.text.strip():
                continue
            editable += 1
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if run.font.name:
                        used_fonts.add(run.font.name)
        expected = expected_text_counts[index] if index < len(expected_text_counts) else 0
        if expected > 0 and editable < expected:
            raise ExportError(f"PPTX slide {index + 1} lost editable text boxes: expected {expected}, got {editable}.", stage="export")
        editable_counts.append(editable)
    return {"editable_text_boxes": editable_counts, "fonts": sorted(used_fonts), "openable": True}


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
    if bool(item.get("isListItem")):
        _set_bullet(paragraph)
    run = paragraph.add_run()
    run.text = text
    font_name = _font_family_name(str(item.get("fontFamily") or PPT_FONT))
    run.font.name = font_name
    run.font.size = Pt(round(font_size_px * 0.75 * 2) / 2)
    run.font.bold = _font_weight_is_bold(item.get("fontWeight"))
    rgb = _css_rgb_to_rgbcolor(str(item.get("color") or "rgb(20, 33, 61)"))
    if rgb:
        run.font.color.rgb = rgb
    _set_east_asian_font(run, font_name)


def _font_family_name(value: str) -> str:
    first = value.split(",", 1)[0].strip().strip("\"'")
    return first if first and first.lower() not in {"sans-serif", "serif", "monospace"} else PPT_FONT


def _set_bullet(paragraph: Any) -> None:
    try:
        p_pr = paragraph._p.get_or_add_pPr()  # pylint: disable=protected-access
        for child in list(p_pr):
            if child.tag.endswith("buNone"):
                p_pr.remove(child)
        bullet = OxmlElement("a:buChar")
        bullet.set("char", "•")
        p_pr.append(bullet)
    except Exception:  # pylint: disable=broad-exception-caught
        return


def _add_speaker_notes(slide: Any, slide_spec: SlideSpec) -> None:
    notes = slide_spec.notes.strip()
    if slide_spec.sources:
        notes = (notes + "\n\nSources: " + ", ".join(slide_spec.sources)).strip()
    if not notes:
        return
    try:
        slide.notes_slide.notes_text_frame.text = notes
    except (AttributeError, ValueError):
        return


def _blank_slide_image(project: DeckProjectPaths, index: int) -> Path:
    path = project.previews / f"page-{index:03d}_blank.png"
    image = Image.new("RGB", (CANVAS_WIDTH * 2, CANVAS_HEIGHT * 2), (247, 243, 234))
    image.save(path)
    return path


def _backup_authoring_sources(project: DeckProjectPaths) -> None:
    """Freeze the exact author SVGs used for this export."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = project.backup / timestamp / "svg_output"
    counter = 2
    while destination.exists():
        destination = project.backup / f"{timestamp}-{counter}" / "svg_output"
        counter += 1
    if project.svg_output.exists():
        shutil.copytree(project.svg_output, destination)


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
        east_asian = r_pr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
        if east_asian is None:
            east_asian = OxmlElement("a:ea")
            r_pr.append(east_asian)
        east_asian.set("typeface", font_name)
    except Exception:  # pylint: disable=broad-exception-caught
        return
