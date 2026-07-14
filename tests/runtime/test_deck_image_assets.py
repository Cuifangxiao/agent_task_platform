import io
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation

from core.deck.contract import DeckSourceFile, GenerateDeckInput
from core.deck.errors import ExportError
from core.deck.export import DeckExporter
from core.deck.export.svg_native import add_svg_as_native_shapes
from core.deck.quality.svg import validate_svg_page
from core.deck.rendering.browser import BrowserRenderer
from core.deck.sources import materialize_source_images, parse_uploaded_files_with_provenance
from core.deck.spec import DeckSpec, SlideSpec, default_theme


def _image_bytes(fmt: str = "PNG", size: tuple[int, int] = (320, 180)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (20, 80, 140)).save(buffer, format=fmt)
    return buffer.getvalue()


def _input(filename: str, content: bytes) -> GenerateDeckInput:
    return GenerateDeckInput(
        user_id=1,
        username="tester",
        user_description="Deck",
        source_files=[DeckSourceFile(filename=filename, content=content)],
    )


def test_uploaded_image_is_sanitized_and_skipped_by_text_parser(tmp_path: Path):
    inp = _input("../../Executive Photo.PNG", _image_bytes())

    assets = materialize_source_images(inp, tmp_path / "request")
    names, text, chunks = parse_uploaded_files_with_provenance(inp)

    assert names == "../../Executive Photo.PNG"
    assert text == ""
    assert chunks == []
    assert len(assets) == 1
    asset = assets[0]
    assert asset["filename"].startswith("source-01-")
    assert asset["filename"].endswith(".png")
    assert asset["svg_href"] == f"../images/{asset['filename']}"
    assert (tmp_path / "request" / asset["relative_path"]).is_file()
    assert asset["width"] == 320
    assert asset["height"] == 180


def test_uploaded_image_dimension_limit_is_enforced(tmp_path: Path):
    inp = _input("too-wide.png", _image_bytes(size=(8193, 1)))

    with pytest.raises(ValueError, match="8192"):
        materialize_source_images(inp, tmp_path / "request")


@pytest.mark.parametrize(
    "href",
    [
        "file:///secret.png",
        "https://example.com/photo.png",
        "../../secret.png",
        "../images/../secret.png",
        "../images/user controlled.png",
        "C:\\secret.png",
    ],
)
def test_svg_quality_rejects_unapproved_image_paths(href: str):
    svg = _svg_with_image(href)

    issues = validate_svg_page(svg, page=1, theme=default_theme())

    assert "svg_image_href" in {issue.kind for issue in issues}


def test_svg_quality_accepts_opaque_request_image_href():
    issues = validate_svg_page(
        _svg_with_image("../images/source-01-aabbccddeeff.webp"),
        page=1,
        theme=default_theme(),
    )

    assert "svg_image_href" not in {issue.kind for issue in issues}


def test_native_export_supports_meet_and_slice_for_safe_images(tmp_path: Path):
    slides_dir = tmp_path / "request" / "slides"
    images_dir = tmp_path / "request" / "images"
    slides_dir.mkdir(parents=True)
    images_dir.mkdir(parents=True)
    Image.new("RGB", (400, 100), (10, 20, 30)).save(images_dir / "source-01-aabbccddeeff.png")
    svg_path = slides_dir / "1.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<image id="meet" href="../images/source-01-aabbccddeeff.png" x="0" y="0" width="100" height="100" preserveAspectRatio="xMidYMid meet"/>'
        '<image id="slice" href="../images/source-01-aabbccddeeff.png" x="120" y="0" width="100" height="100" preserveAspectRatio="xMidYMid slice"/>'
        "</svg>",
        encoding="utf-8",
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    stats = add_svg_as_native_shapes(slide, svg_path)

    assert stats.shape_count == 2
    meet, slice_picture = slide.shapes
    assert round(meet.width / 9525) == 100
    assert round(meet.height / 9525) == 25
    assert round(meet.top / 9525, 1) == 37.5
    assert round(slice_picture.width / 9525) == 100
    assert round(slice_picture.height / 9525) == 100
    assert slice_picture.crop_left == pytest.approx(0.375)
    assert slice_picture.crop_right == pytest.approx(0.375)


def test_native_export_rejects_image_path_traversal_before_mutation(tmp_path: Path):
    slides_dir = tmp_path / "request" / "slides"
    slides_dir.mkdir(parents=True)
    svg_path = slides_dir / "1.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="../../secret.png" x="0" y="0" width="100" height="100"/></svg>',
        encoding="utf-8",
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    with pytest.raises(ExportError):
        add_svg_as_native_shapes(slide, svg_path)

    assert len(slide.shapes) == 0


def test_browser_refuses_unsafe_svg_before_local_file_navigation(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(BrowserRenderer, "_capture_with_playwright", lambda *_args: calls.append(True))
    slide = SlideSpec(index=1, role="hook", component="cover", title="Safe fallback", content={"subtitle": "No local read"})

    artifact = BrowserRenderer(tmp_path).render_slide(
        "request",
        slide,
        "",
        theme=default_theme(),
        svg=_svg_with_image("file:///secret.png"),
    )

    assert calls == []
    assert artifact.renderer == "pillow-fallback"
    assert "svg_image_href" in {issue.kind for issue in artifact.issues}


def test_export_does_not_reopen_html_when_native_svg_preflight_fails(tmp_path: Path):
    request_dir = tmp_path / "request"
    slides_dir = request_dir / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "1.svg").write_text(_svg_with_image("file:///secret.png"), encoding="utf-8")
    (slides_dir / "1.html").write_text("<html>must not be opened</html>", encoding="utf-8")
    Image.new("RGB", (1280, 720), (255, 255, 255)).save(slides_dir / "1.png")

    class BrowserMustNotRun:
        def extract_hidden_text_background(self, *_args):
            raise AssertionError("unsafe SVG HTML must not be reopened")

    slide = SlideSpec(index=1, role="hook", component="cover", title="Safe fallback", content={"subtitle": "No local read"})
    deck = DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=default_theme(), slides=[slide])

    result = DeckExporter(tmp_path, BrowserMustNotRun()).export("request", deck)

    assert result.manifest["slides"][0]["export_mode"] == "preview_raster"
    assert result.manifest["degraded"] is True


def _svg_with_image(href: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>'
        '<rect id="a" x="40" y="40" width="100" height="100" fill="#FFFFFF"/>'
        '<rect id="b" x="160" y="40" width="100" height="100" fill="#FFFFFF"/>'
        '<rect id="c" x="280" y="40" width="100" height="100" fill="#FFFFFF"/>'
        f'<image id="hero" href="{href}" x="400" y="40" width="400" height="300" preserveAspectRatio="xMidYMid slice"/>'
        '<text id="title" data-text="true" data-value="Title" data-box="40,400,400,50" data-font-family="Noto Sans SC" data-font-size="24" x="40" y="430" fill="#14213D">Title</text>'
        "</svg>"
    )
