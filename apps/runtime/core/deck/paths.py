"""Filesystem contract for self-contained, recoverable deck projects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def artifact_dir() -> Path:
    configured = os.getenv("DECK_ARTIFACTS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / "artifacts" / "decks").resolve()


def source_dir() -> Path:
    configured = os.getenv("DECK_SOURCES_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (artifact_dir().parent / "deck-sources").resolve()


@dataclass(frozen=True)
class DeckProjectPaths:
    """Canonical authoring, preview, QA, and delivery paths for one deck."""

    root: Path

    @classmethod
    def for_request(cls, output_root: Path, request_id: str) -> "DeckProjectPaths":
        request_id = str(request_id or "").strip()
        if not request_id or Path(request_id).name != request_id:
            raise ValueError("request_id must be one safe path segment")
        output_root = Path(output_root).resolve()
        root = (output_root / request_id).resolve()
        try:
            root.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("request_id must stay inside the artifact directory") from exc
        return cls(root)

    @property
    def sources(self) -> Path:
        return self.root / "sources"

    @property
    def analysis(self) -> Path:
        return self.root / "analysis"

    @property
    def images(self) -> Path:
        return self.root / "images"

    @property
    def templates(self) -> Path:
        return self.root / "templates"

    @property
    def svg_output(self) -> Path:
        return self.root / "svg_output"

    @property
    def previews(self) -> Path:
        return self.root / "previews"

    @property
    def styles(self) -> Path:
        return self.previews / "styles"

    @property
    def notes(self) -> Path:
        return self.root / "notes"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def backup(self) -> Path:
        return self.root / "backup"

    @property
    def design_spec_json(self) -> Path:
        return self.root / "design-spec.json"

    @property
    def design_spec_markdown(self) -> Path:
        return self.root / "design-spec.md"

    @property
    def spec_lock_json(self) -> Path:
        return self.root / "spec-lock.json"

    @property
    def spec_lock_markdown(self) -> Path:
        return self.root / "spec-lock.md"

    @property
    def deck_spec(self) -> Path:
        return self.root / "spec.json"

    @property
    def deck_pptx(self) -> Path:
        return self.exports / "deck.pptx"

    @property
    def export_manifest(self) -> Path:
        return self.reports / "export-manifest.json"

    @property
    def generation_manifest(self) -> Path:
        return self.reports / "generation-manifest.json"

    @property
    def quality_report(self) -> Path:
        return self.reports / "quality-report.json"

    @property
    def notes_total(self) -> Path:
        return self.notes / "total.md"

    def svg(self, page: int) -> Path:
        return self.svg_output / _page_filename(page, ".svg")

    def preview_html(self, page: int) -> Path:
        return self.previews / _page_filename(page, ".html")

    def preview_png(self, page: int) -> Path:
        return self.previews / _page_filename(page, ".png")

    def preview_background(self, page: int) -> Path:
        return self.previews / f"page-{_positive_page(page):03d}_bg.png"

    def ensure(self) -> "DeckProjectPaths":
        for path in (
            self.root,
            self.sources,
            self.analysis,
            self.images,
            self.templates,
            self.svg_output,
            self.previews,
            self.styles,
            self.notes,
            self.reports,
            self.exports,
            self.backup,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()


def _page_filename(page: int, suffix: str) -> str:
    return f"page-{_positive_page(page):03d}{suffix}"


def _positive_page(page: int) -> int:
    value = int(page)
    if value <= 0:
        raise ValueError("page index must be positive")
    return value


__all__ = ["DeckProjectPaths", "artifact_dir", "source_dir"]
