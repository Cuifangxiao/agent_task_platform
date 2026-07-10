"""Deck RPC handler."""

from api import provider_pb2
from core.deck.contract import DeckNodeModelConfig, DeckSourceFile, GenerateDeckInput, GenerateDeckOutput, ResumeDeckInput
from core.deck import DeckService


class DeckHandler:
    def __init__(self, deck_service: DeckService) -> None:
        self._deck_service = deck_service

    def generate_deck(self, req: provider_pb2.GenerateDeckRequest):
        for out in self._deck_service.generate_deck(
            GenerateDeckInput(
                job_id=getattr(req, "job_id", ""),
                generation_mode=getattr(req, "generation_mode", "step_by_step"),
                user_id=req.user.user_id,
                username=req.user.username,
                user_description=req.user_description,
                source_file_filename=req.source_file_filename,
                source_file=req.source_file,
                source_files=[
                    DeckSourceFile(
                        filename=item.filename,
                        content=item.content,
                        resource_id=getattr(item, "resource_id", ""),
                        local_path=getattr(item, "local_path", ""),
                        sha256=getattr(item, "sha256", ""),
                        size_bytes=getattr(item, "size_bytes", 0),
                    )
                    for item in req.source_files
                ],
                node_model_configs=[
                    DeckNodeModelConfig(
                        node_key=item.node_key,
                        provider=item.provider,
                        model=item.model,
                        api_key=item.api_key,
                        base_url=item.base_url,
                        provider_options_json=item.provider_options_json,
                        temperature=item.temperature,
                        max_tokens=item.max_tokens,
                        supports_image_input=getattr(item, "supports_image_input", False),
                    )
                    for item in req.node_model_configs
                ],
            )
        ):
            yield _to_generate_deck_response(out)

    def resume_deck(self, req: provider_pb2.ResumeDeckRequest):
        for out in self._deck_service.resume_deck(
            ResumeDeckInput(
                job_id=getattr(req, "job_id", ""),
                user_id=req.user.user_id,
                username=req.user.username,
                request_id=req.request_id,
                outline_markdown=req.outline_markdown,
                action=getattr(req, "action", ""),
                payload_json=getattr(req, "payload_json", ""),
                node_model_configs=[
                    DeckNodeModelConfig(
                        node_key=item.node_key,
                        provider=item.provider,
                        model=item.model,
                        api_key=item.api_key,
                        base_url=item.base_url,
                        provider_options_json=item.provider_options_json,
                        temperature=item.temperature,
                        max_tokens=item.max_tokens,
                        supports_image_input=getattr(item, "supports_image_input", False),
                    )
                    for item in req.node_model_configs
                ],
            )
        ):
            yield _to_generate_deck_response(out)


def _to_generate_deck_response(out: GenerateDeckOutput):
    return provider_pb2.GenerateDeckResponse(
        event_version=out.event_version,
        job_id=out.job_id,
        request_id=out.request_id,
        event_type=out.event_type,
        stage=out.stage,
        message=out.message,
        channel=out.channel,
        text_delta=out.text_delta,
        content=out.content,
        outline_markdown=out.outline_markdown,
        payload_json=out.payload_json,
        artifact_path=out.artifact_path,
        done=out.done,
        error=out.error,
        sequence=out.sequence,
        stage_status=out.stage_status,
        progress=provider_pb2.DeckProgress(current=out.progress_current, total=out.progress_total),
        slide=provider_pb2.DeckSlideEvent(index=out.slide_index, version=out.slide_version),
        severity=out.severity,
        action_required=out.action_required,
        retryable=out.retryable,
        error_code=out.error_code,
    )
