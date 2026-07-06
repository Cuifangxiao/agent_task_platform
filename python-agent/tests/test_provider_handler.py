from unittest.mock import patch

from api import provider_pb2
from core.provider import contract as provider_contract
from core.provider.service import ProviderService
from handler.provider_handler import ProviderHandler


def test_test_credential_maps_proto_both_ways():
    handler = ProviderHandler(ProviderService())

    req = provider_pb2.TestCredentialRequest(
        user=provider_pb2.UserContext(user_id=1, username="alice"),
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
    )

    with patch(
        "core.provider.service.provider_config_manager.test_credential",
        return_value=type("ProbeResult", (), {"status": "ok", "message": "probe ok"})(),
    ):
        resp = handler.test_credential(req)

    assert isinstance(resp, provider_pb2.TestCredentialResponse)
    assert resp.provider == "openai"
    assert resp.status == "ok"
    assert resp.request_id


def test_list_providers_maps_proto_response():
    handler = ProviderHandler(ProviderService())
    parameter = type(
        "Parameter",
        (),
        {
            "name": "reasoning_effort",
            "display_name": "Reasoning effort",
            "type": "enum",
            "description": "",
            "default_value": "low",
            "options": ["none", "low", "medium", "high"],
            "option_values": ["none", "low", "medium", "high"],
            "required": False,
            "min": None,
            "max": None,
        },
    )()
    provider = type(
        "Provider",
        (),
        {
            "name": "openai",
            "display_name": "OpenAI",
            "description": "test",
            "manager": "openai",
            "sdk": "langchain_openai",
            "default_base_url": "https://api.openai.com/v1",
            "supports_custom_base_url": True,
            "credential_test": type("CredentialTest", (), {"enabled": True})(),
            "models": [
                type(
                    "Model",
                    (),
                    {
                        "id": "gpt-4o-mini",
                        "model_name": "gpt-4o-mini",
                        "display_name": "GPT-4o mini",
                        "type": "chat",
                        "reasoning_supported": False,
                        "disabled": False,
                        "parameters": [parameter],
                    },
                )()
            ],
        },
    )()

    with patch(
        "core.provider.service.provider_config_manager.list_providers",
        return_value=[provider],
    ):
        resp = handler.list_providers()

    assert isinstance(resp, provider_pb2.ListProvidersResponse)
    assert len(resp.providers) == 1
    assert resp.providers[0].name == "openai"
    assert resp.providers[0].models[0].parameters[0].name == "reasoning_effort"
    assert resp.providers[0].models[0].parameters[0].default_value == "low"


def test_stream_text_maps_proto_stream_response():
    service = ProviderService()
    handler = ProviderHandler(service)
    req = provider_pb2.StreamTextRequest(
        user=provider_pb2.UserContext(user_id=1, username="alice"),
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        prompt="Hello",
        system_prompt="Be brief.",
        temperature=0.7,
        max_tokens=128,
        provider_options_json='{"top_p":0.9}',
    )

    with patch.object(
        service,
        "stream_text",
        return_value=iter(
            [
                provider_contract.StreamTextOutput(
                    request_id="req-1",
                    delta="Hi",
                    sequence=1,
                ),
                provider_contract.StreamTextOutput(
                    request_id="req-1",
                    done=True,
                    sequence=1,
                ),
            ]
        ),
    ):
        responses = list(handler.stream_text(req))

    assert len(responses) == 2
    assert isinstance(responses[0], provider_pb2.StreamTextResponse)
    assert responses[0].request_id == "req-1"
    assert responses[0].delta == "Hi"
    assert responses[0].sequence == 1
    assert responses[1].done is True
