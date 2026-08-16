"""Trusted local SVG icon registry and placeholder injection."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.deck.design_system.catalog import DESIGN_SYSTEM_DIR


REGISTRY_PATH = DESIGN_SYSTEM_DIR / "icons" / "lucide.json"
ALLOWED_TAGS = frozenset({"path", "circle", "ellipse", "line", "polyline", "polygon", "rect"})
ALLOWED_ATTRIBUTES = frozenset(
    {"d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "x2", "y1", "y2", "width", "height", "points"}
)
ICON_PLACEHOLDER_RE = re.compile(
    r"<span(?P<before>[^>]*?)\sdata-icon=(?P<quote>[\"'])(?P<key>[a-z0-9_-]{1,64})(?P=quote)(?P<after>[^>]*)>\s*</span>",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class TrustedIcon:
    key: str
    tags: tuple[str, ...]
    view_box: str
    nodes: tuple[dict[str, Any], ...]


class IconRegistry:
    def __init__(self, path: Path = REGISTRY_PATH) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != "trusted-icon-registry.v1":
            raise ValueError("unsupported icon registry schema")
        icons: dict[str, TrustedIcon] = {}
        for item in raw.get("icons") or []:
            key = str(item.get("key") or "").strip()
            nodes = tuple(item.get("nodes") or ())
            if not key or not nodes:
                continue
            _validate_nodes(nodes)
            icons[key] = TrustedIcon(
                key=key,
                tags=tuple(str(tag) for tag in item.get("tags") or ()),
                view_box=str(item.get("view_box") or "0 0 24 24"),
                nodes=nodes,
            )
        if not icons:
            raise ValueError("icon registry is empty")
        self._icons = icons
        self.source = str(raw.get("source") or "")
        self.license = str(raw.get("license") or "")

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._icons))

    def search(self, query: str, *, limit: int = 12) -> list[str]:
        terms = {part.casefold() for part in re.findall(r"[\w\u3400-\u9fff-]+", query or "") if len(part) > 1}
        scored: list[tuple[int, str]] = []
        for key, icon in self._icons.items():
            haystack = " ".join((key, *icon.tags)).casefold()
            score = sum(1 for term in terms if term in haystack)
            scored.append((score, key))
        scored.sort(key=lambda item: (-item[0], item[1]))
        matching = [key for score, key in scored if score > 0]
        return (matching or list(self.keys()))[: max(1, limit)]

    def render(
        self,
        key: str,
        *,
        fallback: str = "circle-check",
        extra_classes: tuple[str, ...] = (),
    ) -> str:
        icon = self._icons.get(str(key or "").strip()) or self._icons.get(fallback)
        if icon is None:
            return ""
        class_names = tuple(dict.fromkeys(("icon", *extra_classes)))
        class_attribute = html.escape(" ".join(class_names), quote=True)
        nodes = "".join(_render_node(node) for node in icon.nodes)
        return (
            f'<span class="{class_attribute}" data-resolved-icon="true" '
            f'data-icon-key="{html.escape(icon.key, quote=True)}">'
            f'<svg viewBox="{html.escape(icon.view_box, quote=True)}" aria-hidden="true" '
            'focusable="false" fill="none" stroke="currentColor" stroke-width="2">'
            f"{nodes}</svg></span>"
        )

    def inject(self, fragment: str) -> tuple[str, list[str], list[str]]:
        resolved: list[str] = []
        missing: list[str] = []

        def replace(match: re.Match[str]) -> str:
            key = match.group("key").casefold()
            if key not in self._icons:
                if key not in missing:
                    missing.append(key)
                return ""
            # The renderer contract records the distinct icon keys used on a
            # page, not every repeated occurrence.  Keeping occurrences here
            # used to overflow HtmlSlideSpec's 10-key limit on diagrams that
            # intentionally reused one icon across many nodes.
            if key not in resolved:
                resolved.append(key)
            return self.render(key, extra_classes=_placeholder_classes(match))

        return ICON_PLACEHOLDER_RE.sub(replace, fragment), resolved, missing


@lru_cache(maxsize=1)
def icon_registry() -> IconRegistry:
    return IconRegistry()


def _validate_nodes(nodes: tuple[dict[str, Any], ...]) -> None:
    if len(nodes) > 16:
        raise ValueError("trusted icon exceeds node budget")
    for node in nodes:
        tag = str(node.get("tag") or "")
        attributes = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
        if tag not in ALLOWED_TAGS:
            raise ValueError(f"unsupported trusted icon tag: {tag}")
        if set(attributes) - ALLOWED_ATTRIBUTES:
            raise ValueError(f"unsupported trusted icon attributes on {tag}")


def _render_node(node: dict[str, Any]) -> str:
    tag = str(node["tag"])
    attributes = node.get("attributes") or {}
    rendered = " ".join(
        f'{name}="{html.escape(str(value), quote=True)}"'
        for name, value in sorted(attributes.items())
    )
    return f"<{tag} {rendered}/>"


def _placeholder_classes(match: re.Match[str]) -> tuple[str, ...]:
    """Keep safe presentation classes attached to a model icon placeholder.

    The trusted SVG still replaces the entire placeholder.  Retaining only its
    class list lets page CSS size and align the resolved icon without carrying
    arbitrary model-authored attributes into the trusted fragment.
    """

    attributes = f'{match.group("before")} {match.group("after")}'
    class_match = re.search(
        r"\bclass\s*=\s*([\"'])(?P<classes>[^\"']*)\1",
        attributes,
        flags=re.IGNORECASE,
    )
    if class_match is None:
        return ()
    names = re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", html.unescape(class_match.group("classes")))
    return tuple(dict.fromkeys(name for name in names if name != "icon"))


__all__ = ["IconRegistry", "TrustedIcon", "icon_registry"]
