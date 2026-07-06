from pathlib import Path

from core.provider.model_utils import ProviderConfigManager


def test_provider_catalog_loads_qwen_model_parameters():
    provider = ProviderConfigManager._load_provider_definition(
        Path("providers/catalog/qwen.yaml")
    )

    qwen_plus = provider.find_model("qwen-plus")

    assert provider.name == "qwen"
    assert qwen_plus is not None
    assert qwen_plus.reasoning_supported is True
    assert qwen_plus.default_parameters["enable_thinking"] is False
    assert qwen_plus.parameters[0].name == "enable_thinking"
    assert qwen_plus.parameters[0].default_value == "false"
