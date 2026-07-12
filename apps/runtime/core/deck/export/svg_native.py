"""Translate the restricted deck SVG subset into native PowerPoint shapes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_CONNECTOR_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, MSO_VERTICAL_ANCHOR, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Emu, Pt

from core.deck.assets import MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS, is_safe_image_href, resolve_safe_image_href
from core.deck.errors import ExportError
from core.deck.rendering.svg import SVG_ALLOWED_TAGS


EMU_PER_PX = 9525


@dataclass(frozen=True)
class SVGNativeStats:
    shape_count: int
    editable_text_boxes: int
    fonts: list[str]


def add_svg_as_native_shapes(slide: Any, svg_path: Path) -> SVGNativeStats:
    """Add one restricted SVG page to an existing blank PowerPoint slide."""

    try:
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as exc:
        raise ExportError(f"Cannot read native SVG {svg_path.name}: {exc}", stage="export") from exc
    if _local_name(root.tag) != "svg":
        raise ExportError(f"Native SVG {svg_path.name} has no <svg> root.", stage="export")

    # Validate every primitive before mutating the destination slide. A failed
    # conversion can then fall back cleanly without leaving partial shapes behind.
    _preflight(root, svg_path)
    stats = _MutableStats()
    _add_children(slide, root, stats, svg_path)
    if stats.shape_count == 0:
        raise ExportError(f"Native SVG {svg_path.name} produced no PowerPoint shapes.", stage="export")
    return SVGNativeStats(stats.shape_count, stats.editable_text_boxes, sorted(stats.fonts))


@dataclass
class _MutableStats:
    shape_count: int = 0
    editable_text_boxes: int = 0
    fonts: set[str] = field(default_factory=set)


def _preflight(parent: ET.Element, svg_path: Path) -> None:
    for element in parent:
        tag = _local_name(element.tag)
        if tag not in SVG_ALLOWED_TAGS:
            raise ExportError(f"Unsupported SVG element <{tag}> reached native export.", stage="export")
        if tag == "g":
            _preflight(element, svg_path)
        elif tag == "rect":
            _finite_attributes(element, "x", "y")
            _finite_attributes(element, "width", "height", positive=True)
        elif tag == "circle":
            _finite_attributes(element, "cx", "cy")
            _finite_attributes(element, "r", positive=True)
        elif tag == "ellipse":
            _finite_attributes(element, "cx", "cy")
            _finite_attributes(element, "rx", "ry", positive=True)
        elif tag == "line":
            _finite_attributes(element, "x1", "y1", "x2", "y2")
        elif tag == "polyline" and len(_points(element)) < 2:
            raise ExportError("SVG polyline needs at least two points.", stage="export")
        elif tag == "polygon" and len(_points(element)) < 3:
            raise ExportError("SVG polygon needs at least three points.", stage="export")
        elif tag == "text":
            _text_box(element)
        elif tag == "image":
            _finite_attributes(element, "x", "y")
            _finite_attributes(element, "width", "height", positive=True)
            href = str(element.get("href") or "").strip()
            if not is_safe_image_href(href):
                raise ExportError("SVG image href is outside the approved request image namespace.", stage="export")
            try:
                image_path = resolve_safe_image_href(svg_path, href)
                _image_dimensions(image_path)
            except (Image.DecompressionBombError, OSError, ValueError, UnidentifiedImageError) as exc:
                raise ExportError(f"Cannot use SVG image asset '{href}': {exc}", stage="export") from exc
            preserve = str(element.get("preserveAspectRatio") or "xMidYMid meet").strip()
            if preserve not in {"xMidYMid meet", "xMidYMid slice"}:
                raise ExportError(f"Unsupported SVG image fit '{preserve}'.", stage="export")


def _add_children(slide: Any, parent: ET.Element, stats: _MutableStats, svg_path: Path) -> None:
    for element in parent:
        tag = _local_name(element.tag)
        if tag not in SVG_ALLOWED_TAGS:
            raise ExportError(f"Unsupported SVG element <{tag}> reached native export.", stage="export")
        if tag == "g":
            _add_children(slide, element, stats, svg_path)
        elif tag == "rect":
            _add_rect(slide, element, stats)
        elif tag in {"circle", "ellipse"}:
            _add_ellipse(slide, element, stats)
        elif tag == "line":
            _add_line(slide, element, stats)
        elif tag == "polyline":
            _add_polyline(slide, element, stats)
        elif tag == "polygon":
            _add_polygon(slide, element, stats)
        elif tag == "text":
            _add_text(slide, element, stats)
        elif tag == "image":
            _add_image(slide, element, stats, svg_path)
        elif tag == "tspan":
            continue


def _add_rect(slide: Any, element: ET.Element, stats: _MutableStats) -> None:
    x, y = _float(element, "x"), _float(element, "y")
    width, height = _positive(element, "width"), _positive(element, "height")
    rounded = _float(element, "rx") > 0
    shape_type = MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE if rounded else MSO_AUTO_SHAPE_TYPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, _emu(x), _emu(y), _emu(width), _emu(height))
    _apply_shape_style(shape, element)
    _name_shape(shape, element)
    stats.shape_count += 1


def _add_ellipse(slide: Any, element: ET.Element, stats: _MutableStats) -> None:
    if _local_name(element.tag) == "circle":
        radius = _positive(element, "r")
        x, y, width, height = _float(element, "cx") - radius, _float(element, "cy") - radius, radius * 2, radius * 2
    else:
        rx, ry = _positive(element, "rx"), _positive(element, "ry")
        x, y, width, height = _float(element, "cx") - rx, _float(element, "cy") - ry, rx * 2, ry * 2
    shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, _emu(x), _emu(y), _emu(width), _emu(height))
    _apply_shape_style(shape, element)
    _name_shape(shape, element)
    stats.shape_count += 1


def _add_line(slide: Any, element: ET.Element, stats: _MutableStats) -> None:
    shape = slide.shapes.add_connector(
        MSO_CONNECTOR_TYPE.STRAIGHT,
        _emu(_float(element, "x1")),
        _emu(_float(element, "y1")),
        _emu(_float(element, "x2")),
        _emu(_float(element, "y2")),
    )
    _apply_line_style(shape, element)
    _name_shape(shape, element)
    stats.shape_count += 1


def _add_polyline(slide: Any, element: ET.Element, stats: _MutableStats) -> None:
    points = _points(element)
    if len(points) < 2:
        raise ExportError("SVG polyline needs at least two points.", stage="export")
    for index, (start, end) in enumerate(zip(points, points[1:], strict=False), start=1):
        shape = slide.shapes.add_connector(
            MSO_CONNECTOR_TYPE.STRAIGHT,
            _emu(start[0]),
            _emu(start[1]),
            _emu(end[0]),
            _emu(end[1]),
        )
        _apply_line_style(shape, element)
        _name_shape(shape, element, suffix=f"-{index}")
        stats.shape_count += 1


def _add_polygon(slide: Any, element: ET.Element, stats: _MutableStats) -> None:
    points = _points(element)
    if len(points) < 3:
        raise ExportError("SVG polygon needs at least three points.", stage="export")
    builder = slide.shapes.build_freeform(points[0][0], points[0][1], scale=EMU_PER_PX)
    builder.add_line_segments(points[1:], close=True)
    shape = builder.convert_to_shape(Emu(0), Emu(0))
    _apply_shape_style(shape, element)
    _name_shape(shape, element)
    stats.shape_count += 1


def _add_text(slide: Any, element: ET.Element, stats: _MutableStats) -> None:
    x, y, width, height = _text_box(element)
    lines = ["".join(tspan.itertext()) for tspan in element if _local_name(tspan.tag) == "tspan"]
    source = str(element.get("data-value") or "").strip()
    text = "\v".join(lines) if lines else source or "".join(element.itertext()).strip()
    if not text:
        return
    box = slide.shapes.add_textbox(_emu(x), _emu(y), _emu(width), _emu(height))
    _name_shape(box, element)
    frame = box.text_frame
    frame.clear()
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.word_wrap = False
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = _vertical_anchor(str(element.get("data-valign") or "top"))
    paragraph = frame.paragraphs[0]
    paragraph.alignment = _alignment(str(element.get("data-align") or "left"))
    font_size_px = _number(element.get("data-font-size") or element.get("font-size"), 18.0)
    paragraph.line_spacing = Pt(font_size_px * 0.75 * 1.25)
    run = paragraph.add_run()
    run.text = text
    font_name = str(element.get("data-font-family") or element.get("font-family") or "Noto Sans SC").split(",", 1)[0].strip(" '\"")
    run.font.name = font_name
    run.font.size = Pt(round(font_size_px * 0.75 * 2) / 2)
    run.font.bold = str(element.get("data-bold") or "").lower() == "true" or _number(element.get("font-weight"), 400) >= 600
    color = _rgb(str(element.get("fill") or "#14213D"))
    if color is not None:
        run.font.color.rgb = color
    _set_east_asian_font(run, font_name)
    stats.shape_count += 1
    if element.get("data-editable") == "true" or element.get("data-text") == "true":
        stats.editable_text_boxes += 1
    stats.fonts.add(font_name)


def _add_image(slide: Any, element: ET.Element, stats: _MutableStats, svg_path: Path) -> None:
    href = str(element.get("href") or "").strip()
    try:
        image_path = resolve_safe_image_href(svg_path, href)
        image_width, image_height = _image_dimensions(image_path)
    except (Image.DecompressionBombError, OSError, ValueError, UnidentifiedImageError) as exc:
        raise ExportError(f"Cannot add SVG image asset '{href}': {exc}", stage="export") from exc

    x, y = _float(element, "x"), _float(element, "y")
    width, height = _positive(element, "width"), _positive(element, "height")
    fit = str(element.get("preserveAspectRatio") or "xMidYMid meet").strip().rsplit(" ", 1)[-1]
    image_ratio = image_width / image_height
    box_ratio = width / height

    if fit == "meet":
        if image_ratio >= box_ratio:
            picture_width = width
            picture_height = width / image_ratio
            picture_x = x
            picture_y = y + (height - picture_height) / 2
        else:
            picture_height = height
            picture_width = height * image_ratio
            picture_x = x + (width - picture_width) / 2
            picture_y = y
        picture = slide.shapes.add_picture(
            str(image_path),
            _emu(picture_x),
            _emu(picture_y),
            _emu(picture_width),
            _emu(picture_height),
        )
    else:
        picture = slide.shapes.add_picture(str(image_path), _emu(x), _emu(y), _emu(width), _emu(height))
        if image_ratio > box_ratio:
            visible_fraction = box_ratio / image_ratio
            picture.crop_left = picture.crop_right = (1 - visible_fraction) / 2
        elif image_ratio < box_ratio:
            visible_fraction = image_ratio / box_ratio
            picture.crop_top = picture.crop_bottom = (1 - visible_fraction) / 2
    _name_shape(picture, element)
    stats.shape_count += 1


def _image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        if str(image.format or "").upper() not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("image payload format is not PNG, JPEG, or WebP")
        width, height = image.size
        if width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE or width * height > MAX_IMAGE_PIXELS:
            raise ValueError("image dimensions exceed the safe asset limit")
        image.verify()
    if width <= 0 or height <= 0:
        raise ValueError("image has invalid dimensions")
    return width, height


def _apply_shape_style(shape: Any, element: ET.Element) -> None:
    fill = str(element.get("fill") or "none")
    fill_rgb = _rgb(fill)
    if fill_rgb is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_rgb
    stroke = str(element.get("stroke") or "none")
    stroke_rgb = _rgb(stroke)
    if stroke_rgb is None or _number(element.get("stroke-width"), 0) <= 0:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = stroke_rgb
        shape.line.width = Pt(_number(element.get("stroke-width"), 1) * 0.75)


def _apply_line_style(shape: Any, element: ET.Element) -> None:
    stroke = _rgb(str(element.get("stroke") or "#14213D"))
    if stroke is None:
        shape.line.fill.background()
        return
    shape.line.color.rgb = stroke
    shape.line.width = Pt(max(0.5, _number(element.get("stroke-width"), 1) * 0.75))


def _text_box(element: ET.Element) -> tuple[float, float, float, float]:
    raw = str(element.get("data-box") or "")
    try:
        x, y, width, height = [float(item) for item in raw.split(",")]
    except (TypeError, ValueError) as exc:
        raise ExportError(f"SVG text has invalid data-box '{raw}'.", stage="export") from exc
    if not all(math.isfinite(value) for value in (x, y, width, height)) or width <= 0 or height <= 0:
        raise ExportError(f"SVG text has non-positive data-box '{raw}'.", stage="export")
    return x, y, width, height


def _points(element: ET.Element) -> list[tuple[float, float]]:
    raw = str(element.get("points") or "").replace(",", " ").split()
    try:
        values = [float(item) for item in raw]
    except ValueError as exc:
        raise ExportError("SVG points contain a non-numeric value.", stage="export") from exc
    if len(values) % 2:
        raise ExportError("SVG points must contain x/y pairs.", stage="export")
    if not all(math.isfinite(value) for value in values):
        raise ExportError("SVG points must contain finite values.", stage="export")
    return list(zip(values[0::2], values[1::2], strict=False))


def _finite_attributes(element: ET.Element, *names: str, positive: bool = False) -> None:
    for name in names:
        raw = element.get(name)
        try:
            value = float(raw or 0)
        except (TypeError, ValueError) as exc:
            raise ExportError(f"SVG <{_local_name(element.tag)}> has invalid {name}={raw!r}.", stage="export") from exc
        if not math.isfinite(value) or (positive and value <= 0):
            raise ExportError(f"SVG <{_local_name(element.tag)}> has invalid {name}={raw!r}.", stage="export")


def _float(element: ET.Element, name: str) -> float:
    return _number(element.get(name), 0.0)


def _positive(element: ET.Element, name: str) -> float:
    value = _float(element, name)
    if value <= 0:
        raise ExportError(f"SVG <{_local_name(element.tag)}> has invalid {name}={value:g}.", stage="export")
    return value


def _number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _rgb(value: str) -> RGBColor | None:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return RGBColor(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def _alignment(value: str) -> PP_ALIGN:
    if value == "center":
        return PP_ALIGN.CENTER
    if value == "right":
        return PP_ALIGN.RIGHT
    return PP_ALIGN.LEFT


def _vertical_anchor(value: str) -> MSO_VERTICAL_ANCHOR:
    if value == "middle":
        return MSO_VERTICAL_ANCHOR.MIDDLE
    if value == "bottom":
        return MSO_VERTICAL_ANCHOR.BOTTOM
    return MSO_VERTICAL_ANCHOR.TOP


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


def _name_shape(shape: Any, element: ET.Element, *, suffix: str = "") -> None:
    name = (str(element.get("id") or "SVG shape") + suffix)[:255]
    try:
        shape._element.nvSpPr.cNvPr.set("name", name)  # pylint: disable=protected-access
    except (AttributeError, TypeError):
        try:
            shape._element.nvCxnSpPr.cNvPr.set("name", name)  # pylint: disable=protected-access
        except (AttributeError, TypeError):
            try:
                shape._element.nvPicPr.cNvPr.set("name", name)  # pylint: disable=protected-access
            except (AttributeError, TypeError):
                return


def _emu(value: float) -> Emu:
    return Emu(round(float(value) * EMU_PER_PX))


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


__all__ = ["SVGNativeStats", "add_svg_as_native_shapes"]
