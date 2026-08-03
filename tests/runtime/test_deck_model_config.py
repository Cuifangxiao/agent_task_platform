import pytest

import core.deck.runtime.model_config as deck_model_config
from core.deck.domain.contracts import (
    MODEL_NODE_KEYS,
    GeneratePptInput,
    ModelConfig,
)


def _model(node_key: str, *, supports_image_input: bool = False) -> ModelConfig:
    return ModelConfig(
        node_key=node_key,
        provider="openai",
        model=f"model-for-{node_key.strip().lower()}",
        api_key="test",
        max_tokens=1_024,
        supports_image_input=supports_image_input,
    )


def _input(models: list[ModelConfig]) -> GeneratePptInput:
    return GeneratePptInput(
        task_id="model-config-test",
        description="Build a three-stage deck.",
        models=models,
    )


def test_ppt_model_nodes_are_exactly_outline_style_and_designer(monkeypatch):
    created = []

    def fake_build_chat_client_from_node(config):
        created.append(config.node_key.strip().lower())
        return f"client:{config.node_key.strip().lower()}"

    monkeypatch.setattr(
        deck_model_config,
        "build_chat_client_from_node",
        fake_build_chat_client_from_node,
    )

    assert MODEL_NODE_KEYS == frozenset({"outline", "style", "designer"})
    result = deck_model_config.build_ppt_llms(
        _input(
            [
                _model(" Outline "),
                _model("style"),
                _model("designer", supports_image_input=True),
            ]
        )
    )

    assert created == ["outline", "style", "designer"]
    assert set(result) == {
        "outline",
        "style",
        "designer",
        "__capabilities__",
        "__metadata__",
    }
    assert result["__capabilities__"] == {
        "outline": {"supports_image_input": False},
        "style": {"supports_image_input": False},
        "designer": {"supports_image_input": True},
    }


@pytest.mark.parametrize("retired_node", ["brief", "writer"])
def test_build_ppt_llms_rejects_retired_model_nodes(monkeypatch, retired_node):
    monkeypatch.setattr(
        deck_model_config,
        "build_chat_client_from_node",
        lambda config: object(),
    )
    models = [_model("outline"), _model("style"), _model("designer"), _model(retired_node)]

    with pytest.raises(ValueError, match=f"unsupported model node '{retired_node}'"):
        deck_model_config.build_ppt_llms(_input(models))


def test_build_ppt_llms_requires_every_three_stage_node(monkeypatch):
    monkeypatch.setattr(
        deck_model_config,
        "build_chat_client_from_node",
        lambda config: object(),
    )

    with pytest.raises(ValueError, match=r"missing model config for node\(s\): style"):
        deck_model_config.build_ppt_llms(
            _input([_model("outline"), _model("designer")])
        )


def test_build_chat_client_preserves_configured_max_tokens(monkeypatch):
    captured = {}
    client = object()

    def fake_build_chat_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(deck_model_config.provider_config_manager, "build_chat_client", fake_build_chat_client)

    result = deck_model_config.build_chat_client(
        provider="openai",
        model="gpt-4o-mini",
        api_key="test",
        base_url="",
        provider_options={"timeout": 30},
        temperature=0.2,
        max_tokens=512,
    )

    assert result is client
    assert captured["max_tokens"] == 512
    assert captured["provider_options"] == {"timeout": 30}


def test_build_chat_client_rejects_non_positive_max_tokens():
    with pytest.raises(ValueError, match="max_tokens must be positive"):
        deck_model_config.build_chat_client(
            provider="openai",
            model="gpt-4o-mini",
            api_key="test",
            base_url="",
            provider_options={},
            temperature=0.2,
            max_tokens=0,
        )
