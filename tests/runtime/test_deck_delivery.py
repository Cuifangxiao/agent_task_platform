import json

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

import core.deck.pipeline.delivery as nodes
from core.deck.domain.contracts import EventType, ResourceType
from core.deck.export.image_pptx import ImagePptxExporter
from core.deck.workspace.paths import DeckProjectPaths


def test_delivery_assembles_image_only_pptx_and_reports(tmp_path, monkeypatch):
    request_id = "html-delivery"
    project = DeckProjectPaths.for_request(tmp_path, request_id).ensure()
    Image.new("RGB", (3200, 1800), (246, 244, 238)).save(project.preview_png(1))
    project.slide_diagnostics(1).write_text(
        json.dumps(
            {
                "slide_id": "s01",
                "diagnostics": [
                    {
                        "code": "near_edge",
                        "category": "content",
                        "severity": "warning",
                        "message": "Content is close to the safe area.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = {
        "s01": {
            "slide_id": "s01",
            "index": 1,
            "title": "One image, one slide",
            "layout_family": "cover_minimal",
            "layout_variant": "default",
            "composition_summary": "Minimal cover",
            "html_path": "previews/page-001.html",
            "preview_path": "previews/page-001.png",
            "diagnostics_path": "diagnostics/page-001.json",
            "content_hash": "a" * 64,
            "html_hash": "b" * 64,
            "artifact_hash": "c" * 64,
            "renderer_version": "chromium-html.v1",
            "prompt_version": "html-deck.v2",
            "repair_attempts": 1,
            "fallback_used": False,
        }
    }
    emitted = []
    monkeypatch.setattr(nodes, "get_stream_writer", lambda: emitted.append)

    result = nodes.assemble_image_pptx(
        {
            "request_id": request_id,
            "project_dir": str(project.root),
            "deck_title": "HTML deck",
            "deck_plan": {"deck_title": "HTML deck", "core_message": "Ship the result"},
            "design_spec": {"theme_id": "paper_editorial"},
            "selected_template_id": "paper_editorial",
            "completed_slides": completed,
        },
        ImagePptxExporter(tmp_path),
    )

    presentation = Presentation(result["artifact_path"])
    assert len(presentation.slides) == 1
    shapes = list(presentation.slides[0].shapes)
    assert len(shapes) == 1
    assert shapes[0].shape_type == MSO_SHAPE_TYPE.PICTURE
    assert emitted[0].type is EventType.STEP_STARTED
    assert emitted[-1].type is EventType.ARTIFACT_READY
    assert emitted[-1].resources[0].type is ResourceType.PPTX
    assert emitted[-1].resources[0].relative_path == "exports/deck.pptx"

    generation = json.loads(project.generation_manifest.read_text(encoding="utf-8"))
    assert generation["schema"] == "deck-generation-manifest.v2"
    assert generation["export"]["engine"] == "python-pptx-image-only"
    assert generation["export"]["editable"] is False
    quality = json.loads(project.quality_report.read_text(encoding="utf-8"))
    assert quality["schema"] == "deck-quality-report.v2"
    assert quality["warning_count"] == 1
    assert quality["repaired_pages"] == [{"slide_id": "s01", "attempts": 1}]

    finalized = nodes.finalize_deck({"artifact_path": result["artifact_path"]})
    assert finalized == {"status": "completed"}
    assert emitted[-1].type is EventType.STEP_COMPLETED
