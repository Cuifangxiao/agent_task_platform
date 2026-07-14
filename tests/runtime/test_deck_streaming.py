import json
from types import SimpleNamespace

import core.deck.events as events
from core.deck.streaming import event_to_output, stream_graph_outputs


class FakeGraph:
    def __init__(self, parts):
        self.parts = parts
        self.calls = []

    def stream(self, stream_input, **kwargs):
        self.calls.append((stream_input, kwargs))
        yield from self.parts


def test_stream_graph_outputs_uses_v2_envelope_and_synthesizes_done():
    graph = FakeGraph(
        [
            {
                "type": "custom",
                "ns": (),
                "data": events.stage_started("brief", "Preparing brief"),
            },
            {"type": "values", "ns": (), "data": {}, "interrupts": ()},
        ]
    )

    outputs = list(
        stream_graph_outputs(
            "job-1",
            "request-1",
            graph,
            {"deck_title": "Demo"},
            {"configurable": {"thread_id": "request-1"}},
            initial_sequence=4,
        )
    )

    assert [output.event_type for output in outputs] == ["stage_started", "done"]
    assert [output.sequence for output in outputs] == [5, 6]
    _, kwargs = graph.calls[0]
    assert kwargs == {
        "config": {"configurable": {"thread_id": "request-1"}},
        "stream_mode": ["custom", "values"],
        "version": "v2",
        "output_keys": [],
    }


def test_values_interrupt_becomes_terminal_outline_review_event():
    graph = FakeGraph(
        [
            {
                "type": "values",
                "ns": (),
                "data": {},
                "interrupts": (
                    SimpleNamespace(
                        value={
                            "stage": "outline_review",
                            "outline_markdown": "# Demo",
                            "allowed_actions": ["approve_outline"],
                        }
                    ),
                ),
            }
        ]
    )

    outputs = list(stream_graph_outputs("job-1", "request-1", graph, {}, {}))

    assert len(outputs) == 1
    interrupt = outputs[0]
    assert interrupt.event_type == "interrupt"
    assert interrupt.stage == "outline_review"
    assert interrupt.stage_status == "waiting"
    assert interrupt.action_required == "approve_outline"
    assert interrupt.outline_markdown == "# Demo"
    assert interrupt.done is True
    assert json.loads(interrupt.payload_json)["allowed_actions"] == ["approve_outline"]


def test_style_interrupt_uses_choose_style_action():
    graph = FakeGraph(
        [
            {
                "type": "values",
                "ns": (),
                "data": {},
                "interrupts": (SimpleNamespace(value={"stage": "style_review"}),),
            }
        ]
    )

    [interrupt] = list(stream_graph_outputs("job-1", "request-1", graph, {}, {}))

    assert interrupt.message == "Style options are ready for review"
    assert interrupt.action_required == "choose_style"


def test_existing_terminal_event_is_not_duplicated():
    graph = FakeGraph(
        [
            {
                "type": "custom",
                "ns": (),
                "data": events.generation_done(),
            }
        ]
    )

    outputs = list(stream_graph_outputs("job-1", "request-1", graph, {}, {}))

    assert [output.event_type for output in outputs] == ["done"]


def test_event_to_output_maps_payload_progress_slide_and_stage_alias():
    output = event_to_output(
        "job-1",
        "request-1",
        7,
        {
            "event_type": "slide_preview",
            "stage": "render",
            "message": "Preview ready",
            "payload": {"index": "3", "version": "2", "current": "3", "total": "8"},
            "severity": "warning",
        },
    )

    assert output.stage == "designer"
    assert (output.progress_current, output.progress_total) == (3, 8)
    assert (output.slide_index, output.slide_version) == (3, 2)
    assert output.severity == "warning"
    assert output.sequence == 7
