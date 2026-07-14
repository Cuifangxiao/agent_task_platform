"""Build the small, explicit model registry used by the PPT pipeline."""

from __future__ import annotations

from typing import Any

from core.deck.contract import GeneratePptInput, MODEL_NODE_KEYS, ModelConfig
from core.provider.model_utils import provider_config_manager


def build_ppt_llms(inp: GeneratePptInput) -> dict[str, Any]:
    if not inp.models:
        raise ValueError("models is required")

    clients: dict[str, Any] = {}
    capabilities: dict[str, dict[str, bool]] = {}
    metadata: dict[str, dict[str, str]] = {}
    for config in inp.models:
        node_key = config.node_key.strip().lower()
        if node_key not in MODEL_NODE_KEYS:
            allowed = ", ".join(sorted(MODEL_NODE_KEYS))
            raise ValueError(f"unsupported model node '{config.node_key}'; expected one of: {allowed}")
        if node_key in clients:
            raise ValueError(f"duplicate model config for node '{node_key}'")
        clients[node_key] = build_chat_client_from_node(config)
        capabilities[node_key] = {"supports_image_input": bool(config.supports_image_input)}
        metadata[node_key] = {"provider": config.provider, "model": config.model}

    clients["__capabilities__"] = capabilities
    clients["__metadata__"] = metadata
    return clients


def describe_model_assignment(inp: GeneratePptInput) -> str:
    return ", ".join(f"{item.node_key}={item.provider}/{item.model}" for item in inp.models)


def build_chat_client_from_node(config: ModelConfig) -> Any:
    return build_chat_client(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        provider_options=config.provider_options,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )


def build_chat_client(
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    provider_options: dict[str, Any],
    temperature: float,
    max_tokens: int,
) -> Any:
    normalized_max_tokens = int(max_tokens or 0)
    if normalized_max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    return provider_config_manager.build_chat_client(
        provider_name=provider.strip().lower(),
        model_name=model.strip(),
        api_key=api_key.strip(),
        base_url=base_url.strip(),
        temperature=temperature,
        max_tokens=normalized_max_tokens,
        provider_options=dict(provider_options or {}),
    )


__all__ = [
    "build_chat_client",
    "build_chat_client_from_node",
    "build_ppt_llms",
    "describe_model_assignment",
]
