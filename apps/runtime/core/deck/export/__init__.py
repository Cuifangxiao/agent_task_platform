"""PPTX export package for rendered deck artifacts."""

from core.deck.export.text_layer import HIDE_TEXT_JS, TEXT_LAYER_JS

__all__ = ["DeckExporter", "ExportResult", "HIDE_TEXT_JS", "TEXT_LAYER_JS", "extract_text_layer"]


def __getattr__(name: str):
    if name in {"DeckExporter", "ExportResult", "extract_text_layer"}:
        from core.deck.export import pptx

        return getattr(pptx, name)
    raise AttributeError(name)
