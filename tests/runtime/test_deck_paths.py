import json

import pytest

from core.deck.paths import DeckProjectPaths
from core.deck.sources import persist_normalized_source_bundle


def test_deck_project_paths_separate_author_sources_previews_and_delivery(tmp_path):
    project = DeckProjectPaths.for_request(tmp_path, "request-1").ensure()

    assert project.svg(2).relative_to(project.root).as_posix() == "svg_output/page-002.svg"
    assert project.preview_html(2).relative_to(project.root).as_posix() == "previews/page-002.html"
    assert project.preview_png(2).relative_to(project.root).as_posix() == "previews/page-002.png"
    assert project.deck_pptx.relative_to(project.root).as_posix() == "exports/deck.pptx"
    assert project.quality_report.relative_to(project.root).as_posix() == "reports/quality-report.json"
    assert project.notes_total.relative_to(project.root).as_posix() == "notes/total.md"
    for directory in (project.sources, project.analysis, project.images, project.templates, project.svg_output, project.previews, project.notes, project.reports, project.exports, project.backup):
        assert directory.is_dir()
    assert not project.private_debug.is_relative_to(project.root)
    assert project.raw_model_output(1, 0).is_relative_to(project.private_debug)


def test_normalized_sources_are_archived_before_strategy(tmp_path):
    project = DeckProjectPaths.for_request(tmp_path, "request-2")

    persist_normalized_source_bundle(
        project.root,
        source_names="brief.md",
        normalized_text="### brief.md\n\nRevenue grew 35%.",
        source_chunks=[{"source_file": "brief.md", "page": 1, "excerpt": "Revenue grew 35%."}],
    )

    assert "Revenue grew 35%" in (project.sources / "normalized-source.md").read_text(encoding="utf-8")
    manifest = json.loads((project.sources / "source-manifest.json").read_text(encoding="utf-8"))
    assert manifest["chunks"] == 1
    assert manifest["normalized_markdown"] == "normalized-source.md"


def test_request_directory_cannot_escape_artifact_root_through_symlink(tmp_path):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = artifact_root / "request-3"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="artifact directory"):
        DeckProjectPaths.for_request(artifact_root, "request-3")
