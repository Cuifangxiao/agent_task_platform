"""Deck error hierarchy used at service and pipeline boundaries."""

from __future__ import annotations


class DeckError(Exception):
    """Base class for deck generation failures with a user-facing message."""

    stage = "error"

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        if stage:
            self.stage = stage


class RenderError(DeckError):
    """Browser or fallback rendering failed."""


class ExportError(DeckError):
    """PPTX export failed or produced an invalid artifact."""
