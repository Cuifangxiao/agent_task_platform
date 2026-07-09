"""Deck RPC handler."""

from api import provider_pb2
from core.deck.contract import DeckNodeModelConfig, DeckSourceFile, GenerateDeckInput, GenerateDeckOutput, ResumeDeckInput
from core.deck.service import DeckService


class DeckHandler:
    def __init__(self, deck_service: DeckService) -> None:
        self._deck_service = deck_service

    def generate_deck(self, req: provider_pb2.GenerateDeckRequest):
        for out in self._deck_service.generate_deck(
            GenerateDeckInput(
                user_id=req.user.user_id,
                username=req.user.username,
                user_description=req.user_description,
                source_file_filename=req.source_file_filename,
                source_file=req.source_file,
                source_files=[
                    DeckSourceFile(
                        filename=item.filename,
                        content=item.content,
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
                    )
                    for item in req.node_model_configs
                ],
            )
        ):
            yield _to_generate_deck_response(out)

    def resume_deck(self, req: provider_pb2.ResumeDeckRequest):
        for out in self._deck_service.resume_deck(
            ResumeDeckInput(
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
                    )
                    for item in req.node_model_configs
                ],
            )
        ):
            yield _to_generate_deck_response(out)


def _to_generate_deck_response(out: GenerateDeckOutput):
    return provider_pb2.GenerateDeckResponse(
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
    )
