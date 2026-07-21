"""Isolated Chromium renderer for sanitized slide documents."""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from core.deck.errors import RenderError
from core.deck.schemas import CANVAS_HEIGHT, CANVAS_WIDTH


logger = logging.getLogger(__name__)


class BrowserEnvironmentError(RenderError):
    """Chromium, fonts, filesystem, or renderer infrastructure is unavailable."""


class BrowserContentError(ValueError):
    """Sanitized HTML rendered but still violated deterministic page rules."""


@dataclass(frozen=True)
class RenderProfile:
    width: int = CANVAS_WIDTH
    height: int = CANVAS_HEIGHT
    device_scale_factor: float = 2.0
    timeout_ms: int = 15_000
    minimum_cover_title_px: int = 66
    minimum_page_title_px: int = 46
    minimum_body_px: int = 21
    minimum_caption_px: int = 16
    max_dom_nodes: int = 500
    max_svg_paths: int = 80
    max_shadows: int = 24

    @property
    def pixel_width(self) -> int:
        return round(self.width * self.device_scale_factor)

    @property
    def pixel_height(self) -> int:
        return round(self.height * self.device_scale_factor)

    def to_js(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "minimumCoverTitlePx": self.minimum_cover_title_px,
            "minimumPageTitlePx": self.minimum_page_title_px,
            "minimumBodyPx": self.minimum_body_px,
            "minimumCaptionPx": self.minimum_caption_px,
            "maxDomNodes": self.max_dom_nodes,
            "maxSvgPaths": self.max_svg_paths,
            "maxShadows": self.max_shadows,
        }


@dataclass(frozen=True)
class BrowserMeasurement:
    diagnostics: tuple[dict[str, Any], ...]
    blocked_requests: tuple[str, ...]
    fonts: dict[str, bool]
    dom_nodes: int
    svg_paths: int
    elapsed_ms: int


@dataclass(frozen=True)
class CapturedPNG:
    path: Path
    sha256: str
    width: int
    height: int
    opaque: bool
    elapsed_ms: int


class SecureBrowserSession:
    """Request-scoped browser owner; every render gets a fresh context/page."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deck-html-browser")
        self._state_lock = threading.Lock()
        self._closed = False

    def measure(
        self,
        document: str,
        *,
        profile: RenderProfile,
        font_families: tuple[str, ...],
    ) -> BrowserMeasurement:
        return self._dispatch(self._measure, document, profile, font_families)

    def capture(
        self,
        document: str,
        png_path: Path,
        *,
        profile: RenderProfile,
        font_families: tuple[str, ...],
    ) -> CapturedPNG:
        return self._dispatch(self._capture, document, png_path, profile, font_families)

    def describe(
        self,
        *,
        profile: RenderProfile,
        font_families: tuple[str, ...],
    ) -> dict[str, Any]:
        return self._dispatch(self._describe, profile, font_families)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            future = self._executor.submit(self._close_resources)
        try:
            future.result()
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)

    def _measure(
        self,
        document: str,
        profile: RenderProfile,
        font_families: tuple[str, ...],
    ) -> BrowserMeasurement:
        started = time.perf_counter()
        context, page, blocked = self._open_page(profile)
        try:
            self._load_stable_document(page, document, profile)
            fonts = self._font_status(page, font_families)
            result = page.evaluate(DOM_MEASUREMENT_JS, profile.to_js())
            diagnostics = list(result.get("diagnostics") or []) if isinstance(result, dict) else []
            for family, available in fonts.items():
                if not available:
                    diagnostics.append(
                        {
                            "code": "FONT_UNAVAILABLE",
                            "category": "environment",
                            "severity": "error",
                            "selector": "",
                            "message": f"Approved font is unavailable in Chromium: {family}",
                            "suggestion": "Install the configured font in the renderer image and restart the runtime.",
                            "box": {},
                            "details": {"font_family": family},
                        }
                    )
            return BrowserMeasurement(
                diagnostics=tuple(diagnostics),
                blocked_requests=tuple(blocked),
                fonts=fonts,
                dom_nodes=int(result.get("domNodes") or 0) if isinstance(result, dict) else 0,
                svg_paths=int(result.get("svgPaths") or 0) if isinstance(result, dict) else 0,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
        except BrowserContentError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise _classify_browser_exception(exc) from exc
        finally:
            context.close()

    def _capture(
        self,
        document: str,
        png_path: Path,
        profile: RenderProfile,
        font_families: tuple[str, ...],
    ) -> CapturedPNG:
        started = time.perf_counter()
        context, page, _blocked = self._open_page(profile)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = png_path.with_name(f".{png_path.stem}.{os.getpid()}.tmp.png")
        try:
            self._load_stable_document(page, document, profile)
            fonts = self._font_status(page, font_families)
            missing = [family for family, available in fonts.items() if not available]
            if missing:
                raise BrowserEnvironmentError(f"approved fonts are unavailable: {', '.join(missing)}")
            result = page.evaluate(DOM_MEASUREMENT_JS, profile.to_js())
            errors = [
                item
                for item in (result.get("diagnostics") or [])
                if isinstance(item, dict) and item.get("severity") == "error"
            ]
            if errors:
                raise BrowserContentError(f"page changed before capture: {errors[0].get('code', 'DOM_INVALID')}")
            locator = page.locator(".slide")
            locator.screenshot(
                path=str(temporary),
                animations="disabled",
                caret="hide",
                scale="device",
                timeout=profile.timeout_ms,
            )
            width, height, opaque = _validate_png(temporary, profile)
            digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
            os.replace(temporary, png_path)
            return CapturedPNG(
                path=png_path,
                sha256=digest,
                width=width,
                height=height,
                opaque=opaque,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
        except (BrowserContentError, BrowserEnvironmentError):
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise _classify_browser_exception(exc) from exc
        finally:
            temporary.unlink(missing_ok=True)
            context.close()

    def _open_page(self, profile: RenderProfile) -> tuple[Any, Any, list[str]]:
        browser = self._get_browser()
        try:
            context = browser.new_context(
                viewport={"width": profile.width, "height": profile.height},
                device_scale_factor=profile.device_scale_factor,
                accept_downloads=False,
                service_workers="block",
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise BrowserEnvironmentError(f"failed to create isolated browser context: {exc}") from exc
        blocked: list[str] = []

        def block_request(route: Any) -> None:
            blocked.append(str(route.request.url))
            route.abort("blockedbyclient")

        context.route("**/*", block_request)
        page = context.new_page()
        page.set_default_timeout(profile.timeout_ms)
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.on("popup", lambda popup: popup.close())
        return context, page, blocked

    def _describe(
        self,
        profile: RenderProfile,
        font_families: tuple[str, ...],
    ) -> dict[str, Any]:
        browser = self._get_browser()
        try:
            playwright_version = importlib.metadata.version("playwright")
        except importlib.metadata.PackageNotFoundError:
            playwright_version = "unknown"
        return {
            "playwright_version": playwright_version,
            "chromium_version": str(getattr(browser, "version", "unknown")),
            "render_profile": profile.to_js()
            | {"deviceScaleFactor": profile.device_scale_factor, "timeoutMs": profile.timeout_ms},
            "font_families": list(dict.fromkeys(family for family in font_families if family)),
            "font_contract_version": os.getenv("DECK_FONT_BUNDLE_VERSION", "system-fonts.v1"),
            "network_policy": "block-all",
            "browser_context": "fresh-per-operation",
        }

    @staticmethod
    def _load_stable_document(page: Any, document: str, profile: RenderProfile) -> None:
        page.set_content(document, wait_until="domcontentloaded", timeout=profile.timeout_ms)
        page.add_style_tag(
            content="*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"
        )
        page.evaluate(
            """async () => {
              if (document.fonts && document.fonts.ready) await document.fonts.ready;
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }"""
        )

    @staticmethod
    def _font_status(page: Any, font_families: tuple[str, ...]) -> dict[str, bool]:
        # FontFaceSet.check() can return true for a missing system family when
        # Chromium silently uses fallback.  Compare canvas metrics against a
        # guaranteed-missing family with the same generic fallback instead.
        probe = """
        family => {
          const canvas = document.createElement('canvas');
          const context = canvas.getContext('2d');
          if (!context) return false;
          const sample = 'mmmmmmmmmmWWWWWW汉字排版0123456789';
          context.font = `48px "__deck_font_guaranteed_missing__", monospace`;
          const fallbackWidth = context.measureText(sample).width;
          const escaped = String(family).replace(/["\\\\]/g, '');
          context.font = `48px "${escaped}", monospace`;
          const requestedWidth = context.measureText(sample).width;
          return Math.abs(requestedWidth - fallbackWidth) > 0.25;
        }
        """
        return {
            family: bool(page.evaluate(probe, family))
            for family in dict.fromkeys(font_families)
            if family
        }

    def _get_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright  # type: ignore

            self._playwright = sync_playwright().start()
            # Playwright defaults chromium_sandbox to false.  Enable it
            # explicitly and run the deployment image as an unprivileged user.
            self._browser = self._playwright.chromium.launch(
                headless=True,
                chromium_sandbox=True,
            )
            return self._browser
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise BrowserEnvironmentError(
                "Playwright Chromium is unavailable; install it with `python -m playwright install chromium`."
            ) from exc

    def _dispatch(self, operation: Any, *args: Any) -> Any:
        with self._state_lock:
            if self._closed:
                raise BrowserEnvironmentError("browser session is closed")
            future = self._executor.submit(operation, *args)
        return future.result()

    def _close_resources(self) -> None:
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._playwright = None
        if browser is not None:
            try:
                browser.close()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("failed to close Chromium", exc_info=True)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("failed to stop Playwright", exc_info=True)


class HTMLBrowserRenderer:
    def __init__(
        self,
        session: SecureBrowserSession | None = None,
        *,
        profile: RenderProfile | None = None,
    ) -> None:
        self.session = session or SecureBrowserSession()
        self.profile = profile or RenderProfile()
        self._owns_session = session is None

    def measure(self, document: str, *, font_families: tuple[str, ...]) -> BrowserMeasurement:
        return self.session.measure(document, profile=self.profile, font_families=font_families)

    def capture(self, document: str, png_path: Path, *, font_families: tuple[str, ...]) -> CapturedPNG:
        return self.session.capture(document, png_path, profile=self.profile, font_families=font_families)

    def metadata(self, *, font_families: tuple[str, ...]) -> dict[str, Any]:
        return self.session.describe(profile=self.profile, font_families=font_families)

    def close(self) -> None:
        if self._owns_session:
            self.session.close()


def _validate_png(path: Path, profile: RenderProfile) -> tuple[int, int, bool]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise BrowserEnvironmentError("Chromium did not produce a PNG file")
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if (width, height) != (profile.pixel_width, profile.pixel_height):
                raise BrowserEnvironmentError(
                    f"PNG size mismatch: expected {profile.pixel_width}x{profile.pixel_height}, got {width}x{height}"
                )
            if "A" in image.getbands() or "transparency" in image.info:
                alpha = image.convert("RGBA").getchannel("A")
                extrema = alpha.getextrema()
                opaque = extrema == (255, 255)
            else:
                opaque = True
            if not opaque:
                raise BrowserEnvironmentError("PNG contains transparent pixels")
            return width, height, opaque
    except BrowserEnvironmentError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise BrowserEnvironmentError(f"PNG validation failed: {exc}") from exc


def _classify_browser_exception(exc: Exception) -> Exception:
    if isinstance(exc, (BrowserContentError, BrowserEnvironmentError)):
        return exc
    name = type(exc).__name__.casefold()
    message = str(exc)
    if "timeout" in name or "timeout" in message.casefold():
        return BrowserEnvironmentError(f"Chromium render timed out: {message}")
    return BrowserEnvironmentError(f"Chromium render failed: {message}")


DOM_MEASUREMENT_JS = r"""
(profile) => {
  const diagnostics = [];
  const push = (code, selector, message, suggestion, box = {}, details = {}, category = 'content') => {
    diagnostics.push({ code, category, severity: 'error', selector, message, suggestion, box, details });
  };
  const rectObject = rect => ({ x: rect.x, y: rect.y, width: rect.width, height: rect.height });
  const selector = el => {
    const box = el.getAttribute('data-box');
    if (box) return `[data-box="${box}"]`;
    const classes = Array.from(el.classList || []).slice(0, 2).join('.');
    return `${el.tagName.toLowerCase()}${classes ? `.${classes}` : ''}`;
  };
  const directTextNodes = el => Array.from(el.childNodes || []).filter(
    node => node.nodeType === Node.TEXT_NODE && (node.textContent || '').trim()
  );
  const parseColor = value => {
    const match = String(value || '').match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)/i);
    return match ? { r: Number(match[1]), g: Number(match[2]), b: Number(match[3]), a: match[4] === undefined ? 1 : Number(match[4]) } : null;
  };
  const luminance = color => {
    const channel = value => {
      const normalized = value / 255;
      return normalized <= .04045 ? normalized / 12.92 : ((normalized + .055) / 1.055) ** 2.4;
    };
    return .2126 * channel(color.r) + .7152 * channel(color.g) + .0722 * channel(color.b);
  };
  const contrast = (a, b) => {
    const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (values[0] + .05) / (values[1] + .05);
  };
  const nearestSolidBackground = el => {
    for (let node = el; node; node = node.parentElement) {
      const color = parseColor(getComputedStyle(node).backgroundColor);
      if (color && color.a >= .95) return color;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  const effectiveOpacity = el => {
    let value = 1;
    for (let node = el; node; node = node.parentElement) value *= Number(getComputedStyle(node).opacity || 1);
    return value;
  };
  const pointInRect = (x, y, rect) => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  const directTextPaintsAt = (el, x, y) => directTextNodes(el).some(node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    return Array.from(range.getClientRects()).some(rect => pointInRect(x, y, rect));
  });
  const elementPaintsAt = (el, x, y) => {
    const style = getComputedStyle(el);
    const opacity = effectiveOpacity(el);
    const rect = el.getBoundingClientRect();
    if (
      style.display === 'none' || style.visibility === 'hidden' || opacity < .05 ||
      !pointInRect(x, y, rect)
    ) return false;
    const tag = el.tagName.toLowerCase();
    if (el.namespaceURI === 'http://www.w3.org/2000/svg' && tag !== 'svg' && tag !== 'g') return true;
    const background = parseColor(style.backgroundColor);
    if ((background && background.a * opacity >= .05) || style.backgroundImage !== 'none') return true;
    const borderPaints = (side, distance) => {
      const width = Number.parseFloat(style[`border${side}Width`]) || 0;
      const color = parseColor(style[`border${side}Color`]);
      return width > 0 && style[`border${side}Style`] !== 'none' && color && color.a * opacity >= .05 && distance <= width + .5;
    };
    if (
      borderPaints('Top', y - rect.top) || borderPaints('Right', rect.right - x) ||
      borderPaints('Bottom', rect.bottom - y) || borderPaints('Left', x - rect.left)
    ) return true;
    if (style.boxShadow && style.boxShadow !== 'none') return true;
    return directTextPaintsAt(el, x, y);
  };
  const roots = Array.from(document.querySelectorAll('.slide'));
  if (roots.length !== 1) {
    push('SLIDE_ROOT_COUNT', '.slide', `Expected one .slide root, found ${roots.length}.`, 'Return exactly one .slide root.');
  }
  const root = roots[0];
  if (!root) return { diagnostics, domNodes: document.querySelectorAll('*').length, svgPaths: 0 };
  const rootRect = root.getBoundingClientRect();
  if (Math.abs(rootRect.width - profile.width) > 1 || Math.abs(rootRect.height - profile.height) > 1) {
    push('SLIDE_SIZE_MISMATCH', '.slide', 'The slide root does not match the fixed canvas.', 'Use exactly 1600×900 CSS pixels.', rectObject(rootRect));
  }
  for (const [name, element] of [['html', document.documentElement], ['body', document.body], ['.slide', root]]) {
    if (element.scrollWidth > profile.width + 1 || element.scrollHeight > profile.height + 1) {
      push('PAGE_SCROLL_OVERFLOW', name, `${name} creates scroll overflow.`, 'Shorten content or change composition.', rectObject(element.getBoundingClientRect()), { scrollWidth: element.scrollWidth, scrollHeight: element.scrollHeight });
    }
  }
  const domNodes = document.querySelectorAll('*').length;
  const svgPaths = document.querySelectorAll('svg path').length;
  if (domNodes > profile.maxDomNodes) push('DOM_NODE_BUDGET', '.slide', `DOM node count ${domNodes} exceeds the budget.`, 'Simplify repeated wrappers and decoration.', {}, { domNodes });
  if (svgPaths > profile.maxSvgPaths) push('SVG_PATH_BUDGET', 'svg', `SVG path count ${svgPaths} exceeds the budget.`, 'Use local registry icons or simpler geometry.', {}, { svgPaths });
  if (document.querySelector('[data-icon]')) push('UNRESOLVED_ICON', '[data-icon]', 'An icon placeholder was not resolved.', 'Use a key from the local icon registry.');

  const allSemantic = Array.from(root.querySelectorAll('[data-box]'));
  if (!allSemantic.length) {
    push('SEMANTIC_BOX_REQUIRED', '.slide', 'The page has no semantic data-box markers.', 'Mark title, content, metric, visual, footer, or caption regions with data-box.');
  }
  for (const el of allSemantic) {
    if (el.closest('[data-decorative="true"]')) {
      push('SEMANTIC_MARKED_DECORATIVE', selector(el), 'A semantic box is also inside decorative content.', 'Content cannot be both semantic and decorative.', rectObject(el.getBoundingClientRect()));
    }
  }

  const textCarriers = [root, ...Array.from(root.querySelectorAll('*'))].filter(
    el => directTextNodes(el).length && !el.closest('[data-decorative="true"]')
  );
  // Base CSS gives decorations pointer-events:none, which also removes them
  // from elementsFromPoint(). Temporarily make them hit-testable so a model
  // cannot hide semantic text behind an opaque element merely by labeling it
  // decorative. The original inline values are restored before capture.
  const decorativeHitState = Array.from(root.querySelectorAll('[data-decorative="true"]')).map(element => ({
    element,
    value: element.style.getPropertyValue('pointer-events'),
    priority: element.style.getPropertyPriority('pointer-events'),
  }));
  for (const item of decorativeHitState) item.element.style.setProperty('pointer-events', 'auto', 'important');
  for (const textEl of textCarriers) {
    const semanticOwner = textEl.closest('[data-box]');
    const computed = getComputedStyle(textEl);
    const rect = textEl.getBoundingClientRect();
    if (!semanticOwner) {
      push('TEXT_OUTSIDE_SEMANTIC_BOX', selector(textEl), 'Visible text is outside a semantic data-box.', 'Wrap meaningful text in a title, content, metric, visual, footer, or caption box.', rectObject(rect));
      continue;
    }
    const boxType = semanticOwner.getAttribute('data-box') || 'content';
    const titleHeading = boxType === 'title'
      ? (semanticOwner.matches('h1,h2,h3') ? semanticOwner : semanticOwner.querySelector('h1,h2,h3'))
      : null;
    const size = Number.parseFloat(computed.fontSize) || 0;
    const minimum = boxType === 'title' && (textEl === titleHeading || titleHeading?.contains(textEl))
      ? (root.getAttribute('data-layout') === 'cover_minimal' ? profile.minimumCoverTitlePx : profile.minimumPageTitlePx)
      : (boxType === 'footer' || boxType === 'caption' || boxType === 'title' ? profile.minimumCaptionPx : profile.minimumBodyPx);
    if (size + .1 < minimum) {
      push('FONT_TOO_SMALL', selector(textEl), `Text is ${size}px; minimum is ${minimum}px.`, 'Reduce content instead of shrinking text.', rectObject(rect), { fontSize: size, minimum });
    }
    const foreground = parseColor(computed.color);
    const background = nearestSolidBackground(textEl);
    const contrastRatio = foreground && foreground.a >= .95 ? contrast(foreground, background) : 0;
    if (contrastRatio < 2.75) {
      push('LOW_TEXT_CONTRAST', selector(textEl), `Text contrast is ${contrastRatio.toFixed(2)}:1.`, 'Use a readable DesignSpec text color for this background.', rectObject(rect), { contrastRatio });
    }
    if (computed.display === 'none' || computed.visibility === 'hidden' || effectiveOpacity(textEl) < .05 || rect.width < 1 || rect.height < 1) {
      push('TEXT_HIDDEN', selector(textEl), 'Text is hidden, transparent, or zero-sized.', 'Restore visible text geometry and opacity.', rectObject(rect));
    }
    let occlusion = null;
    for (const textNode of directTextNodes(textEl)) {
      const range = document.createRange();
      range.selectNodeContents(textNode);
      const textRects = Array.from(range.getClientRects()).filter(item => item.width > 1 && item.height > 1);
      for (const textRect of textRects) {
        const samplePoints = [
          [.5, .5], [.2, .5], [.8, .5], [.5, .25], [.5, .75],
        ];
        for (const [xRatio, yRatio] of samplePoints) {
          const x = Math.min(profile.width - 1, Math.max(0, textRect.left + textRect.width * xRatio));
          const y = Math.min(profile.height - 1, Math.max(0, textRect.top + textRect.height * yRatio));
          const stack = document.elementsFromPoint(x, y);
          // Ancestors participate in painting the text itself and are not
          // occluders. Descendants are different: an absolutely positioned
          // child can cover its parent's direct text and must remain visible
          // to this check.
          const samePaintTree = node => node === textEl || node.contains(textEl);
          const ownIndex = stack.findIndex(samePaintTree);
          const candidates = ownIndex >= 0 ? stack.slice(0, ownIndex) : stack;
          const covering = candidates.find(node => elementPaintsAt(node, x, y));
          if (covering) {
            occlusion = { textRect, covering, x, y };
            break;
          }
        }
        if (occlusion) break;
      }
      if (occlusion) break;
    }
    if (occlusion) {
      push(
        'TEXT_OCCLUDED',
        selector(textEl),
        'Text is covered by another element.',
        'Remove the covering layer or correct z-index.',
        rectObject(occlusion.textRect),
        { occluder: selector(occlusion.covering), sampleX: occlusion.x, sampleY: occlusion.y },
      );
    }
  }
  for (const item of decorativeHitState) {
    if (item.value) item.element.style.setProperty('pointer-events', item.value, item.priority);
    else item.element.style.removeProperty('pointer-events');
  }

  const semantic = allSemantic.filter(el => !el.parentElement?.closest('[data-box]'));
  for (const el of semantic) {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const text = (el.innerText || el.textContent || '').trim();
    const sel = selector(el);
    if (!text && !el.querySelector('svg') && el.getAttribute('data-box') !== 'visual') {
      push('EMPTY_SEMANTIC_BOX', sel, 'A semantic box is empty.', 'Remove the empty box or provide its intended content.', rectObject(rect));
    }
    if (rect.left < -1 || rect.top < -1 || rect.right > profile.width + 1 || rect.bottom > profile.height + 1) {
      push('BOX_OUT_OF_BOUNDS', sel, 'A semantic box extends outside the safe canvas.', 'Move or resize the box.', rectObject(rect));
    }
    // Browser glyph ink commonly extends 8–15px beyond an auto-height line
    // box.  Keep horizontal checks strict and reserve vertical failures for
    // material overflow rather than font-metric ink bounds.
    if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 16) {
      push('TEXT_OVERFLOW', sel, 'Content overflows its semantic box.', 'Shorten copy or choose a roomier recipe.', rectObject(rect), { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight });
    }
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) < 0.05 || rect.width < 1 || rect.height < 1) {
      push('CONTENT_HIDDEN', sel, 'Important content is hidden, transparent, or zero-sized.', 'Restore visible geometry and opacity.', rectObject(rect));
    }

    const boxType = el.getAttribute('data-box') || 'content';
    const titleHeading = boxType === 'title' ? (el.matches('h1,h2,h3') ? el : el.querySelector('h1,h2,h3')) : null;
    if (boxType === 'title') {
      const heading = titleHeading;
      if (heading) {
        const headingStyle = getComputedStyle(heading);
        const lineHeight = Number.parseFloat(headingStyle.lineHeight) || (Number.parseFloat(headingStyle.fontSize) * 1.2);
        const lines = Math.max(1, Math.round(heading.getBoundingClientRect().height / lineHeight));
        const allowed = Number(el.getAttribute('data-title-lines') || heading.getAttribute('data-title-lines') || 1);
        if (lines > allowed) {
          push('TITLE_LINE_BUDGET', selector(heading), `Title uses ${lines} lines; recipe allows ${allowed}.`, 'Shorten the assertion title or use a two-line recipe.', rectObject(heading.getBoundingClientRect()), { lines, allowed });
        }
      }
    }
  }

  for (let i = 0; i < semantic.length; i += 1) {
    for (let j = i + 1; j < semantic.length; j += 1) {
      const a = semantic[i];
      const b = semantic[j];
      if (a.getAttribute('data-allow-overlap') === 'true' || b.getAttribute('data-allow-overlap') === 'true') continue;
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      const width = Math.max(0, Math.min(ar.right, br.right) - Math.max(ar.left, br.left));
      const height = Math.max(0, Math.min(ar.bottom, br.bottom) - Math.max(ar.top, br.top));
      const area = width * height;
      const smaller = Math.max(1, Math.min(ar.width * ar.height, br.width * br.height));
      if (area / smaller > 0.025) {
        push('BOX_OVERLAP', `${selector(a)} ↔ ${selector(b)}`, 'Top-level semantic boxes overlap unexpectedly.', 'Adjust grid tracks, gaps, or explicit geometry.', {}, { overlapRatio: area / smaller });
      }
    }
  }

  let shadows = 0;
  for (const el of root.querySelectorAll('*')) {
    const style = getComputedStyle(el);
    if (style.boxShadow && style.boxShadow !== 'none') shadows += style.boxShadow.split('),').length;
  }
  if (shadows > profile.maxShadows) push('SHADOW_BUDGET', '.slide', `Shadow count ${shadows} exceeds the budget.`, 'Use hierarchy and separators instead of repeated shadows.', {}, { shadows });
  return { diagnostics, domNodes, svgPaths };
}
"""


__all__ = [
    "BrowserContentError",
    "BrowserEnvironmentError",
    "BrowserMeasurement",
    "CapturedPNG",
    "DOM_MEASUREMENT_JS",
    "HTMLBrowserRenderer",
    "RenderProfile",
    "SecureBrowserSession",
]
