"""Build per-node chat clients for deck pipeline requests."""

from __future__ import annotations

import json
from typing import Any

from core.deck.contract import DeckNodeModelConfig, GenerateDeckInput, ResumeDeckInput
from core.provider.model_utils import provider_config_manager


def build_deck_llms(inp: GenerateDeckInput | ResumeDeckInput) -> Any:
    if not inp.node_model_configs:
        raise ValueError("node_model_configs is required")

    clients: dict[str, Any] = {}
    capabilities: dict[str, dict[str, bool]] = {}
    metadata: dict[str, dict[str, str]] = {}
    for config in inp.node_model_configs:
        node_key = config.node_key.strip().lower()
        if not node_key:
            raise ValueError("node_model_configs[].node_key is required")
        clients[node_key] = build_chat_client_from_node(config)
        capabilities[node_key] = {"supports_image_input": bool(config.supports_image_input)}
        metadata[node_key] = {"provider": config.provider, "model": config.model}

    clients["__capabilities__"] = capabilities
    clients["__metadata__"] = metadata

    return clients


def describe_model_assignment(inp: GenerateDeckInput | ResumeDeckInput) -> str:
    parts = [f"{item.node_key}={item.provider}/{item.model}" for item in inp.node_model_configs]
    return ", ".join(parts)


def build_chat_client_from_node(config: DeckNodeModelConfig) -> Any:
    return build_chat_client(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        provider_options_json=config.provider_options_json,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )


def build_chat_client(
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    provider_options_json: str,
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
        provider_options=decode_provider_options(provider_options_json),
    )


def decode_provider_options(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}

    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("provider_options_json must be a JSON object")
    return decoded
