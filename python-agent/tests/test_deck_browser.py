import sys
import types

from PIL import Image

from core.deck.rendering.browser import BrowserRenderer, BrowserSession
from core.deck.spec import SlideSpec, default_theme


def test_render_slide_writes_authoritative_and_history_files(tmp_path, monkeypatch):
    request_id = "req"
    slide = SlideSpec(
        index=1,
        role="evidence",
        component="content-basic",
        title="Updated slide",
        content={"body": "Updated body", "bullets": ["One"]},
    )

    def fake_capture(html, png_path):
        del html
        Image.new("RGB", (2560, 1440), (1, 2, 3)).save(png_path)

    monkeypatch.setattr(BrowserRenderer, "_capture_with_playwright", staticmethod(fake_capture))

    artifact = BrowserRenderer(tmp_path).render_slide(
        request_id,
        slide,
        '<section class="slide"><h1 data-text>Updated slide</h1></section>',
        version=2,
        theme=default_theme(),
    )

    slides_dir = tmp_path / request_id / "slides"
    assert artifact.html_rel == "slides/1.html"
    assert artifact.png_rel == "slides/1.png"
    assert (slides_dir / "1.html").exists()
    assert (slides_dir / "1.png").exists()
    assert (slides_dir / "1_v2.html").exists()
    assert (slides_dir / "1_v2.png").exists()
    assert (slides_dir / "1.html").read_text(encoding="utf-8") == (slides_dir / "1_v2.html").read_text(encoding="utf-8")
    assert (slides_dir / "1.png").read_bytes() == (slides_dir / "1_v2.png").read_bytes()


def test_browser_session_reuses_one_browser_and_closes_idempotently(tmp_path, monkeypatch):
    calls = {"start": 0, "launch": 0, "new_page": 0, "page_close": 0, "browser_close": 0, "stop": 0}

    class FakePage:
        def set_content(self, html, wait_until):
            assert html
            assert wait_until == "load"

        def screenshot(self, path, full_page):
            assert full_page is False
            Image.new("RGB", (10, 10), (1, 2, 3)).save(path)

        def evaluate(self, script):
            return [] if "querySelectorAll" in script else None

        def close(self):
            calls["page_close"] += 1

    class FakeBrowser:
        def new_page(self, **kwargs):
            calls["new_page"] += 1
            assert kwargs["viewport"]["width"] == 1280
            return FakePage()

        def close(self):
            calls["browser_close"] += 1

    class FakeChromium:
        def launch(self, headless):
            calls["launch"] += 1
            assert headless is True
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            calls["stop"] += 1

    class FakeSyncPlaywright:
        def start(self):
            calls["start"] += 1
            return FakePlaywright()

    playwright_module = types.ModuleType("playwright")
    sync_api_module = types.ModuleType("playwright.sync_api")
    sync_api_module.sync_playwright = lambda: FakeSyncPlaywright()
    playwright_module.sync_api = sync_api_module
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

    session = BrowserSession()
    session.capture("<html>one</html>", tmp_path / "one.png")
    session.capture("<html>two</html>", tmp_path / "two.png")
    assert session.extract_text_layer("<html>three</html>") == []
    session.close()
    session.close()

    assert calls == {"start": 1, "launch": 1, "new_page": 1, "page_close": 1, "browser_close": 1, "stop": 1}
