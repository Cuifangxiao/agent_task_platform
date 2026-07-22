import core.deck.agents.processor.generation as nodes


def test_preview_event_id_is_stable_for_replay_and_changes_with_content_or_resource_hash():
    first = nodes._preview_event_id(
        "run-1", "s01", content_hash="a" * 64, preview_hash="c" * 64
    )
    replayed = nodes._preview_event_id(
        "run-1", "s01", content_hash="a" * 64, preview_hash="c" * 64
    )
    changed_resource = nodes._preview_event_id(
        "run-1", "s01", content_hash="a" * 64, preview_hash="d" * 64
    )
    changed_content = nodes._preview_event_id(
        "run-1", "s01", content_hash="b" * 64, preview_hash="c" * 64
    )

    assert first == replayed
    assert changed_resource != first
    assert changed_content != first
    assert first.startswith("slide-preview:run-1:s01:")


def test_emit_slide_preview_uses_the_id_persisted_with_slide_metadata(monkeypatch):
    event_id = "slide-preview:run-1:s01:0123456789abcdef0123"
    slide = {
        "slide_id": "s01",
        "index": 1,
        "role": "cover",
        "assertion_title": "A clear assertion",
        "takeaway": "The one idea the audience should retain.",
        "content_intent": "Open the narrative.",
        "layout_family": "cover_minimal",
        "rhythm": "anchor",
        "transition_from_previous": "",
    }
    emitted = []
    monkeypatch.setattr(nodes, "get_stream_writer", lambda: emitted.append)

    nodes.emit_slide_preview(
        {
            "deck_plan": {
                "deck_title": "Demo",
                "core_message": "One message",
                "narrative_arc": ["Open with the conclusion"],
                "slides": [slide],
            },
            "current_slide_plan": slide,
            "completed_slides": {
                "s01": {
                    "event_id": event_id,
                    "preview_path": "previews/page-001.png",
                    "artifact_hash": "a" * 64,
                }
            },
        }
    )

    assert len(emitted) == 1
    assert emitted[0].event_id == event_id
    assert emitted[0].revision == "a" * 64
