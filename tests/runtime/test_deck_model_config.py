import pytest

import core.deck.runtime.model_config as deck_model_config


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
