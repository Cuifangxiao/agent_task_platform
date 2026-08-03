import hashlib

import pytest

from core.deck.domain.contracts import GeneratePptInput, InputResource
from core.deck.workspace.sources import (
    parse_uploaded_files_with_provenance,
    persist_uploaded_local_assets,
)


def _input(*resources: InputResource) -> GeneratePptInput:
    return GeneratePptInput(
        task_id="task-sources",
        description="Create a deck",
        attachments=list(resources),
    )


def test_uploaded_text_keeps_page_and_paragraph_provenance():
    names, text, chunks = parse_uploaded_files_with_provenance(
        _input(
            InputResource(
                resource_id="report",
                name="report.txt",
                content=b"[Page 2]\nRevenue reached 120 million.\n\nMargin was 42%.",
            )
        )
    )

    assert names == "report.txt"
    assert "Revenue reached 120 million." in text
    assert [chunk["page"] for chunk in chunks] == [2, 2]
    assert all(chunk["source_file"] == "report.txt" for chunk in chunks)


def test_image_upload_is_listed_but_excluded_from_text_evidence():
    names, text, chunks = parse_uploaded_files_with_provenance(
        _input(InputResource(resource_id="photo", name="photo.PNG", content=b"binary-image"))
    )

    assert names == "photo.PNG"
    assert text == ""
    assert chunks == []


def test_image_upload_is_persisted_as_bounded_task_local_metadata(tmp_path):
    content = b"\x89PNG\r\n\x1a\nlocal-image-evidence"
    assets = persist_uploaded_local_assets(
        tmp_path,
        _input(
            InputResource(
                resource_id="photo",
                name="../photo.PNG",
                mime_type="image/png",
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
            )
        ),
    )

    assert len(assets) == 1
    assert assets[0]["original_name"] == "photo.PNG"
    assert assets[0]["relative_path"] == "sources/assets/asset-001.png"
    assert assets[0]["trusted_local_only"] is True
    assert assets[0]["visual_analysis_available"] is False
    assert assets[0]["html_embedding_allowed"] is False
    assert (tmp_path / assets[0]["relative_path"]).read_bytes() == content


def test_source_checksum_is_verified_before_parsing():
    content = b"Revenue grew 35%."
    resource = InputResource(
        resource_id="report",
        name="report.txt",
        content=content,
        sha256=hashlib.sha256(b"different").hexdigest(),
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        parse_uploaded_files_with_provenance(_input(resource))
