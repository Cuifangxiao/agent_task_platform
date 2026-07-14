from unittest.mock import patch

from core.provider import contract as provider_contract
from core.provider.service import ProviderService


def test_test_credential_returns_ok():
    service = ProviderService()

    with patch(
        "core.provider.service.provider_config_manager.test_credential",
        return_value=type("ProbeResult", (), {"status": "ok", "message": "probe ok"})(),
    ):
        result = service.test_credential(
            provider_contract.TestCredentialInput(
                user_id=1,
                username="alice",
                provider="openai",
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
            )
        )

    assert result.provider == "openai"
    assert result.status == "ok"
    assert result.request_id


def test_list_providers_returns_catalog_entries():
    service = ProviderService()
    parameter = type(
        "Parameter",
        (),
        {
            "name": "reasoning_effort",
            "display_name": "Reasoning effort",
            "type": "enum",
            "description": "",
            "default_value": "low",
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
        result = service.list_providers()

    assert len(result) == 1
    assert result[0].name == "openai"
    assert result[0].models[0].model_name == "gpt-4o-mini"
    assert result[0].models[0].parameters[0].name == "reasoning_effort"
    assert result[0].models[0].parameters[0].default_value == "low"


def test_stream_text_yields_delta_and_done_events():
    service = ProviderService()

    class Chunk:
        def __init__(self, content):
            self.content = content

    class FakeClient:
        @staticmethod
        def stream(messages):
            assert messages == [("system", "Be brief."), ("human", "Hello")]
            yield Chunk("Hi")
            yield Chunk([{"text": " there"}])

    with patch(
        "core.provider.service.provider_config_manager.build_chat_client",
        return_value=FakeClient(),
    ) as build_client:
        events = list(
            service.stream_text(
                provider_contract.StreamTextInput(
                    user_id=1,
                    username="alice",
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
            )
        )

    assert [event.delta for event in events] == ["Hi", " there", ""]
    assert events[-1].done is True
    assert events[-1].request_id
    build_client.assert_called_once()
    assert build_client.call_args.kwargs["provider_options"] == {"top_p": 0.9}
