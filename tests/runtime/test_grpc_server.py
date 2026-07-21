from datetime import datetime, timezone
from types import SimpleNamespace

import grpc
import pytest

from api import ppt_agent_pb2
from config import GrpcServerConfig
from core.deck.domain.contracts import AgentEvent, EventType, PptStage, ResourceRef, ResourceType
from handler.ppt_handler import PptHandler, _resource_to_proto, _to_proto
from server.grpc import GrpcServer, PptAgentServicer, ProviderRuntimeServicer, RUNTIME_ERROR_MESSAGE


class RecordingContext:
    def __init__(self, *, active=True):
        self.code = None
        self.details = None
        self.active = active

    def set_code(self, code):
        self.code = code

    def set_details(self, details):
        self.details = details

    def is_active(self):
        return self.active


class FailingProviderHandler:
    def test_credential(self, _request):
        raise RuntimeError("provider secret must not leave server logs")


def test_provider_service_sanitizes_errors():
    servicer = ProviderRuntimeServicer(
        SimpleNamespace(provider_handler=FailingProviderHandler(), ppt_handler=object())
    )
    context = RecordingContext()
    request = SimpleNamespace(provider="openai")

    servicer.TestCredential(request, context)

    assert context.code == grpc.StatusCode.INTERNAL
    assert context.details == RUNTIME_ERROR_MESSAGE


def test_ppt_servicer_delegates_the_single_generate_stream():
    expected = ppt_agent_pb2.AgentEvent(
        task_id="task-1",
        sequence=1,
        type=ppt_agent_pb2.TASK_STARTED,
        stage=ppt_agent_pb2.PREPARING,
    )

    class RecordingPptHandler:
        def __init__(self):
            self.calls = []

        def generate_ppt(self, request, context):
            self.calls.append((request, context))
            yield expected

    handler = RecordingPptHandler()
    servicer = PptAgentServicer(SimpleNamespace(provider_handler=object(), ppt_handler=handler))
    request = ppt_agent_pb2.GeneratePptRequest(task_id="task-1", description="Demo")
    context = RecordingContext()

    assert list(servicer.GeneratePpt(request, context)) == [expected]
    assert handler.calls == [(request, context)]


@pytest.mark.parametrize(
    ("event_type", "proto_type"),
    [
        (EventType.TASK_STARTED, ppt_agent_pb2.TASK_STARTED),
        (EventType.STEP_STARTED, ppt_agent_pb2.STEP_STARTED),
        (EventType.STEP_PROGRESS, ppt_agent_pb2.STEP_PROGRESS),
        (EventType.STEP_COMPLETED, ppt_agent_pb2.STEP_COMPLETED),
        (EventType.ARTIFACT_READY, ppt_agent_pb2.ARTIFACT_READY),
        (EventType.TASK_COMPLETED, ppt_agent_pb2.TASK_COMPLETED),
        (EventType.TASK_FAILED, ppt_agent_pb2.TASK_FAILED),
    ],
)
def test_ppt_handler_maps_every_event_enum(event_type, proto_type):
    event = AgentEvent(
        task_id="task-1",
        sequence=3,
        created_at=datetime.now(timezone.utc),
        type=event_type,
        stage=PptStage.PREPARING,
    )

    assert _to_proto(event).type == proto_type


def test_ppt_handler_preserves_producer_event_id():
    revision = "a" * 64
    event = AgentEvent(
        task_id="task-1",
        sequence=3,
        created_at=datetime.now(timezone.utc),
        type=EventType.ARTIFACT_READY,
        stage=PptStage.RENDERING,
        event_id="slide-preview:task-1:s01:0123456789abcdef0123",
        revision=revision,
    )

    assert _to_proto(event).event_id == event.event_id
    assert _to_proto(event).revision == revision


@pytest.mark.parametrize(
    ("stage", "proto_stage"),
    [
        (PptStage.PREPARING, ppt_agent_pb2.PREPARING),
        (PptStage.PLANNING, ppt_agent_pb2.PLANNING),
        (PptStage.GENERATING_CONTENT, ppt_agent_pb2.GENERATING_CONTENT),
        (PptStage.GENERATING_ASSETS, ppt_agent_pb2.GENERATING_ASSETS),
        (PptStage.GENERATING_PAGES, ppt_agent_pb2.GENERATING_PAGES),
        (PptStage.RENDERING, ppt_agent_pb2.RENDERING),
        (PptStage.ASSEMBLING_PPT, ppt_agent_pb2.ASSEMBLING_PPT),
        (PptStage.FINISHED, ppt_agent_pb2.FINISHED),
    ],
)
def test_ppt_handler_maps_every_stage_enum(stage, proto_stage):
    event = AgentEvent(
        task_id="task-1",
        sequence=3,
        created_at=datetime.now(timezone.utc),
        type=EventType.STEP_PROGRESS,
        stage=stage,
    )

    assert _to_proto(event).stage == proto_stage


@pytest.mark.parametrize(
    ("resource_type", "proto_type"),
    [
        (ResourceType.INPUT_ATTACHMENT, ppt_agent_pb2.INPUT_ATTACHMENT),
        (ResourceType.IMAGE, ppt_agent_pb2.IMAGE),
        (ResourceType.SVG, ppt_agent_pb2.SVG),
        (ResourceType.HTML, ppt_agent_pb2.HTML),
        (ResourceType.PAGE_PREVIEW, ppt_agent_pb2.PAGE_PREVIEW),
        (ResourceType.PPTX, ppt_agent_pb2.PPTX),
        (ResourceType.OTHER_FILE, ppt_agent_pb2.OTHER_FILE),
    ],
)
def test_ppt_handler_maps_every_resource_enum(resource_type, proto_type):
    resource = ResourceRef(
        resource_id="resource-1",
        type=resource_type,
        name="artifact.bin",
        relative_path="exports/artifact.bin",
    )

    assert _resource_to_proto(resource).type == proto_type


def test_ppt_handler_uses_provider_options_dict_and_stops_on_cancelled_context():
    class RecordingService:
        def __init__(self):
            self.input = None

        def generate_ppt(self, inp):
            self.input = inp
            yield AgentEvent(
                task_id=inp.task_id,
                sequence=1,
                created_at=datetime.now(timezone.utc),
                type=EventType.TASK_STARTED,
                stage=PptStage.PREPARING,
            )

    service = RecordingService()
    request = ppt_agent_pb2.GeneratePptRequest(
        task_id="task-9",
        description="Demo",
        models=[
            ppt_agent_pb2.ModelConfig(
                node_key="writer",
                provider="openai",
                model="gpt-test",
                provider_options={"timeout": 30, "reasoning": {"effort": "low"}},
                max_tokens=1000,
            )
        ],
    )

    assert list(PptHandler(service).generate_ppt(request, RecordingContext(active=False))) == []
    assert service.input.models[0].provider_options == {
        "timeout": 30.0,
        "reasoning": {"effort": "low"},
    }


def test_grpc_server_registers_provider_and_ppt_services(monkeypatch):
    registrations = []

    class FakeServer:
        def add_insecure_port(self, address):
            self.address = address

    fake_server = FakeServer()
    monkeypatch.setattr("server.grpc.grpc.server", lambda *_args, **_kwargs: fake_server)
    monkeypatch.setattr(
        "server.grpc.provider_pb2_grpc.add_ProviderRuntimeServiceServicer_to_server",
        lambda _servicer, server: registrations.append(("provider", server)),
    )
    monkeypatch.setattr(
        "server.grpc.ppt_agent_pb2_grpc.add_PptAgentServiceServicer_to_server",
        lambda _servicer, server: registrations.append(("ppt", server)),
    )

    GrpcServer(
        GrpcServerConfig(host="127.0.0.1", port=50099, max_workers=2),
        SimpleNamespace(provider_handler=object(), ppt_handler=object()),
    )

    assert registrations == [("provider", fake_server), ("ppt", fake_server)]
    assert fake_server.address == "127.0.0.1:50099"
