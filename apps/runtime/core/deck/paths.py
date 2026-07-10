"""Filesystem locations for deck artifacts."""

from __future__ import annotations

import os
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
