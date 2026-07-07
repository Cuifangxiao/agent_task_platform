"""Deck RPC handler."""

from api import provider_pb2
from core.deck.contract import DeckNodeModelConfig, GenerateDeckInput
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
                audience=req.audience,
                goal=req.goal,
                page_count=req.page_count,
                language=req.language,
                style=req.style,
                source_text=req.source_text,
                source_pdf_filename=req.source_pdf_filename,
                source_pdf=req.source_pdf,
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
            yield provider_pb2.GenerateDeckResponse(
                request_id=out.request_id,
                event_type=out.event_type,
                stage=out.stage,
                message=out.message,
                payload_json=out.payload_json,
                artifact_path=out.artifact_path,
                done=out.done,
                error=out.error,
                sequence=out.sequence,
            )
