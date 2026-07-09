from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.rendering.charts import chart_is_valid, render_chart_svg
from core.deck.spec import DeckSpec, SlideSpec, default_theme


def test_render_chart_svg_supports_bar_line_and_pie():
    base = {
        "series": [
            {"label": "A", "value": 10},
            {"label": "B", "value": 20},
        ],
        "unit": "%",
    }

    for chart_type in ("bar", "line", "pie"):
        svg = render_chart_svg({**base, "type": chart_type}, title="Chart")
        assert "<svg" in svg
        assert "data-text" in svg
        assert "A" in svg
        assert "20%" in svg


def test_invalid_chart_takeaway_falls_back_to_text_layout():
    deck = DeckSpec(
        title="Deck",
        narrative="",
        audience="",
        goal="",
        language="en",
        theme=default_theme(),
        slides=[],
    )
    slide = SlideSpec(
        index=1,
        role="evidence",
        component="chart-takeaway",
        title="Revenue changed",
        content={"takeaway": "Revenue rose after launch."},
        chart={"type": "bar", "series": []},
    )

    html = HTMLSlideRenderer().render(deck, slide)

    assert not chart_is_valid(slide.chart)
    assert 'data-component="content-basic"' in html
    assert "Revenue rose after launch." in html
    assert "data-chart" not in html
