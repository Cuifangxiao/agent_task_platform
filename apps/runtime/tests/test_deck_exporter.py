import json
import zipfile

from PIL import Image
from pptx import Presentation

from core.deck.export import DeckExporter
import core.deck.pipeline.export as export_pipeline
from core.deck.pipeline.export import _attach_source_notes
from core.deck.spec import DeckSpec, SlideSpec, default_theme


class UnavailableBrowserSession:
    def extract_hidden_text_background(self, _html, _background_path):
        raise RuntimeError("browser intentionally unavailable in this test")


def test_exporter_falls_back_to_full_slide_image_when_browser_unavailable(tmp_path):
    request_id = "11111111-1111-1111-1111-111111111111"
    request_dir = tmp_path / request_id
    slides_dir = request_dir / "slides"
    slides_dir.mkdir(parents=True)
    Image.new("RGB", (2560, 1440), (247, 243, 234)).save(slides_dir / "1.png")
    (slides_dir / "1.html").write_text('<div class="slide"><h1 data-text>Hello</h1></div>', encoding="utf-8")

    deck = DeckSpec(
        title="Test",
        narrative="Narrative",
        audience="Team",
        goal="Explain",
        language="en",
        theme=default_theme(),
        slides=[
            SlideSpec(
                index=1,
                role="hook",
                component="cover",
                title="Hello",
                content={"subtitle": "World"},
                notes="Speaker note",
            )
        ],
    )

    result = DeckExporter(tmp_path, UnavailableBrowserSession()).export(request_id, deck)

    assert result.path.exists()
    assert result.path.name == "deck.pptx"
    assert (request_dir / "spec.json").exists()
    exported = Presentation(str(result.path))
    assert len(exported.slides) == 1
    assert result.warnings and "full-slide image" in result.warnings[0]


def test_exporter_preserves_editable_text_structure_fonts_bullets_and_background(tmp_path):
    request_id = "22222222-2222-4222-8222-222222222222"
    request_dir = tmp_path / request_id
    slides_dir = request_dir / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "1.html").write_text('<section class="slide"><h1 data-text>Title</h1><li data-text>Bullet</li></section>', encoding="utf-8")

    class FakeBrowserSession:
        def extract_hidden_text_background(self, _html, bg_path):
            Image.new("RGB", (2560, 1440), (12, 34, 56)).save(bg_path)
            return [
                {"text": "Title", "x": 80, "y": 60, "width": 700, "height": 60, "fontSize": 40, "fontWeight": "700", "color": "rgb(20, 33, 61)", "lineHeight": 48, "textAlign": "left", "fontFamily": "Arial", "fontAvailable": True, "isListItem": False},
                {"text": "Bullet", "x": 100, "y": 180, "width": 600, "height": 42, "fontSize": 20, "fontWeight": "400", "color": "rgb(88, 103, 122)", "lineHeight": 29, "textAlign": "left", "fontFamily": "Arial", "fontAvailable": True, "isListItem": True},
            ]

    deck = DeckSpec(
        title="Test",
        narrative="",
        audience="",
        goal="",
        language="en",
        theme=default_theme(),
        slides=[SlideSpec(index=1, role="evidence", component="content-basic", title="Title", content={"bullets": ["Bullet"]})],
    )
    result = DeckExporter(tmp_path, FakeBrowserSession()).export(request_id, deck)
    exported = Presentation(str(result.path))
    text_shapes = [shape for shape in exported.slides[0].shapes if getattr(shape, "has_text_frame", False) and shape.text.strip()]
    assert [shape.text for shape in text_shapes] == ["Title", "Bullet"]
    assert all(run.font.name == "Arial" for shape in text_shapes for paragraph in shape.text_frame.paragraphs for run in paragraph.runs)
    assert "a:buChar" in text_shapes[1].text_frame.paragraphs[0]._p.xml
    assert result.warnings == []
    assert result.manifest["structure"]["editable_text_boxes"] == [2]
    assert json.loads((request_dir / "reports" / "export-manifest.json").read_text(encoding="utf-8"))["fonts"] == ["Arial"]
    with zipfile.ZipFile(result.path) as archive:
        embedded = archive.read("ppt/media/image1.png")
    assert embedded == (request_dir / "previews" / "page-001_bg.png").read_bytes()


def test_source_provenance_is_written_into_speaker_notes():
    slide = SlideSpec(index=1, role="evidence", component="content-basic", title="Revenue", content={"body": "Revenue was 40%"}, sources=["fact#1"])
    deck = DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=default_theme(), slides=[slide])
    _attach_source_notes(
        deck,
        {"facts": [{"id": "fact#1", "statement": "Revenue was 40%", "source_file": "report.pdf", "page": 3, "section": "paragraph-2", "source_excerpt": "Revenue was 40% after the launch."}]},
    )
    assert "report.pdf p.3 paragraph-2" in slide.notes
    assert "Revenue was 40% after the launch." in slide.notes


def test_must_fix_and_degraded_export_are_reported_as_needs_review(tmp_path, monkeypatch):
    request_id = "33333333-3333-4333-8333-333333333333"
    slides_dir = tmp_path / request_id / "slides"
    slides_dir.mkdir(parents=True)
    Image.new("RGB", (2560, 1440), (247, 243, 234)).save(slides_dir / "1.png")
    (slides_dir / "1.html").write_text('<section class="slide"><h1 data-text>Title</h1></section>', encoding="utf-8")
    deck = DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=default_theme(), slides=[SlideSpec(index=1, role="evidence", component="content-basic", title="Title", content={"body": "Body"})])
    emitted = []
    monkeypatch.setattr(export_pipeline, "get_stream_writer", lambda: emitted.append)
    result = export_pipeline.export_deck(
        {
            "request_id": request_id,
            "deck_spec": deck.to_dict(),
            "validation_issues": [{"page": 1, "severity": "must_fix", "kind": "overflow", "description": "Overflow"}],
            "source_chunks": [],
            "brief": {"facts": []},
        },
        DeckExporter(tmp_path, UnavailableBrowserSession()),
    )
    artifact = next(event for event in emitted if event["event_type"] == "artifact")
    assert result["quality_status"] == "needs_review"
    assert result["export_degraded"] is True
    assert artifact["payload"]["needs_review"] is True
    assert artifact["payload"]["degraded"] is True
    manifest_path = tmp_path / request_id / "reports" / "generation-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "deck-generation-manifest"
    assert manifest["quality"]["status"] == "needs_review"
    assert manifest["pages"][0]["title"] == "Title"
    assert result["generation_manifest"] == manifest
