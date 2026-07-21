"""Provider model utilities.

This follows the single-file management style used by the reference
`soulmate/utils/model_utils.py`: provider config loading, registry, and
provider-specific client construction all live together.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config import get_config

logger = logging.getLogger(__name__)


StructuredOutputMethod = Literal["json_schema", "json_mode", "function_calling"]


PROVIDER_CHAT_MODELS: dict[str, type[ChatOpenAI]] = {
    "openai": ChatOpenAI,
    "deepseek": ChatOpenAI,
    "qwen": ChatOpenAI,
}


@dataclass(frozen=True)
class CredentialProbeResult:
    status: str
    message: str


class CredentialTestConfig(BaseModel):
    enabled: bool = True
    model: str = ""
    prompt: str = "Reply with OK."
    max_tokens: int = Field(default=16, ge=1, le=256)


class ProviderModelParameter(BaseModel):
    name: str
    display_name: str = ""
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    options: list[Any] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None

    @property
    def default_value(self) -> str:
        return _stringify_parameter_value(self.default)

    @property
    def option_values(self) -> list[str]:
        return [_stringify_parameter_value(option) for option in self.options]


class ProviderModelDefinition(BaseModel):
    id: str
    model_name: str
    display_name: str
    type: Literal["text", "multimodal", "image"] = "text"
    reasoning_supported: bool = False
    disabled: bool = False
    structured_output_method: StructuredOutputMethod | None = None
    parameters: list[ProviderModelParameter] = Field(default_factory=list)
    default_parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderDefinition(BaseModel):
    name: str
    display_name: str
    description: str = ""
    manager: str
    sdk: str = ""
    default_base_url: str = ""
    supports_custom_base_url: bool = True
    structured_output_method: StructuredOutputMethod = "json_schema"
    credential_test: CredentialTestConfig = Field(default_factory=CredentialTestConfig)
    models: list[ProviderModelDefinition] = Field(default_factory=list)

    def find_model(self, model_id_or_name: str) -> ProviderModelDefinition | None:
        candidate = model_id_or_name.strip()
        for model in self.models:
            if model.id == candidate or model.model_name == candidate:
                return model
        return None

    def structured_output_method_for(
        self,
        model: ProviderModelDefinition,
    ) -> StructuredOutputMethod:
        return model.structured_output_method or self.structured_output_method


class ProviderConfigManager:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderDefinition] = {}
        self._loaded = False

    def load_all_provider_configs(self) -> None:
        if self._loaded:
            return

        catalog_dir = Path(get_config().get_provider_catalog_dir())
        if not catalog_dir.is_absolute():
            catalog_dir = Path.cwd() / catalog_dir

        if not catalog_dir.exists():
            raise ValueError(f"provider catalog dir not found: {catalog_dir}")

        providers: dict[str, ProviderDefinition] = {}
        for path in sorted(catalog_dir.glob("*.yaml")):
            provider = self._load_provider_definition(path)
            providers[provider.name] = provider

        self._providers = providers
        self._loaded = True
        logger.info("provider configs loaded. count=%s", len(self._providers))

    def list_providers(self) -> list[ProviderDefinition]:
        self.load_all_provider_configs()
        return list(self._providers.values())

    def get_provider(self, name: str) -> ProviderDefinition:
        self.load_all_provider_configs()
        provider_name = name.strip().lower()
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ValueError(f"provider '{name}' is not configured")
        return provider

    def test_credential(
        self,
        provider_name: str,
        api_key: str,
        base_url: str,
    ) -> CredentialProbeResult:
        provider = self.get_provider(provider_name)
        probe = provider.credential_test
        if not probe.enabled:
            return CredentialProbeResult(
                status="unsupported",
                message=f"provider '{provider.name}' does not support credential probe",
            )
        if not probe.model:
            raise ValueError(f"provider '{provider.name}' missing credential_test.model")

        client = self.build_chat_client(
            provider_name=provider.name,
            model_name=probe.model,
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            temperature=0,
            max_tokens=probe.max_tokens,
            provider_options={},
        )
        client.invoke(probe.prompt)

        return CredentialProbeResult(
            status="ok",
            message=f"credential probe succeeded using model '{probe.model}'",
        )

    def build_chat_client(
        self,
        provider_name: str,
        model_name: str,
        api_key: str,
        base_url: str,
        temperature: float,
        max_tokens: int,
        provider_options: dict[str, Any] | None = None,
    ) -> ChatOpenAI:
        provider = self.get_provider(provider_name)
        model = provider.find_model(model_name)
        if model is None:
            raise ValueError(
                f"model '{model_name}' is not configured for provider '{provider.name}'"
            )
        if model.type == "image":
            raise ValueError(
                f"image model '{model.model_name}' cannot be used as a chat model"
            )

        client_class = PROVIDER_CHAT_MODELS.get(provider.manager)
        if client_class is None:
            raise ValueError(f"provider manager '{provider.manager}' is not supported")

        kwargs: dict[str, Any] = {
            "model": model.model_name,
            "api_key": api_key,
            "base_url": base_url or provider.default_base_url or None,
            "timeout": 60,
            "max_retries": 0,
            "max_tokens": max_tokens,
            "metadata": {
                "provider": provider.name,
                "model_id": model.id,
                "structured_output_method": provider.structured_output_method_for(model),
            },
        }
        kwargs.update(self._runtime_kwargs_for_model(provider, model))
        kwargs.update(self._runtime_kwargs_for_options(provider, provider_options or {}))

        effective_options = {
            **model.default_parameters,
            **(provider_options or {}),
        }
        deepseek_thinking = (
            provider.manager == "deepseek"
            and "thinking" in effective_options
            and str(effective_options["thinking"]).strip().lower() == "enabled"
        )
        qwen_thinking = (
            provider.manager == "qwen"
            and "enable_thinking" in effective_options
            and _is_enabled_parameter_value(effective_options["enable_thinking"])
        )

        if _model_supports_parameter(model, "temperature") and not deepseek_thinking:
            kwargs["temperature"] = temperature
        else:
            kwargs.pop("temperature", None)

        if deepseek_thinking:
            # DeepSeek documents these sampling controls as ignored in thinking
            # mode, so do not send misleading no-op values to the provider.
            for parameter_name in (
                "top_p",
                "frequency_penalty",
                "presence_penalty",
            ):
                kwargs.pop(parameter_name, None)
        elif provider.manager == "deepseek":
            # Effort is only meaningful while DeepSeek thinking mode is enabled.
            kwargs.pop("reasoning_effort", None)

        if provider.manager == "qwen" and not qwen_thinking:
            # Qwen's thinking budget is only valid when hybrid thinking is on.
            extra_body = dict(kwargs.get("extra_body") or {})
            extra_body.pop("thinking_budget", None)
            if extra_body:
                kwargs["extra_body"] = extra_body
            else:
                kwargs.pop("extra_body", None)

        kwargs["max_tokens"] = max_tokens
        return client_class(**kwargs)

    @staticmethod
    def _runtime_kwargs_for_model(
        provider: ProviderDefinition,
        model: ProviderModelDefinition | None,
    ) -> dict[str, Any]:
        if model is None:
            return {}

        defaults = model.default_parameters
        kwargs: dict[str, Any] = {}

        for param_name in ("temperature", "top_p"):
            if param_name in defaults:
                kwargs[param_name] = defaults[param_name]

        if provider.manager == "openai" and defaults.get("reasoning_effort"):
            kwargs["reasoning_effort"] = defaults["reasoning_effort"]

        if provider.manager == "deepseek":
            if defaults.get("reasoning_effort"):
                kwargs["reasoning_effort"] = defaults["reasoning_effort"]
            if defaults.get("thinking"):
                kwargs["extra_body"] = {
                    **dict(kwargs.get("extra_body") or {}),
                    "thinking": {"type": defaults["thinking"]},
                }

        if provider.manager == "qwen":
            extra_body = dict(kwargs.get("extra_body") or {})
            if "enable_thinking" in defaults:
                extra_body["enable_thinking"] = _is_enabled_parameter_value(
                    defaults["enable_thinking"]
                )
            if "thinking_budget" in defaults:
                extra_body["thinking_budget"] = defaults["thinking_budget"]
            if extra_body:
                kwargs["extra_body"] = extra_body

        return kwargs

    @staticmethod
    def _runtime_kwargs_for_options(
        provider: ProviderDefinition,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}

        for param_name in ("top_p", "frequency_penalty", "presence_penalty"):
            if param_name in options:
                kwargs[param_name] = options[param_name]

        if provider.manager in {"openai", "deepseek"} and options.get("reasoning_effort"):
            kwargs["reasoning_effort"] = options["reasoning_effort"]

        if provider.manager == "deepseek" and options.get("thinking"):
            kwargs["extra_body"] = {
                **dict(kwargs.get("extra_body") or {}),
                "thinking": {"type": options["thinking"]},
            }

        if provider.manager == "qwen":
            extra_body = dict(kwargs.get("extra_body") or {})
            if "enable_thinking" in options:
                extra_body["enable_thinking"] = _is_enabled_parameter_value(
                    options["enable_thinking"]
                )
            if "thinking_budget" in options:
                extra_body["thinking_budget"] = options["thinking_budget"]
            if extra_body:
                kwargs["extra_body"] = extra_body

        return kwargs

    @staticmethod
    def _load_provider_definition(path: Path) -> ProviderDefinition:
        with path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}

        if not isinstance(raw, dict):
            raise ValueError(f"invalid provider catalog file: {path}")

        raw_provider: dict[str, Any] = dict(raw)
        raw_provider.setdefault("name", path.stem)
        raw_provider["name"] = str(raw_provider["name"]).strip().lower()
        raw_provider.setdefault("display_name", raw_provider["name"])
        raw_provider.setdefault("manager", raw_provider["name"])

        models = raw_provider.get("models", [])
        if isinstance(models, list):
            normalized_models: list[dict[str, Any]] = []
            for model in models:
                if not isinstance(model, dict):
                    continue
                normalized_models.append(_normalize_model_definition(model))
            raw_provider["models"] = normalized_models

        return ProviderDefinition(**raw_provider)


provider_config_manager = ProviderConfigManager()


def _normalize_model_definition(model: dict[str, Any]) -> dict[str, Any]:
    normalized_model = dict(model)
    normalized_model.setdefault("id", normalized_model.get("model_name", ""))
    normalized_model.setdefault("model_name", normalized_model.get("id", ""))
    normalized_model.setdefault("display_name", normalized_model.get("model_name", ""))
    model_type = str(normalized_model.get("type") or "text").strip().lower()
    # Older catalogs used `chat` for every conversational model. Keep those
    # definitions loadable while exposing the capability-oriented enum.
    normalized_model["type"] = (
        _infer_legacy_chat_model_type(str(normalized_model.get("model_name") or ""))
        if model_type == "chat"
        else model_type
    )

    parameters = _normalize_model_parameters(normalized_model.get("parameters", []))
    normalized_model["parameters"] = parameters

    defaults = dict(normalized_model.get("default_parameters") or {})
    for parameter in parameters:
        name = parameter.get("name")
        if name and "default" in parameter and name not in defaults:
            defaults[name] = parameter.get("default")
    normalized_model["default_parameters"] = defaults

    if "reasoning_supported" not in normalized_model:
        reasoning_parameters = {
            "reasoning_effort",
            "thinking_level",
            "enable_thinking",
            "thinking_budget",
        }
        normalized_model["reasoning_supported"] = any(
            parameter.get("name") in reasoning_parameters for parameter in parameters
        )

    return normalized_model


def _infer_legacy_chat_model_type(model_name: str) -> str:
    name = model_name.strip().lower()
    if re.search(r"(?:^|[-_. ])vl(?:[-_. ]|$)", name):
        return "multimodal"
    if any(
        marker in name
        for marker in (
            "gpt-4o",
            "gpt-4.1",
            "gpt-5",
            "vision",
            "gemini",
            "claude-3",
            "claude-4",
        )
    ):
        return "multimodal"
    return "text"


def _normalize_model_parameters(raw_parameters: Any) -> list[dict[str, Any]]:
    if isinstance(raw_parameters, dict):
        parameters: list[dict[str, Any]] = []
        for name, value in raw_parameters.items():
            parameter = dict(value) if isinstance(value, dict) else {"default": value}
            parameter.setdefault("name", name)
            parameters.append(parameter)
        return [_normalize_model_parameter(parameter) for parameter in parameters]

    if not isinstance(raw_parameters, list):
        return []

    return [
        _normalize_model_parameter(parameter)
        for parameter in raw_parameters
        if isinstance(parameter, dict)
    ]


def _normalize_model_parameter(parameter: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parameter)
    normalized["name"] = str(normalized.get("name", "")).strip()
    normalized.setdefault("display_name", normalized["name"])
    normalized.setdefault("type", "string")
    normalized.setdefault("description", "")
    normalized.setdefault("required", False)
    normalized.setdefault("options", [])
    return normalized


def _stringify_parameter_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _is_enabled_parameter_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "enabled", "on"}


def _model_supports_parameter(
    model: ProviderModelDefinition,
    parameter_name: str,
) -> bool:
    return any(parameter.name == parameter_name for parameter in model.parameters)
