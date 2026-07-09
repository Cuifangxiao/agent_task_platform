import core.deck.events as events


def test_event_constructors_keep_expected_schema():
    event = events.slide_preview("Preview ready", {"index": 1})

    assert event == {
        "event_type": "slide_preview",
        "stage": "design_slides",
        "message": "Preview ready",
        "payload": {"index": 1},
    }


def test_event_requires_type():
    try:
        events.event("", "stage")
    except ValueError as exc:
        assert "event_type" in str(exc)
    else:
        raise AssertionError("event() should reject a blank event_type")
