from pathlib import Path

import pytest

import core.provider.model_utils as model_utils
from core.provider.model_utils import ProviderConfigManager


class RecordingChatModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_provider_catalog_loads_qwen_model_parameters():
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/qwen.yaml")
    )

    qwen_plus = provider.find_model("qwen-plus")

    assert provider.name == "qwen"
    assert qwen_plus is not None
    assert qwen_plus.type == "text"
    assert qwen_plus.reasoning_supported is True
    assert qwen_plus.default_parameters["enable_thinking"] is False
    assert qwen_plus.parameters[0].name == "enable_thinking"
    assert qwen_plus.parameters[0].default_value == "false"
    thinking_budget = next(
        parameter for parameter in qwen_plus.parameters if parameter.name == "thinking_budget"
    )
    assert thinking_budget.default_value == ""
    assert thinking_budget.min == 1
    assert thinking_budget.max is None
    assert provider.structured_output_method_for(qwen_plus) == "json_mode"


def test_deepseek_catalog_defaults_to_thinking_enabled():
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/deepseek.yaml")
    )

    flash = provider.find_model("deepseek-v4-flash")

    assert flash is not None
    assert flash.default_parameters["thinking"] == "enabled"
    assert flash.default_parameters["reasoning_effort"] == "high"
    assert any(parameter.name == "temperature" for parameter in flash.parameters)
    assert provider.structured_output_method_for(flash) == "json_mode"


def test_gemini_catalog_exposes_multimodal_models_and_reasoning_options():
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/gemini.yaml")
    )

    flash = provider.find_model("gemini-3.5-flash")
    pro = provider.find_model("gemini-2.5-pro")

    assert provider.name == "gemini"
    assert provider.default_base_url == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert provider.credential_test.model == "gemini-2.5-flash"
    assert flash is not None
    assert flash.type == "multimodal"
    assert flash.reasoning_supported is True
    assert flash.default_parameters["reasoning_effort"] == "medium"
    assert pro is not None
    assert pro.type == "multimodal"
    assert provider.structured_output_method_for(pro) == "json_schema"


def test_kimi_catalog_exposes_k3_and_k2_6_with_model_specific_thinking():
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/kimi.yaml")
    )

    k3 = provider.find_model("kimi-k3")
    k2_6 = provider.find_model("kimi-k2.6")

    assert provider.name == "kimi"
    assert provider.default_base_url == "https://api.moonshot.cn/v1"
    assert provider.credential_test.model == "kimi-k2.6"
    assert k3 is not None
    assert k3.type == "multimodal"
    assert k3.reasoning_supported is True
    assert k3.default_parameters["reasoning_effort"] == "max"
    assert k2_6 is not None
    assert k2_6.type == "multimodal"
    assert k2_6.default_parameters["thinking"] == "enabled"
    assert provider.structured_output_method_for(k3) == "json_schema"
    assert provider.structured_output_method_for(k2_6) == "json_schema"


def test_model_can_override_provider_structured_output_method(tmp_path):
    catalog = tmp_path / "provider.yaml"
    catalog.write_text(
        """
name: compatible
display_name: Compatible
manager: openai
structured_output_method: json_mode
models:
  - id: schema-model
    model_name: schema-model
    display_name: Schema model
    structured_output_method: json_schema
""".strip(),
        encoding="utf-8",
    )

    provider = ProviderConfigManager._load_provider_definition(catalog)
    model = provider.find_model("schema-model")

    assert model is not None
    assert provider.structured_output_method_for(model) == "json_schema"


def test_provider_catalog_loads_qwen_vision_model():
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/qwen.yaml")
    )

    multimodal_models = (
        "qwen3.7-plus",
        "qwen3.6-plus",
        "qwen3.6-flash",
        "qwen3.6-35b-a3b",
        "qwen3.6-27b",
        "qwen3-vl-plus",
        "qwen3-vl-flash",
        "qwen-vl-plus",
    )

    for model_name in multimodal_models:
        model = provider.find_model(model_name)
        assert model is not None
        assert model.model_name == model_name
        assert model.type == "multimodal"

    qwen_max = provider.find_model("qwen3.7-max")
    assert qwen_max is not None
    assert qwen_max.type == "text"


def test_legacy_chat_model_type_is_normalized_to_text(tmp_path):
    catalog = tmp_path / "legacy.yaml"
    catalog.write_text(
        """
name: legacy
display_name: Legacy
manager: openai
models:
  - id: legacy-chat
    model_name: legacy-chat
    display_name: Legacy Chat
    type: chat
  - id: legacy-vl
    model_name: qwen3-vl-plus
    display_name: Legacy Vision Chat
    type: chat
""".strip(),
        encoding="utf-8",
    )

    provider = ProviderConfigManager._load_provider_definition(catalog)

    assert provider.models[0].type == "text"
    assert provider.models[1].type == "multimodal"


def test_image_model_cannot_be_built_as_chat_client(tmp_path):
    catalog = tmp_path / "images.yaml"
    catalog.write_text(
        """
name: images
display_name: Images
manager: openai
models:
  - id: image-model
    model_name: image-model
    display_name: Image Model
    type: image
""".strip(),
        encoding="utf-8",
    )
    provider = ProviderConfigManager._load_provider_definition(catalog)
    manager = ProviderConfigManager()
    manager._providers = {provider.name: provider}
    manager._loaded = True

    with pytest.raises(ValueError, match="cannot be used as a chat model"):
        manager.build_chat_client(
            provider_name="images",
            model_name="image-model",
            api_key="sk-test",
            base_url="",
            temperature=1,
            max_tokens=128,
        )


def test_deepseek_disabled_thinking_omits_effort_and_keeps_temperature(monkeypatch):
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/deepseek.yaml")
    )
    manager = ProviderConfigManager()
    manager._providers = {provider.name: provider}
    manager._loaded = True
    monkeypatch.setitem(model_utils.PROVIDER_CHAT_MODELS, "deepseek", RecordingChatModel)

    client = manager.build_chat_client(
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        api_key="sk-test",
        base_url="",
        temperature=0.6,
        max_tokens=128,
        provider_options={"thinking": "disabled", "reasoning_effort": "max"},
    )

    assert client.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert client.kwargs["temperature"] == 0.6
    assert "reasoning_effort" not in client.kwargs
    assert client.kwargs["metadata"]["structured_output_method"] == "json_mode"


def test_deepseek_enabled_thinking_omits_unsupported_sampling_controls(monkeypatch):
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/deepseek.yaml")
    )
    manager = ProviderConfigManager()
    manager._providers = {provider.name: provider}
    manager._loaded = True
    monkeypatch.setitem(model_utils.PROVIDER_CHAT_MODELS, "deepseek", RecordingChatModel)

    client = manager.build_chat_client(
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        api_key="sk-test",
        base_url="",
        temperature=0.6,
        max_tokens=128,
        provider_options={
            "thinking": "enabled",
            "reasoning_effort": "max",
            "top_p": 0.9,
        },
    )

    assert client.kwargs["reasoning_effort"] == "max"
    assert "temperature" not in client.kwargs
    assert "top_p" not in client.kwargs


def test_qwen_enabled_thinking_sends_numeric_budget_in_extra_body(monkeypatch):
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/qwen.yaml")
    )
    manager = ProviderConfigManager()
    manager._providers = {provider.name: provider}
    manager._loaded = True
    monkeypatch.setitem(model_utils.PROVIDER_CHAT_MODELS, "qwen", RecordingChatModel)

    client = manager.build_chat_client(
        provider_name="qwen",
        model_name="qwen3.6-flash",
        api_key="sk-test",
        base_url="",
        temperature=0.6,
        max_tokens=128,
        provider_options={"enable_thinking": True, "thinking_budget": 16384},
    )

    assert client.kwargs["extra_body"] == {
        "enable_thinking": True,
        "thinking_budget": 16384,
    }


def test_qwen_disabled_thinking_omits_budget(monkeypatch):
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/qwen.yaml")
    )
    manager = ProviderConfigManager()
    manager._providers = {provider.name: provider}
    manager._loaded = True
    monkeypatch.setitem(model_utils.PROVIDER_CHAT_MODELS, "qwen", RecordingChatModel)

    client = manager.build_chat_client(
        provider_name="qwen",
        model_name="qwen3.6-flash",
        api_key="sk-test",
        base_url="",
        temperature=0.6,
        max_tokens=128,
        provider_options={"enable_thinking": "false", "thinking_budget": 16384},
    )

    assert client.kwargs["extra_body"] == {"enable_thinking": False}


def test_gemini_uses_compatible_endpoint_and_reasoning_effort(monkeypatch):
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/gemini.yaml")
    )
    manager = ProviderConfigManager()
    manager._providers = {provider.name: provider}
    manager._loaded = True
    monkeypatch.setitem(model_utils.PROVIDER_CHAT_MODELS, "gemini", RecordingChatModel)

    client = manager.build_chat_client(
        provider_name="gemini",
        model_name="gemini-3.5-flash",
        api_key="gemini-test-key",
        base_url="",
        temperature=0.6,
        max_tokens=2048,
        provider_options={"reasoning_effort": "high"},
    )

    assert client.kwargs["base_url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert client.kwargs["reasoning_effort"] == "high"
    assert client.kwargs["max_tokens"] == 2048
    assert "temperature" not in client.kwargs
    assert client.kwargs["metadata"]["provider"] == "gemini"


def test_kimi_uses_compatible_endpoint_and_model_specific_thinking(monkeypatch):
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/kimi.yaml")
    )
    manager = ProviderConfigManager()
    manager._providers = {provider.name: provider}
    manager._loaded = True
    monkeypatch.setitem(model_utils.PROVIDER_CHAT_MODELS, "kimi", RecordingChatModel)

    k3_client = manager.build_chat_client(
        provider_name="kimi",
        model_name="kimi-k3",
        api_key="moonshot-test-key",
        base_url="",
        temperature=0.2,
        max_tokens=4096,
        provider_options={"reasoning_effort": "high"},
    )
    k2_client = manager.build_chat_client(
        provider_name="kimi",
        model_name="kimi-k2.6",
        api_key="moonshot-test-key",
        base_url="",
        temperature=0.2,
        max_tokens=4096,
        provider_options={"thinking": "disabled"},
    )

    assert k3_client.kwargs["base_url"] == "https://api.moonshot.cn/v1"
    assert k3_client.kwargs["metadata"]["structured_output_method"] == "json_schema"
    assert k3_client.kwargs["reasoning_effort"] == "high"
    assert "temperature" not in k3_client.kwargs
    assert k2_client.kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert k2_client.kwargs["metadata"]["structured_output_method"] == "json_schema"
    assert "temperature" not in k2_client.kwargs
