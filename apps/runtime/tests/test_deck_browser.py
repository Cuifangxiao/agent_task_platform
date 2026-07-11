import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

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
    assert artifact.html_rel == "slides/1_v2.html"
    assert artifact.png_rel == "slides/1_v2.png"
    assert (slides_dir / "1.html").exists()
    assert (slides_dir / "1.png").exists()
    assert (slides_dir / "1_v2.html").exists()
    assert (slides_dir / "1_v2.png").exists()
    assert (slides_dir / "1.html").read_text(encoding="utf-8") == (slides_dir / "1_v2.html").read_text(encoding="utf-8")
    assert (slides_dir / "1.png").read_bytes() == (slides_dir / "1_v2.png").read_bytes()


def test_browser_session_reuses_one_browser_and_closes_idempotently(tmp_path, monkeypatch):
    calls = {"start": 0, "launch": 0, "new_page": 0, "page_close": 0, "browser_close": 0, "stop": 0}
    browser_thread_ids = set()

    def record_browser_thread():
        browser_thread_ids.add(threading.get_ident())

    class FakePage:
        def set_content(self, html, wait_until):
            record_browser_thread()
            assert html
            assert wait_until == "load"

        def screenshot(self, path, full_page):
            record_browser_thread()
            assert full_page is False
            Image.new("RGB", (10, 10), (1, 2, 3)).save(path)

        def evaluate(self, script):
            record_browser_thread()
            return [] if "querySelectorAll" in script else None

        def close(self):
            record_browser_thread()
            calls["page_close"] += 1

    class FakeBrowser:
        def new_page(self, **kwargs):
            record_browser_thread()
            calls["new_page"] += 1
            assert kwargs["viewport"]["width"] == 1280
            return FakePage()

        def close(self):
            record_browser_thread()
            calls["browser_close"] += 1

    class FakeChromium:
        def launch(self, headless):
            record_browser_thread()
            calls["launch"] += 1
            assert headless is True
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            record_browser_thread()
            calls["stop"] += 1

    class FakeSyncPlaywright:
        def start(self):
            record_browser_thread()
            calls["start"] += 1
            return FakePlaywright()

    playwright_module = types.ModuleType("playwright")
    sync_api_module = types.ModuleType("playwright.sync_api")
    sync_api_module.sync_playwright = lambda: FakeSyncPlaywright()
    playwright_module.sync_api = sync_api_module
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

    session = BrowserSession()
    caller_thread_id = threading.get_ident()
    with ThreadPoolExecutor(max_workers=2) as callers:
        first = callers.submit(session.capture, "<html>one</html>", tmp_path / "one.png")
        second = callers.submit(session.capture, "<html>two</html>", tmp_path / "two.png")
        first.result()
        second.result()
        assert callers.submit(session.extract_text_layer, "<html>three</html>").result() == []
    session.close()
    session.close()

    assert calls == {"start": 1, "launch": 1, "new_page": 1, "page_close": 1, "browser_close": 1, "stop": 1}
    assert len(browser_thread_ids) == 1
    assert caller_thread_id not in browser_thread_ids
