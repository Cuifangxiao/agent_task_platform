from PIL import Image

from core.deck.export import DeckExporter
from core.deck.spec import DeckSpec, SlideSpec, default_theme


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

    result = DeckExporter(tmp_path).export(request_id, deck)

    assert result.path.exists()
    assert result.path.name == "deck.pptx"
    assert (request_dir / "spec.json").exists()
