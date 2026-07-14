from types import SimpleNamespace

import grpc

from server.grpc import ProviderRuntimeServicer, RUNTIME_ERROR_MESSAGE


class RecordingContext:
    def __init__(self):
        self.code = None
        self.details = None

    def set_code(self, code):
        self.code = code

    def set_details(self, details):
        self.details = details


class FailingProviderHandler:
    def test_credential(self, _request):
        raise RuntimeError("provider secret must not leave server logs")


class FailingDeckHandler:
    def generate_deck(self, _request):
        raise RuntimeError("private artifact path must not leave server logs")


def test_unary_runtime_error_is_sanitized():
    servicer = ProviderRuntimeServicer(
        SimpleNamespace(
            provider_handler=FailingProviderHandler(),
            deck_handler=FailingDeckHandler(),
        )
    )
    context = RecordingContext()
    request = SimpleNamespace(
        user=SimpleNamespace(user_id=7, username="alice"),
        provider="openai",
    )

    servicer.TestCredential(request, context)

    assert context.code == grpc.StatusCode.INTERNAL
    assert context.details == RUNTIME_ERROR_MESSAGE
    assert "secret" not in context.details


def test_deck_stream_runtime_error_has_stable_safe_contract():
    servicer = ProviderRuntimeServicer(
        SimpleNamespace(
            provider_handler=FailingProviderHandler(),
            deck_handler=FailingDeckHandler(),
        )
    )
    request = SimpleNamespace(
        user=SimpleNamespace(user_id=7, username="alice"),
        user_description="Quarterly review",
        source_file_filename="",
    )

    responses = list(servicer.GenerateDeck(request, RecordingContext()))

    assert len(responses) == 1
    assert responses[0].event_version == 1
    assert responses[0].event_type == "error"
    assert responses[0].error == RUNTIME_ERROR_MESSAGE
    assert responses[0].error_code == "runtime_internal"
    assert responses[0].done is True
