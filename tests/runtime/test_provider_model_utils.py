from pathlib import Path

import pytest

from core.provider.model_utils import ProviderConfigManager


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


def test_provider_catalog_loads_qwen_vision_model():
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/qwen.yaml")
    )

    qwen_vl = provider.find_model("qwen3-vl-plus")

    assert qwen_vl is not None
    assert qwen_vl.model_name == "qwen3-vl-plus"
    assert qwen_vl.type == "multimodal"


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
