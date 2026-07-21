import hashlib
import json

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from core.deck.errors import ExportError
from core.deck.export.image_pptx import ImagePptxExporter


def _png(path, color, *, size=(1600, 900), mode="RGB"):
    image = Image.new(mode, size, color)
    image.save(path, format="PNG")


def test_image_exporter_sorts_pages_and_places_one_full_bleed_picture(tmp_path):
    exporter = ImagePptxExporter(tmp_path)
    project = exporter.project_paths("deck-1")
    first = project.preview_png(1)
    second = project.preview_png(2)
    _png(first, "#112233")
    _png(second, "#445566")
    completed = {
        "s02": {"slide_id": "s02", "index": 2, "preview_path": project.relative(second)},
        "s01": {"slide_id": "s01", "index": 1, "preview_path": project.relative(first)},
    }

    result = exporter.export("deck-1", completed)
    manifest_on_disk = json.loads(project.export_manifest.read_text(encoding="utf-8"))
    presentation = Presentation(str(result.path))

    assert result.path == project.deck_pptx.resolve()
    assert result.manifest == manifest_on_disk
    assert result.manifest["schema"] == "image-pptx-export.v1"
    assert result.manifest["image_based"] is True
    assert result.manifest["editable"] is False
    assert [item["slide_id"] for item in result.manifest["slides"]] == ["s01", "s02"]
    assert result.manifest["slides"][0]["preview_sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert len(presentation.slides) == 2
    for slide in presentation.slides:
        assert len(slide.shapes) == 1
        picture = slide.shapes[0]
        assert picture.shape_type == MSO_SHAPE_TYPE.PICTURE
        assert (picture.left, picture.top) == (0, 0)
        assert picture.width == presentation.slide_width
        assert picture.height == presentation.slide_height


def test_image_exporter_manifest_is_deterministic_for_the_same_previews(tmp_path):
    exporter = ImagePptxExporter(tmp_path)
    project = exporter.project_paths("deck-2")
    preview = project.preview_png(1)
    _png(preview, "white")
    completed = {"s01": {"slide_id": "s01", "index": 1, "preview_path": project.relative(preview)}}

    first = exporter.export("deck-2", completed).manifest
    second = exporter.export("deck-2", completed).manifest

    assert first == second


@pytest.mark.parametrize(
    ("request_id", "preview_path", "size", "mode", "color", "message"),
    [
        ("wrong-ratio", "previews/page-001.png", (1000, 1000), "RGB", "white", "exact 16:9"),
        ("transparent", "previews/page-001.png", (1600, 900), "RGBA", (255, 255, 255, 0), "transparency"),
    ],
)
def test_image_exporter_rejects_invalid_preview_contract(
    tmp_path, request_id, preview_path, size, mode, color, message
):
    exporter = ImagePptxExporter(tmp_path)
    project = exporter.project_paths(request_id)
    preview = project.root / preview_path
    preview.parent.mkdir(parents=True, exist_ok=True)
    _png(preview, color, size=size, mode=mode)

    with pytest.raises(ExportError, match=message):
        exporter.export(
            request_id,
            {"s01": {"slide_id": "s01", "index": 1, "preview_path": preview_path}},
        )


def test_image_exporter_rejects_path_escape_and_nonconsecutive_indexes(tmp_path):
    exporter = ImagePptxExporter(tmp_path)

    with pytest.raises(ExportError, match="inside the task directory"):
        exporter.export(
            "path-escape",
            {"s01": {"slide_id": "s01", "index": 1, "preview_path": "../outside.png"}},
        )

    project = exporter.project_paths("index-gap")
    preview = project.preview_png(2)
    _png(preview, "white")
    with pytest.raises(ExportError, match="consecutive"):
        exporter.export(
            "index-gap",
            {"s02": {"slide_id": "s02", "index": 2, "preview_path": project.relative(preview)}},
        )


def test_image_exporter_rejects_stale_preview_hash(tmp_path):
    exporter = ImagePptxExporter(tmp_path)
    project = exporter.project_paths("stale-preview")
    preview = project.preview_png(1)
    _png(preview, "white")

    with pytest.raises(ExportError, match="changed after capture"):
        exporter.export(
            "stale-preview",
            {
                "s01": {
                    "slide_id": "s01",
                    "index": 1,
                    "preview_path": project.relative(preview),
                    "preview_hash": "0" * 64,
                }
            },
        )


def test_image_exporter_rejects_palette_png_transparency(tmp_path):
    exporter = ImagePptxExporter(tmp_path)
    project = exporter.project_paths("palette-alpha")
    preview = project.preview_png(1)
    image = Image.new("P", (1600, 900), 0)
    image.info["transparency"] = 0
    image.save(preview, format="PNG", transparency=0)

    with pytest.raises(ExportError, match="transparency"):
        exporter.export(
            "palette-alpha",
            {"s01": {"slide_id": "s01", "index": 1, "preview_path": project.relative(preview)}},
        )
