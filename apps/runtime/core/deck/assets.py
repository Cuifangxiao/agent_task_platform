"""Security rules shared by uploaded deck image assets and SVG consumers."""

from __future__ import annotations

import re
from pathlib import Path


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_EDGE = 8192
MAX_IMAGE_PIXELS = 40_000_000

# SVG author pages live in request/svg_output and uploaded images live in request/images.
# Asset basenames are deliberately opaque and contain no user-controlled path
# segments, dots, whitespace, or URL syntax.
SAFE_IMAGE_HREF_RE = re.compile(
    r"^\.\./images/[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.(?:png|jpe?g|webp)$",
    flags=re.IGNORECASE,
)


def is_image_filename(filename: str) -> bool:
    return Path(str(filename or "").strip()).suffix.casefold() in IMAGE_EXTENSIONS


def is_safe_image_href(href: str) -> bool:
    return bool(SAFE_IMAGE_HREF_RE.fullmatch(str(href or "").strip()))


def resolve_safe_image_href(svg_path: Path, href: str) -> Path:
    """Resolve an approved relative SVG image href inside its request/images dir."""

    if not is_safe_image_href(href):
        raise ValueError("SVG image href is not an approved request image path")

    resolved_svg = svg_path.expanduser().resolve(strict=True)
    request_dir = resolved_svg.parent.parent.resolve(strict=True)
    images_dir = (request_dir / "images").resolve(strict=True)
    candidate = (resolved_svg.parent / href).resolve(strict=True)
    try:
        candidate.relative_to(images_dir)
    except ValueError as exc:
        raise ValueError("SVG image asset escapes the request images directory") from exc
    if candidate.parent != images_dir or not candidate.is_file():
        raise ValueError("SVG image asset must be a regular file directly under request/images")
    if candidate.suffix.casefold() not in IMAGE_EXTENSIONS:
        raise ValueError("SVG image asset has an unsupported extension")
    if candidate.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("SVG image asset exceeds the size limit")
    return candidate


__all__ = [
    "IMAGE_EXTENSIONS",
    "IMAGE_MIME_TYPES",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_EDGE",
    "MAX_IMAGE_PIXELS",
    "SAFE_IMAGE_HREF_RE",
    "is_image_filename",
    "is_safe_image_href",
    "resolve_safe_image_href",
]
