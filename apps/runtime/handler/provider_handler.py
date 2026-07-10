"""provider 相关 RPC 的编排层: proto <-> core contract 的双向转换。

不处理异常、不 set StatusCode——那是 server 层(错误边界)的职责。
"""

from api import provider_pb2
from core.provider.contract import StreamTextInput, TestCredentialInput
from core.provider.service import ProviderService


class ProviderHandler:
    def __init__(self, provider_service: ProviderService) -> None:
        self._provider_service = provider_service

    def test_credential(
        self, req: provider_pb2.TestCredentialRequest
    ) -> provider_pb2.TestCredentialResponse:
        out = self._provider_service.test_credential(
            TestCredentialInput(
                user_id=req.user.user_id,
                username=req.user.username,
                provider=req.provider,
                api_key=req.api_key,
                base_url=req.base_url,
            )
        )
        return provider_pb2.TestCredentialResponse(
            request_id=out.request_id,
            provider=out.provider,
            status=out.status,
            message=out.message,
        )

    def list_providers(
        self,
    ) -> provider_pb2.ListProvidersResponse:
        providers = self._provider_service.list_providers()
        return provider_pb2.ListProvidersResponse(
            providers=[
                provider_pb2.ProviderDescriptor(
                    name=provider.name,
                    display_name=provider.display_name,
                    description=provider.description,
                    manager=provider.manager,
                    sdk=provider.sdk,
                    default_base_url=provider.default_base_url,
                    supports_credential_test=provider.supports_credential_test,
                    supports_custom_base_url=provider.supports_custom_base_url,
                    models=[
                        provider_pb2.ProviderModel(
                            id=model.id,
                            model_name=model.model_name,
                            display_name=model.display_name,
                            type=model.type,
                            reasoning_supported=model.reasoning_supported,
                            parameters=[
                                _model_parameter_to_proto(parameter)
                                for parameter in model.parameters
                            ],
                        )
                        for model in provider.models
                    ],
                )
                for provider in providers
            ]
        )

    def stream_text(self, req: provider_pb2.StreamTextRequest):
        for out in self._provider_service.stream_text(
            StreamTextInput(
                user_id=req.user.user_id,
                username=req.user.username,
                provider=req.provider,
                model=req.model,
                api_key=req.api_key,
                base_url=req.base_url,
                prompt=req.prompt,
                system_prompt=req.system_prompt,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                provider_options_json=req.provider_options_json,
            )
        ):
            yield provider_pb2.StreamTextResponse(
                request_id=out.request_id,
                delta=out.delta,
                done=out.done,
                error=out.error,
                sequence=out.sequence,
            )


def _model_parameter_to_proto(parameter) -> provider_pb2.ProviderModelParameter:
    kwargs = {
        "name": parameter.name,
        "display_name": parameter.display_name,
        "type": parameter.type,
        "description": parameter.description,
        "default_value": parameter.default_value,
        "options": parameter.options,
        "required": parameter.required,
    }
    if parameter.min is not None:
        kwargs["min"] = parameter.min
    if parameter.max is not None:
        kwargs["max"] = parameter.max
    return provider_pb2.ProviderModelParameter(**kwargs)
