import hashlib

import pytest

from core.deck.contract import GeneratePptInput, InputResource
from core.deck.sources import parse_uploaded_files_with_provenance


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
