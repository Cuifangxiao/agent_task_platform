"""Provider credential and catalog domain service."""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from core.provider.contract import (
    ProviderModelParameterOutput,
    ProviderModelOutput,
    ProviderOutput,
    StreamTextInput,
    StreamTextOutput,
    TestCredentialInput,
    TestCredentialOutput,
)
from core.provider.model_utils import provider_config_manager

logger = logging.getLogger(__name__)


class ProviderService:
    def test_credential(self, inp: TestCredentialInput) -> TestCredentialOutput:
        request_id = str(uuid.uuid4())
        provider_name = inp.provider.strip().lower()

        logger.info(
            "test credential: request_id=%s user_id=%s provider=%s",
            request_id,
            inp.user_id,
            provider_name,
        )

        try:
            result = provider_config_manager.test_credential(
                provider_name=provider_name,
                api_key=inp.api_key.strip(),
                base_url=inp.base_url.strip(),
            )
            status = result.status
            message = result.message
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "test credential failed: request_id=%s provider=%s err=%s",
                request_id,
                provider_name,
                exc,
            )
            status = "failed"
            message = str(exc)

        return TestCredentialOutput(
            request_id=request_id,
            provider=provider_name,
            status=status,
            message=message,
        )

    def stream_text(self, inp: StreamTextInput) -> Iterator[StreamTextOutput]:
        request_id = str(uuid.uuid4())
        provider_name = inp.provider.strip().lower()
        sequence = 0

        logger.info(
            "stream text: request_id=%s user_id=%s provider=%s model=%s",
            request_id,
            inp.user_id,
            provider_name,
            inp.model,
        )

        try:
            client = provider_config_manager.build_chat_client(
                provider_name=provider_name,
                model_name=inp.model.strip(),
                api_key=inp.api_key.strip(),
                base_url=inp.base_url.strip(),
                temperature=inp.temperature,
                max_tokens=inp.max_tokens,
                provider_options=_decode_provider_options(inp.provider_options_json),
            )
            messages = _build_messages(inp.system_prompt, inp.prompt)
            for chunk in client.stream(messages):
                delta = _extract_chunk_text(chunk)
                if not delta:
                    continue
                sequence += 1
                yield StreamTextOutput(
                    request_id=request_id,
                    delta=delta,
                    sequence=sequence,
                )

            yield StreamTextOutput(
                request_id=request_id,
                done=True,
                sequence=sequence,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "stream text failed: request_id=%s provider=%s model=%s",
                request_id,
                provider_name,
                inp.model,
            )
            yield StreamTextOutput(
                request_id=request_id,
                done=True,
                error=str(exc),
                sequence=sequence,
            )

    def list_providers(self) -> list[ProviderOutput]:
        outputs: list[ProviderOutput] = []

        for provider in provider_config_manager.list_providers():
            outputs.append(
                ProviderOutput(
                    name=provider.name,
                    display_name=provider.display_name,
                    description=provider.description,
                    manager=provider.manager,
                    sdk=provider.sdk,
                    default_base_url=provider.default_base_url,
                    supports_credential_test=provider.credential_test.enabled,
                    supports_custom_base_url=provider.supports_custom_base_url,
                    models=[
                        ProviderModelOutput(
                            id=model.id,
                            model_name=model.model_name,
                            display_name=model.display_name,
                            type=model.type,
                            reasoning_supported=model.reasoning_supported,
                            parameters=[
                                ProviderModelParameterOutput(
                                    name=parameter.name,
                                    display_name=parameter.display_name,
                                    type=parameter.type,
                                    description=parameter.description,
                                    default_value=parameter.default_value,
                                    options=parameter.option_values,
                                    required=parameter.required,
                                    min=parameter.min,
                                    max=parameter.max,
                                )
                                for parameter in model.parameters
                            ],
                        )
                        for model in provider.models
                        if not model.disabled
                    ],
                )
            )

        return outputs


def _decode_provider_options(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}

    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("provider_options_json must be a JSON object")
    return decoded


def _build_messages(system_prompt: str, prompt: str) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    system_prompt = system_prompt.strip()
    if system_prompt:
        messages.append(("system", system_prompt))
    messages.append(("human", prompt))
    return messages


def _extract_chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""
