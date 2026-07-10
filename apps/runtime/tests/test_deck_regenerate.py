import json
from types import SimpleNamespace

from PIL import Image

from core.deck.rendering.browser import BrowserRenderer
from core.deck.contract import ResumeDeckInput
from core.deck.export import DeckExporter
from core.deck.pipeline import regenerate
from core.deck.spec import DeckSpec, SlideSpec, default_theme


class FakeWriterLLM:
    def __init__(self):
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "index": 1,
                    "role": "evidence",
                    "component": "content-basic",
                    "title": "新版页面更聚焦",
                    "content": {"body": "保留结论，减少说明。", "bullets": ["只留最关键证据"]},
                    "notes": "Explain the shorter version.",
                    "sources": ["fact#1"],
                },
                ensure_ascii=False,
            )
        )


def test_regenerate_slide_uses_writer_output_without_copying_feedback(tmp_path, monkeypatch):
    request_id = "11111111-1111-1111-1111-111111111111"
    request_dir = tmp_path / request_id
    request_dir.mkdir(parents=True)
    deck = DeckSpec(
        title="Deck",
        narrative="Narrative",
        audience="Team",
        goal="Explain",
        language="zh",
        theme=default_theme(),
        slides=[
            SlideSpec(
                index=1,
                role="evidence",
                component="content-basic",
                title="旧页面",
                content={"body": "旧正文", "bullets": ["旧要点"]},
                sources=["fact#1"],
            )
        ],
    )
    (request_dir / "spec.json").write_text(json.dumps(deck.to_dict(), ensure_ascii=False), encoding="utf-8")

    fake_writer = FakeWriterLLM()
    monkeypatch.setenv("DECK_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(regenerate, "build_deck_llms", lambda inp: {"writer": fake_writer})

    def fake_capture(html, png_path):
        del html
        Image.new("RGB", (2560, 1440), (10, 20, 30)).save(png_path)

    monkeypatch.setattr(BrowserRenderer, "_capture_with_playwright", staticmethod(fake_capture))
    monkeypatch.setattr(
        DeckExporter,
        "_capture_hidden_text_background",
        staticmethod(lambda html_path, req_dir, index: (req_dir / "slides" / f"{index}.png", [])),
    )

    outputs = list(
        regenerate.regenerate_slide_from_artifacts(
            request_id,
            ResumeDeckInput(
                user_id=1,
                username="tester",
                request_id=request_id,
                action="regenerate_slide",
                payload_json=json.dumps({"index": 1, "feedback": "文字太多"}, ensure_ascii=False),
            ),
        )
    )

    html = (request_dir / "slides" / "1.html").read_text(encoding="utf-8")
    spec = json.loads((request_dir / "spec.json").read_text(encoding="utf-8"))
    assert "文字太多" in fake_writer.messages[0][1][1]
    assert "文字太多" not in html
    assert spec["slides"][0]["title"] == "新版页面更聚焦"
    assert (request_dir / "slides" / "1_v2.html").exists()
    assert outputs[-1].event_type == "done"
    assert (request_dir / "deck.pptx").exists()


def test_regenerate_slide_without_writer_model_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DECK_ARTIFACTS_DIR", str(tmp_path))

    outputs = list(
        regenerate.regenerate_slide_from_artifacts(
            "missing-model",
            ResumeDeckInput(
                user_id=1,
                username="tester",
                request_id="missing-model",
                action="regenerate_slide",
                payload_json=json.dumps({"index": 1, "feedback": "文字太多"}, ensure_ascii=False),
            ),
        )
    )

    assert outputs[0].event_type == "error"
    assert outputs[0].done is True
