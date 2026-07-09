"""Browser-backed slide rendering with a deterministic local fallback."""

from __future__ import annotations

import base64
import io
import logging
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from core.deck.spec import CANVAS_HEIGHT, CANVAS_WIDTH, SlideSpec, ThemeTokens
from core.deck.validators import ValidationIssue, validate_html_static

logger = logging.getLogger(__name__)


@dataclass
class SlideArtifact:
    index: int
    version: int
    html_path: Path
    png_path: Path
    html_rel: str
    png_rel: str
    thumb_b64: str
    issues: list[ValidationIssue]
    renderer: str


class BrowserRenderer:
    def __init__(self, output_root: Path) -> None:
        self._output_root = output_root

    def render_slide(
        self,
        request_id: str,
        slide: SlideSpec,
        html: str,
        *,
        version: int = 1,
        theme: ThemeTokens | None = None,
    ) -> SlideArtifact:
        request_dir = self._request_dir(request_id)
        slides_dir = request_dir / "slides"
        slides_dir.mkdir(parents=True, exist_ok=True)

        suffix = "" if version == 1 else f"_v{version}"
        html_path = slides_dir / f"{slide.index}{suffix}.html"
        png_path = slides_dir / f"{slide.index}{suffix}.png"
        html_path.write_text(html, encoding="utf-8")

        renderer = "playwright"
        try:
            self._capture_with_playwright(html, png_path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            renderer = "pillow-fallback"
            logger.info("slide screenshot fell back to pillow: page=%s err=%s", slide.index, exc)
            _fallback_preview_png(png_path, slide, theme)

        issues = validate_html_static(html, page=slide.index)
        thumb_b64 = _thumbnail_b64(png_path)
        return SlideArtifact(
            index=slide.index,
            version=version,
            html_path=html_path,
            png_path=png_path,
            html_rel=_relative_to_request(html_path, request_dir),
            png_rel=_relative_to_request(png_path, request_dir),
            thumb_b64=thumb_b64,
            issues=issues,
            renderer=renderer,
        )

    def render_style_sample(self, request_id: str, style_id: str, html: str, *, theme: ThemeTokens) -> dict[str, Any]:
        request_dir = self._request_dir(request_id)
        styles_dir = request_dir / "styles"
        styles_dir.mkdir(parents=True, exist_ok=True)
        html_path = styles_dir / f"{style_id}.html"
        png_path = styles_dir / f"{style_id}.png"
        html_path.write_text(html, encoding="utf-8")
        try:
            self._capture_with_playwright(html, png_path)
            renderer = "playwright"
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.info("style screenshot fell back to pillow: style=%s err=%s", style_id, exc)
            renderer = "pillow-fallback"
            _fallback_style_png(png_path, theme)
        return {
            "html": _relative_to_request(html_path, request_dir),
            "png": _relative_to_request(png_path, request_dir),
            "thumb_b64": _thumbnail_b64(png_path),
            "renderer": renderer,
        }

    def _request_dir(self, request_id: str) -> Path:
        path = self._output_root / request_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _capture_with_playwright(html: str, png_path: Path) -> None:
        from playwright.sync_api import sync_playwright  # type: ignore

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT}, device_scale_factor=2)
                page.set_content(html, wait_until="networkidle")
                page.wait_for_timeout(300)
                page.screenshot(path=str(png_path), full_page=False)
            finally:
                browser.close()


def _fallback_preview_png(path: Path, slide: SlideSpec, theme: ThemeTokens | None) -> None:
    palette = (theme.palette if theme else {}) if theme else {}
    bg = _color(palette.get("bg"), (247, 243, 234))
    surface = _color(palette.get("surface"), (255, 255, 255))
    ink = _color(palette.get("ink"), (20, 33, 61))
    muted = _color(palette.get("muted"), (88, 103, 122))
    accent = _color(palette.get("accent"), (42, 157, 143))
    accent2 = _color(palette.get("accent2"), (231, 111, 81))
    image = Image.new("RGB", (CANVAS_WIDTH * 2, CANVAS_HEIGHT * 2), bg)
    draw = ImageDraw.Draw(image)
    scale = 2

    def rect(box: tuple[int, int, int, int], fill: tuple[int, int, int], outline: tuple[int, int, int] | None = None) -> None:
        draw.rounded_rectangle(tuple(v * scale for v in box), radius=18 * scale, fill=fill, outline=outline, width=2 * scale)

    rect((56, 52, 1224, 668), bg, _blend(muted, bg, 0.35))
    if slide.component in {"cover", "end"}:
        rect((910, 116, 1188, 546), accent)
        draw.rectangle((950 * scale, 160 * scale, 1030 * scale, 520 * scale), fill=accent2)
        _draw_wrapped(draw, slide.title, (86, 152), 780, _font(48), ink, scale)
        _draw_wrapped(draw, str(slide.content.get("subtitle") or ""), (90, 338), 680, _font(24), muted, scale)
    elif slide.component == "section":
        rect((70, 136, 1210, 536), accent)
        _draw_wrapped(draw, slide.title, (140, 232), 940, _font(44), bg, scale)
        _draw_wrapped(draw, str(slide.content.get("subtitle") or ""), (144, 345), 860, _font(24), bg, scale)
    else:
        _draw_wrapped(draw, slide.title, (78, 62), 1000, _font(34), ink, scale)
        body = str(slide.content.get("body") or slide.content.get("takeaway") or slide.content.get("subtitle") or "")
        if body:
            _draw_wrapped(draw, body, (82, 128), 920, _font(22), accent, scale)
        items = _fallback_items(slide)
        y = 210
        for item in items[:6]:
            draw.ellipse((86 * scale, (y + 10) * scale, 102 * scale, (y + 26) * scale), fill=accent2)
            _draw_wrapped(draw, item, (122, y), 880, _font(23), ink, scale)
            y += 74
        rect((900, 188, 1192, 520), surface, _blend(muted, bg, 0.3))
        _draw_wrapped(draw, slide.component, (936, 230), 210, _font(22), muted, scale)
    draw.text((1188 * scale, 638 * scale), str(slide.index), fill=muted, font=_font(18))
    image.save(path)


def _fallback_style_png(path: Path, theme: ThemeTokens) -> None:
    slide = SlideSpec(
        index=1,
        role="hook",
        component="cover",
        title=theme.name,
        content={"subtitle": theme.motif, "eyebrow": "Style Option"},
    )
    _fallback_preview_png(path, slide, theme)


def _fallback_items(slide: SlideSpec) -> list[str]:
    content = slide.content
    for key in ("bullets", "items", "steps"):
        if isinstance(content.get(key), list):
            return [str(item) for item in content[key]]
    if isinstance(content.get("cards"), list):
        return [f"{item.get('value', '')} {item.get('label', '')}".strip() for item in content["cards"] if isinstance(item, dict)]
    if isinstance(content.get("left"), list) or isinstance(content.get("right"), list):
        return [str(item) for item in (content.get("left") or []) + (content.get("right") or [])]
    return [str(content.get("body") or content.get("takeaway") or slide.title)]


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    width: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    scale: int,
) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    avg = max(8, int(getattr(font, "size", 18) * 0.58))
    max_chars = max(8, width * scale // avg)
    lines = textwrap.wrap(clean, width=max_chars)[:5]
    y = xy[1] * scale
    for line in lines:
        draw.text((xy[0] * scale, y), line, fill=fill, font=font)
        y += int(getattr(font, "size", 18) * 1.34)


def _thumbnail_b64(path: Path) -> str:
    try:
        image = Image.open(path)
        image.thumbnail((360, 210))
        for quality in (72, 58, 44):
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=quality)
            data = buffer.getvalue()
            if len(data) <= 60000:
                return base64.b64encode(data).decode("ascii")
        return base64.b64encode(data).decode("ascii")
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        try:
            return ImageFont.truetype(candidate, size * 2)
        except OSError:
            continue
    return ImageFont.load_default()


def _color(value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(int(a[index] * ratio + b[index] * (1 - ratio)) for index in range(3))  # type: ignore[return-value]


def _relative_to_request(path: Path, request_dir: Path) -> str:
    return path.resolve().relative_to(request_dir.resolve()).as_posix()
