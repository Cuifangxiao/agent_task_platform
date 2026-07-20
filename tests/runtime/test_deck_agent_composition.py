from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

import core.deck.agents as agents
import core.deck.events as events
from core.deck.agents import Design, Generation, Planning, Postprocessor
from core.deck.contract import EventType, PptStage
from core.deck.instrumentation import run_phase_graph
from core.deck.runner import PptGraph
from core.deck.state import DeckGraphState


ROOT_EDGES = {
    ("__start__", "planning_phase"),
    ("planning_phase", "design_phase"),
    ("design_phase", "generation_phase"),
    ("generation_phase", "postprocessor_phase"),
    ("postprocessor_phase", "__end__"),
}


def _edges(graph) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in graph.get_graph().edges}


def test_agents_expose_only_concrete_phase_classes():
    assert {"Design", "Generation", "Planning", "Postprocessor"}.issubset(agents.__all__)
    assert not hasattr(agents, "AgentNode")
    assert not hasattr(agents, "GraphFragment")
    assert not hasattr(agents, "DeckWorkflow")


def test_each_phase_owns_init_nodes_compile_graph_and_graph(tmp_path):
    planning = Planning({})
    design = Design({}, tmp_path)
    generation = Generation({}, tmp_path)
    postprocessor = Postprocessor(tmp_path)

    assert _edges(planning.graph) == {
        ("__start__", "brief"),
        ("brief", "outline"),
        ("outline", "__end__"),
    }
    assert _edges(design.graph) == {
        ("__start__", "style"),
        ("style", "design_lock"),
        ("design_lock", "__end__"),
    }
    assert _edges(generation.graph) == {
        ("__start__", "write_slides"),
        ("write_slides", "__end__"),
    }
    assert _edges(postprocessor.graph) == {
        ("__start__", "export"),
        ("export", "__end__"),
    }


def test_root_runner_assembles_phases_like_the_reference_project(tmp_path):
    module = PptGraph({}, tmp_path)

    assert _edges(module.graph) == ROOT_EDGES


def test_every_request_gets_fresh_root_and_phase_graphs(tmp_path):
    first = PptGraph({}, tmp_path / "first")
    second = PptGraph({}, tmp_path / "second")

    assert first.graph is not second.graph
    for phase_name in ("planning", "design", "generation", "postprocessor"):
        assert getattr(first, phase_name).graph is not getattr(second, phase_name).graph
    assert first.design.output_dir == tmp_path / "first"
    assert second.design.output_dir == tmp_path / "second"
    assert first.generation.output_dir == tmp_path / "first"
    assert second.postprocessor.output_dir == tmp_path / "second"


class _RecordingPhase:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        builder = StateGraph(DeckGraphState)
        builder.add_node("record", self.record)
        builder.add_edge(START, "record")
        builder.add_edge("record", END)
        self.graph = builder.compile()

    def record(self, state: DeckGraphState):
        title = str(state.get("deck_title") or "")
        return {"deck_title": f"{title}>{self.marker}"}

    def run(self, state: DeckGraphState):
        return run_phase_graph(self.graph, state)


def test_root_phase_wrappers_pass_state_in_order(tmp_path):
    module = PptGraph(
        {},
        tmp_path,
        planning=_RecordingPhase("planning"),
        design=_RecordingPhase("design"),
        generation=_RecordingPhase("generation"),
        postprocessor=_RecordingPhase("postprocessor"),
    )

    result = module.invoke({"deck_title": "seed"})

    assert result["deck_title"] == "seed>planning>design>generation>postprocessor"


class _CustomEventPhase:
    def __init__(self) -> None:
        builder = StateGraph(DeckGraphState)
        builder.add_node("emit", self.emit)
        builder.add_edge(START, "emit")
        builder.add_edge("emit", END)
        self.graph = builder.compile()

    @staticmethod
    def emit(state: DeckGraphState):
        get_stream_writer()(events.stage_progress("brief", "nested phase event"))
        return {"deck_title": f"{state.get('deck_title', '')}>nested"}

    def run(self, state: DeckGraphState):
        return run_phase_graph(self.graph, state)


def test_nested_phase_custom_events_reach_the_root_stream(tmp_path):
    module = PptGraph(
        {},
        tmp_path,
        planning=_CustomEventPhase(),
        design=_RecordingPhase("design"),
        generation=_RecordingPhase("generation"),
        postprocessor=_RecordingPhase("postprocessor"),
    )

    outputs = list(
        module.stream(
            {"deck_title": "seed"},
            stream_mode=["custom", "values"],
        )
    )
    custom = [data for mode, data in outputs if mode == "custom"]
    values = [data for mode, data in outputs if mode == "values"]

    assert len(custom) == 1
    assert custom[0].type is EventType.STEP_PROGRESS
    assert custom[0].stage is PptStage.PREPARING
    assert values[-1]["deck_title"] == "seed>nested>design>generation>postprocessor"


def test_default_phases_share_the_request_browser_session(tmp_path):
    browser_session = object()

    module = PptGraph({}, tmp_path, browser_session)

    assert module.generation.browser_renderer._browser_session is browser_session
    assert module.postprocessor.exporter._browser_session is browser_session
